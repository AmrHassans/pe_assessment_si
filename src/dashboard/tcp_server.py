''' TCP server worker for dashboard application. 
    Handles TCP connections, sending commands and receiving sensor readings.
'''
#---------------- Imports ----------------#
import socket
import queue
from datetime import datetime
from typing import Dict

from ..common.config import HOST, PORT
from ..common.models import Reading
from ..common.protocol import extract_messages, encode_ndjson
from ..common.logging_setup import setup_logger

#---------------- Logger ----------------#
log = setup_logger("dashboard.tcp", "dashboard.log")

#---------------- TCP Server Worker ----------------#

def tcp_server_worker(
    out_queue: "queue.Queue[Reading]",
    cmd_queue: "queue.Queue[dict]",
    conn_flag: Dict,
    stop_event
) -> None:
    '''
    TCP server worker that listens for incoming connections from the sensor device,
    sends commands from cmd_queue, and puts received sensor readings into out_queue.
    Args:
        out_queue: Queue to put received Reading objects.
        cmd_queue: Queue to get commands to send to the sensor device.
        conn_flag: Dict to update TCP connection status and last command/ack info.
        stop_event: Event to signal the worker to stop.
    Returns:
        None
    '''
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    srv.settimeout(1.0)

    log.info("TCP listening on %s:%s", HOST, PORT)

    conn = None
    rx_buf = b""

    def mark_disconnected():
        conn_flag["connected"] = False
        conn_flag["last_cmd"] = "-"
        conn_flag["last_cmd_ts"] = "-"
        conn_flag["last_ack"] = "-"
        conn_flag["last_ack_ok"] = None
        conn_flag["last_ack_ts"] = "-"
        conn_flag["last_ack_detail"] = ""

    mark_disconnected()

    try:
        while not stop_event.is_set():
            if conn is None:
                try:
                    conn, addr = srv.accept()
                    conn.settimeout(0.1)
                    rx_buf = b""
                    conn_flag["connected"] = True
                    log.info("Client connected: %s", addr)
                except socket.timeout:
                    continue

            # Send pending commands
            try:
                while True:
                    cmd = cmd_queue.get_nowait()
                    conn.sendall(encode_ndjson(cmd))
                    conn_flag["last_cmd"] = cmd.get("cmd", "unknown")
                    conn_flag["last_cmd_ts"] = datetime.now().isoformat(timespec="seconds")
                    log.info("Sent cmd: %s", cmd)
            except queue.Empty:
                pass
            except Exception as e:
                log.warning("Send cmd error: %s", e)

            # Receive
            try:
                chunk = conn.recv(4096)
                if chunk == b"":
                    log.warning("Client disconnected")
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                    mark_disconnected()
                    continue
                rx_buf += chunk
            except socket.timeout:
                continue
            except Exception as e:
                log.warning("recv error: %s", e)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                mark_disconnected()
                continue

            msgs, rx_buf = extract_messages(rx_buf)
            for obj in msgs:
                if obj.get("type") == "ack":
                    conn_flag["last_ack"] = str(obj.get("cmd", "-"))
                    conn_flag["last_ack_ok"] = bool(obj.get("ok", True))
                    conn_flag["last_ack_ts"] = str(obj.get("ts", "-"))
                    conn_flag["last_ack_detail"] = str(obj.get("detail", ""))
                    log.info("Received ACK: %s", obj)
                    continue

                if "sensor" in obj and "value" in obj and "ts" in obj:
                    try:
                        r = Reading(
                            sensor=str(obj["sensor"]),
                            value=float(obj["value"]),
                            ts=str(obj["ts"]),
                            status=str(obj.get("status", "OK")),
                        )
                        out_queue.put(r)
                    except Exception:
                        pass

    finally:
        mark_disconnected()
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        try:
            srv.close()
        except Exception:
            pass
        log.info("TCP server stopped")
