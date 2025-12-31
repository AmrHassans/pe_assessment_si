import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
import time

from src.common.models import Reading
from src.dashboard.state import SharedState, SENSOR_LIMITS


def test_alarm_low_high_fault():
    state = SharedState()
    sensor = next(iter(SENSOR_LIMITS.keys()))
    low = float(SENSOR_LIMITS[sensor]["low"])
    high = float(SENSOR_LIMITS[sensor]["high"])
    now_mono = time.monotonic()

    # LOW
    r_low = Reading(sensor=sensor, value=low - 1.0, ts="t_low", status="OK")
    alarm_low = state.update(r_low, now_mono)
    assert alarm_low is not None
    assert "LOW" in alarm_low
    assert "value=" in alarm_low
    latest, alarms, active, _ = state.snapshot()
    assert active[sensor] == "LOW"

    # HIGH
    r_high = Reading(sensor=sensor, value=high + 1.0, ts="t_high", status="OK")
    alarm_high = state.update(r_high, now_mono + 0.1)
    assert alarm_high is not None
    assert "HIGH" in alarm_high
    latest, alarms, active, _ = state.snapshot()
    assert active[sensor] == "HIGH"

    # FAULT (must include value)
    r_fault = Reading(sensor=sensor, value=123.456, ts="t_fault", status="FAULT")
    alarm_fault = state.update(r_fault, now_mono + 0.2)
    assert alarm_fault is not None
    assert "FAULT" in alarm_fault
    assert "value=" in alarm_fault  # important requirement
    latest, alarms, active, _ = state.snapshot()
    assert active[sensor] == "FAULT"
