'''
Shared state for dashboard application.
Maintains latest readings, alarm log, active alarms, and time-series buffers.
'''
#--------------------------------import--------------------------------#
import json
import threading
from collections import deque # deque for efficient popping from left
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from ..common.config import PLOT_WINDOW_SEC # seconds of data to keep in buffer
from ..common.models import Reading


#--------------------------------constants--------------------------------#
SCHEMA_FILENAME = "sensors.json"


#-------------------------------helper functions--------------------------------#
#
def _find_schema_path() -> Path:
    '''Locate the sensors.json schema file.
    Arguments:
         None
    Returns:
        Path to the sensors.json file.
    '''
    here = Path(__file__).resolve()
    p1 = here.parents[1] / "common" / SCHEMA_FILENAME
    p2 = here.parents[2] / SCHEMA_FILENAME
    if p1.exists():
        return p1
    if p2.exists():
        return p2
    raise FileNotFoundError(f"Missing {SCHEMA_FILENAME}. Tried: {p1} and {p2}")


def _load_sensor_limits() -> Dict[str, Dict[str, Any]]:
    ''' Load sensor limits from sensors.json schema file.
    Arguments:
        None
    Returns:
        Dict of sensor name -> limits dict.
    '''
    schema_path = _find_schema_path()
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    limits = data.get("limits", {})
    if not isinstance(limits, dict) or not limits:
        raise ValueError(f"{SCHEMA_FILENAME} missing/invalid 'limits'")
    return limits

#-------------------------------Shared State Class--------------------------------#
SENSOR_LIMITS = _load_sensor_limits()

#
class SharedState:
    '''
    Shared state for dashboard application.
    Maintains latest readings, alarm log, active alarms, and time-series buffers.
    '''

    def __init__(self) -> None:
        ''' Initializes the SharedState instance.
        Arguments:
            None    
        Returns:
            None
        '''
        self.latest: Dict[str, Reading] = {}
        self.alarm_log: List[str] = []
        self.active_alarms: Dict[str, str] = {}
        self.buffers: Dict[str, Deque[Tuple[float, float]]] = {name: deque() for name in SENSOR_LIMITS.keys()} # time-series buffers for plotting 
        self.lock = threading.Lock() # to ensure thread-safe access

    def update(self, r: Reading, now_monotonic: float) -> Optional[str]:
        '''
        Updates the shared state with a new reading.
        Args:
            r: Reading instance containing sensor data.
            now_monotonic: Current monotonic time for buffer timestamping.
        Returns:
            Optional alarm message if an alarm was triggered, else None.
        '''
        limits = SENSOR_LIMITS.get(r.sensor)
        if not limits:
            return None

        alarm_type: Optional[str] = None
        alarm_msg: Optional[str] = None

        if r.status == "FAULT":
            alarm_type = "FAULT"
            alarm_msg = f"[{r.ts}] {r.sensor}: FAULT (value={r.value})"
        else:
            low = float(limits["low"])
            high = float(limits["high"])
            if r.value < low:
                alarm_type = "LOW"
                alarm_msg = f"[{r.ts}] {r.sensor}: LOW (value={r.value} < {low})"
            elif r.value > high:
                alarm_type = "HIGH"
                alarm_msg = f"[{r.ts}] {r.sensor}: HIGH (value={r.value} > {high})"

        with self.lock: # ensure thread-safe updates
            self.latest[r.sensor] = r

            buf = self.buffers[r.sensor]
            buf.append((now_monotonic, r.value))
            cutoff = now_monotonic - PLOT_WINDOW_SEC
            while buf and buf[0][0] < cutoff:
                buf.popleft()

            if alarm_type:
                self.active_alarms[r.sensor] = alarm_type
            else:
                self.active_alarms.pop(r.sensor, None)

            if alarm_msg:
                self.alarm_log.append(alarm_msg)
                if len(self.alarm_log) > 500:
                    self.alarm_log = self.alarm_log[-500:]

        return alarm_msg

    def clear_alarms(self) -> None:
        '''
        Clears all active alarms and the alarm log.
        Arguments:
            None
        Returns:
            None
        '''
        with self.lock:
            self.alarm_log.clear()
            self.active_alarms.clear()

    def snapshot(self):
        '''
        Takes a snapshot of the current shared state.
        Arguments:
            None
        Returns:
            Tuple containing copies of latest readings, alarm log, active alarms, and buffers.
        '''
        with self.lock:
            return dict(self.latest), list(self.alarm_log), dict(self.active_alarms), {k: list(v) for k, v in self.buffers.items()}
