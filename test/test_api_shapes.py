import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
import queue
import time

from fastapi.testclient import TestClient

from src.common.models import Reading
from src.dashboard.api import create_api_app, SENSOR_LIMITS
from src.dashboard.state import SharedState


def test_api_sensors_shape_no_data_then_data():
    state = SharedState()
    cmd_queue = queue.Queue()
    conn_flag = {"connected": False}

    app = create_api_app(state, cmd_queue, conn_flag)
    client = TestClient(app)

    # First call: no data
    r = client.get("/api/sensors")
    assert r.status_code == 200
    data = r.json()

    assert isinstance(data, dict)
    for name in SENSOR_LIMITS.keys():
        assert name in data
        assert "status" in data[name]
        # no data case
        assert data[name]["status"] in ("NO_DATA", "OK", "FAULT")

    # Inject a reading into state and call again
    sensor = next(iter(SENSOR_LIMITS.keys()))
    state.update(Reading(sensor=sensor, value=1.0, ts="t1", status="OK"), time.monotonic())

    r2 = client.get("/api/sensors")
    assert r2.status_code == 200
    data2 = r2.json()

    assert "value" in data2[sensor]
    assert "ts" in data2[sensor]
    assert "status" in data2[sensor]
