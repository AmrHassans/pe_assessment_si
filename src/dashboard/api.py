''' 
API server for the Sensor Dashboard application.
Uses FastAPI to provide REST endpoints and WebSocket streams.
Ends up running with Uvicorn.
'''
# ---------------- Imports ----------------#
import asyncio
import json
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

from ..common.config import API_HOST, API_PORT
from .state import SharedState

# ---------------- Constants & Helpers ----------------#
SCHEMA_FILENAME = "sensors.json"

# ---------------- Schema Loading ----------------#
def _find_schema_path() -> Path:
    here = Path(__file__).resolve()
    p1 = here.parents[1] / "common" / SCHEMA_FILENAME  # src/common/sensors.json
    p2 = here.parents[2] / SCHEMA_FILENAME             # project root/sensors.json
    if p1.exists():
        return p1
    if p2.exists():
        return p2
    raise FileNotFoundError(f"Could not find {SCHEMA_FILENAME}. Tried: {p1} and {p2}")

# ---------------- Sensor Limits Loading ----------------#
def _load_sensor_limits() -> Dict[str, Dict[str, Any]]:
    '''
    Loads sensor limits from sensors.json schema file.
    Returns a dict of sensor name -> limits dict.
    '''
    schema_path = _find_schema_path() # load schema file
    data = json.loads(schema_path.read_text(encoding="utf-8")) # parse JSON
    limits = data.get("limits", {}) # get limits section
    if not isinstance(limits, dict) or not limits:
        raise ValueError(f"{SCHEMA_FILENAME} missing/invalid 'limits'")
    return limits

# ---------------- Main API App Creation ----------------#
SENSOR_LIMITS = _load_sensor_limits()


#---------------- API App ----------------#
def create_api_app(state: SharedState, cmd_queue, conn_flag) -> FastAPI:
    '''
    Creates and configures the FastAPI app with REST endpoints and WebSocket routes.
    Args:
        state: SharedState instance for accessing sensor data and alarms.
        cmd_queue: Queue for sending commands to the TCP server.
        conn_flag: Dict for TCP connection status and last command/ack info.
    Returns:
        Configured FastAPI app instance.
    '''
    app = FastAPI(title="Sensor Dashboard API")

    # ---------------- REST ----------------
    @app.get("/api/sensors")
    def get_sensors():# Returns the latest readings for all sensors.
        latest, _, active, _ = state.snapshot()
        out = {}
        for name in SENSOR_LIMITS.keys():
            r = latest.get(name)
            out[name] = {"status": "NO_DATA"} if r is None else {
                "value": r.value,
                "ts": r.ts,
                "status": r.status,
                "active_alarm": active.get(name)
            }
        return out
    
# ---------------- Alarms ----------------

    @app.get("/api/alarms") # Returns the alarm log, limited to the most recent 'limit' entries.
    def get_alarms(limit: int = 100):
        _, alarms, _, _ = state.snapshot()
        limit = max(1, min(limit, 500))
        return {"count": len(alarms), "alarms": alarms[-limit:]}

    @app.get("/api/active_alarms") # Returns the currently active alarms for all sensors.
    def get_active_alarms():
        _, _, active, _ = state.snapshot()
        return active

    @app.post("/api/alarms/clear") # Clears all active alarms.
    def clear_alarms():
        state.clear_alarms()
        return {"status": "ok"}
    
# ---------------- Commands ----------------
    @app.post("/api/cmd/pause") # Queues a 'pause' command to the TCP server.
    def cmd_pause():
        cmd_queue.put({"type": "cmd", "cmd": "pause"})
        return {"status": "queued"}

    @app.post("/api/cmd/resume")# Queues a 'resume' command to the TCP server.
    def cmd_resume():
        cmd_queue.put({"type": "cmd", "cmd": "resume"})
        return {"status": "queued"}

    @app.post("/api/cmd/rate") # Queues a 'set_rate' command to the TCP server with the specified rate in Hz.
    def cmd_rate(hz: float = 5.0):
        hz = max(0.5, min(hz, 50.0))
        cmd_queue.put({"type": "cmd", "cmd": "set_rate", "hz": hz})
        return {"status": "queued", "hz": hz}

    @app.post("/api/cmd/fault") # Queues a 'force_fault' command to the TCP server for the specified sensor.
    def cmd_fault(sensor: str, enable: bool = True):
        if sensor not in SENSOR_LIMITS:
            return {"status": "error", "message": "unknown sensor"}
        cmd_queue.put({"type": "cmd", "cmd": "force_fault", "sensor": sensor, "enable": enable})
        return {"status": "queued"}

    @app.post("/api/cmd/out_of_range") # Queues a 'force_oor' command to the TCP server for the specified sensor and mode.
    def cmd_oor(sensor: str, mode: str = "HIGH", enable: bool = True):
        if sensor not in SENSOR_LIMITS:
            return {"status": "error", "message": "unknown sensor"}
        mode = mode.upper()
        if mode not in ("LOW", "HIGH"):
            return {"status": "error", "message": "mode must be LOW or HIGH"}
        cmd_queue.put({"type": "cmd", "cmd": "force_oor", "sensor": sensor, "mode": mode, "enable": enable})
        return {"status": "queued"}

    @app.post("/api/cmd/self_test")# Queues a 'self_test' command to the TCP server.
    def cmd_self_test():
        cmd_queue.put({"type": "cmd", "cmd": "self_test"})
        return {"status": "queued"}

    @app.post("/api/cmd/snapshot") # Queues a 'snapshot' command to the TCP server.
    def cmd_snapshot():
        cmd_queue.put({"type": "cmd", "cmd": "snapshot"})
        return {"status": "queued"}

    @app.get("/api/cmd/status") # Returns the status of the TCP connection and last command/acknowledgment.
    def cmd_status():
        return {
            "tcp_connected": bool(conn_flag.get("connected", False)),
            "last_cmd": conn_flag.get("last_cmd", "-"),
            "last_cmd_ts": conn_flag.get("last_cmd_ts", "-"),
            "last_ack": conn_flag.get("last_ack", "-"),
            "last_ack_ok": conn_flag.get("last_ack_ok"),
            "last_ack_ts": conn_flag.get("last_ack_ts", "-"),
            "last_ack_detail": conn_flag.get("last_ack_detail", ""),
        }

    # ---------------- WebSockets ----------------
    @app.websocket("/ws/sensors")
    async def ws_sensors(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                latest, _, active, _ = state.snapshot()
                payload = {}
                for name in SENSOR_LIMITS.keys():
                    r = latest.get(name)
                    payload[name] = None if r is None else {
                        "value": r.value,
                        "ts": r.ts,
                        "status": r.status,
                        "active_alarm": active.get(name),
                    }
                await ws.send_json({"type": "sensors", "data": payload})
                await asyncio.sleep(0.5)  # 2 Hz
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/alarms")
    async def ws_alarms(ws: WebSocket):
        await ws.accept()
        last_len = 0
        try:
            while True:
                _, alarms, _, _ = state.snapshot()
                if len(alarms) > last_len:
                    new_items = alarms[last_len:]
                    last_len = len(alarms)
                    for line in new_items:
                        await ws.send_json({"type": "alarm", "line": line})
                await asyncio.sleep(0.2)
        except WebSocketDisconnect:
            return

    return app

# ---------------- API Server Runner ----------------#
def run_api(state: SharedState, cmd_queue, conn_flag) -> None:
    app = create_api_app(state, cmd_queue, conn_flag)
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="warning")
