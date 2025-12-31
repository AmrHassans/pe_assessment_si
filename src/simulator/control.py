from __future__ import annotations

import threading
from typing import Optional, Tuple


class SimControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused = False
        self._hz = 5.0
        self._forced_fault: Optional[str] = None
        self._forced_oor_sensor: Optional[str] = None
        self._forced_oor_mode: str = "HIGH"  # LOW/HIGH
        self._snapshot_requested = False

    def snapshot(self) -> Tuple[bool, float, Optional[str], Optional[str], str]:
        with self._lock:
            return (
                self._paused,
                self._hz,
                self._forced_fault,
                self._forced_oor_sensor,
                self._forced_oor_mode,
            )

    def request_snapshot(self) -> None:
        with self._lock:
            self._snapshot_requested = True

    def consume_snapshot_request(self) -> bool:
        with self._lock:
            was = self._snapshot_requested
            self._snapshot_requested = False
            return was

    def apply_cmd(self, cmd_obj: dict) -> str:
        cmd = str(cmd_obj.get("cmd", "")).strip()

        with self._lock:
            if cmd == "pause":
                self._paused = True
                return "paused"

            if cmd == "resume":
                self._paused = False
                return "resumed"

            if cmd == "set_rate":
                hz = float(cmd_obj.get("hz", 5.0))
                self._hz = max(0.5, min(hz, 50.0))
                return f"rate_set:{self._hz}"

            if cmd == "force_fault":
                sensor = cmd_obj.get("sensor")
                enable = bool(cmd_obj.get("enable", True))
                self._forced_fault = str(sensor) if enable and sensor else None
                return f"force_fault:{self._forced_fault}"

            if cmd == "force_oor":
                sensor = cmd_obj.get("sensor")
                enable = bool(cmd_obj.get("enable", True))
                mode = str(cmd_obj.get("mode", "HIGH")).upper()
                if mode not in ("LOW", "HIGH"):
                    mode = "HIGH"
                self._forced_oor_mode = mode
                self._forced_oor_sensor = str(sensor) if enable and sensor else None
                return f"force_oor:{self._forced_oor_sensor}:{self._forced_oor_mode}"

            #  New maintenance commands
            if cmd == "self_test":
                # keep it simple: just ACK success
                return "self_test_ok"

            if cmd == "snapshot":
                # send one immediate full batch even if paused
                self._snapshot_requested = True
                return "snapshot_requested"

        return f"unknown_cmd:{cmd}"
