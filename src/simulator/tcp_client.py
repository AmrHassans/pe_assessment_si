import json
import random
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

from ..common.config import HOST, PORT
from ..common.protocol import encode_ndjson, extract_messages
from ..common.logging_setup import setup_logger
from .control import SimControl

log = setup_logger("simulator", "simulator.log")

SCHEMA_FILENAME = "sensors.json"


def _find_schema_path() -> Path:
    here = Path(__file__).resolve()
    p1 = here.parents[1] / "common" / SCHEMA_FILENAME  # src/common/sensors.json
    p2 = here.parents[2] / SCHEMA_FILENAME             # project root/sensors.json
    if p1.exists():
        return p1
    if p2.exists():
        return p2
    raise FileNotFoundError(f"Missing {SCHEMA_FILENAME}. Tried: {p1} and {p2}")


def _load_schema() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    schema_path = _find_schema_path()
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    limits = data.get("limits", {})
    sim_profile = data.get("sim_profile", {})
    if not isinstance(limits, dict) or not limits:
        raise ValueError(f"{SCHEMA_FILENAME} missing/invalid 'limits'")
    if not isinstance(sim_profile, dict) or not sim_profile:
        raise ValueError(f"{SCHEMA_FILENAME} missing/invalid 'sim_profile'")
    log.info("Loaded schema from: %s", schema_path)
    return limits, sim_profile


SENSOR_LIMITS, SIM_SENSOR_PROFILE = _load_schema()


def iso_now_ms() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def send_json(sock: socket.socket, send_lock: threading.Lock, obj: dict) -> None:
    with send_lock:
        sock.sendall(encode_ndjson(obj))


def command_receiver(sock: socket.socket, send_lock: threading.Lock, ctrl: SimControl, stop_evt: threading.Event) -> None:
    rx_buf = b""
    sock.settimeout(0.1)

    while not stop_evt.is_set():
        try:
            chunk = sock.recv(4096)
            if chunk == b"":
                log.warning("Server disconnected (recv empty)")
                stop_evt.set()
                break
            rx_buf += chunk
        except socket.timeout:
            continue
        except Exception as e:
            log.warning("command_receiver recv error: %s", e)
            stop_evt.set()
            break

        msgs, rx_buf = extract_messages(rx_buf)
        for obj in msgs:
            if obj.get("type") != "cmd":
                continue

            cmd_name = str(obj.get("cmd", "unknown"))
            try:
                detail = ctrl.apply_cmd(obj)
                ack = {"type": "ack", "cmd": cmd_name, "ok": True, "ts": iso_now_ms(), "detail": detail}
                send_json(sock, send_lock, ack)
                log.info("Processed cmd: %s | detail=%s", obj, detail)
            except Exception as e:
                ack = {"type": "ack", "cmd": cmd_name, "ok": False, "ts": iso_now_ms(), "detail": f"exception: {e}"}
                try:
                    send_json(sock, send_lock, ack)
                except Exception:
                    stop_evt.set()
                    break


def gen_value(name: str, ctrl: SimControl) -> float:
    limits = SENSOR_LIMITS[name]
    profile = SIM_SENSOR_PROFILE.get(name, {"base": 0.0, "noise": 0.0})

    base = float(profile["base"])
    noise = float(profile["noise"])
    v = base + random.uniform(-noise, noise)

    # occasional random OOR
    if random.random() < 0.02:
        if random.random() < 0.5:
            v = float(limits["low"]) - random.uniform(0.5, 3.0)
        else:
            v = float(limits["high"]) + random.uniform(0.5, 3.0)

    paused, hz, forced_fault, forced_oor_sensor, forced_oor_mode = ctrl.snapshot()
    if forced_oor_sensor == name:
        v = float(limits["low"]) - 2.0 if forced_oor_mode == "LOW" else float(limits["high"]) + 2.0

    return float(v)


def _connect_loop(stop_all: threading.Event) -> socket.socket:
    while not stop_all.is_set():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((HOST, PORT))
            log.info("Connected to %s:%s", HOST, PORT)
            return sock
        except ConnectionRefusedError:
            log.warning("Dashboard not ready yet, retrying in 1s...")
            time.sleep(1)
        except Exception as e:
            log.warning("Connect error: %s | retrying in 1s...", e)
            time.sleep(1)
    raise RuntimeError("Stop requested")


def run_simulator_forever(stop_all: threading.Event) -> None:
    log.info("Simulator starting (with reconnect + snapshot)...")

    ctrl = SimControl()

    while not stop_all.is_set():
        sock = None
        stop_evt = threading.Event()
        send_lock = threading.Lock()

        try:
            sock = _connect_loop(stop_all)

            threading.Thread(
                target=command_receiver,
                args=(sock, send_lock, ctrl, stop_evt),
                daemon=True
            ).start()

            with sock:
                while not stop_all.is_set() and not stop_evt.is_set():
                    # snapshot can force one send even if paused
                    snapshot_now = ctrl.consume_snapshot_request()

                    paused, hz, forced_fault, forced_oor_sensor, forced_oor_mode = ctrl.snapshot()
                    if paused and not snapshot_now:
                        time.sleep(0.1)
                        continue

                    # If snapshot_now: send exactly one batch immediately
                    period_s = 1.0 / max(0.5, hz)
                    start = time.time()

                    random_fault = None
                    if forced_fault is None and random.random() < 0.01:
                        random_fault = random.choice(list(SENSOR_LIMITS.keys()))

                    for name in SENSOR_LIMITS.keys():
                        status = "OK"
                        if forced_fault == name or random_fault == name:
                            status = "FAULT"

                        value = gen_value(name, ctrl)
                        msg = {"sensor": name, "value": value, "ts": iso_now_ms(), "status": status}
                        send_json(sock, send_lock, msg)

                    if snapshot_now:
                        # don't rate-limit snapshots
                        continue

                    elapsed = time.time() - start
                    time.sleep(max(0.0, period_s - elapsed))

        except Exception as e:
            log.warning("Simulator connection loop error: %s", e)
        finally:
            try:
                stop_evt.set()
            except Exception:
                pass
            try:
                if sock:
                    sock.close()
            except Exception:
                pass

        if not stop_all.is_set():
            log.warning("Reconnecting in 1s...")
            time.sleep(1)

    log.info("Simulator stopped (stop requested)")
