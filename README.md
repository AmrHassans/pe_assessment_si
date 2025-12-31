
# Real-Time Production Line Dashboard with Remote Access & Notification

## Overview
This project provides a real-time dashboard for monitoring a production line, featuring remote access, live sensor data, alarm notifications, and a REST/WebSocket API. It is designed for both end users (operators, engineers) and developers.

### Key Features:
- Real-time visualization of multiple sensor values (temperature, pressure, vibration, flow, etc.)
- Alarm detection and logging with history
- Remote control and command interface
- REST API and WebSocket endpoints for integration
- Simulator for testing without hardware
---

## Setup Steps

1. **Clone the repository**
	git clone <https://github.com/AmrHassans/pe_assessment_si>
	cd pe_simple_assessment
	```

## Running Instructions

1. **Start the Simulator** (generates sensor data)
	```sh
	python -m src.simulator.main
	```

2. **Start the Dashboard** (GUI + API server)
	```sh
	python -m src.dashboard.main
	```

**Automatic (Windows):**
1. Double-click `run_dashboard_and_simulator.bat` to launch both in separate terminals.
## Protocol Description

The dashboard communicates with the simulator using a TCP socket and newline-delimited JSON (NDJSON) messages.

**Message Encoding:**
- Each message is a JSON object, encoded as UTF-8, and terminated by a newline (`\n`).

**Example:**
```json
{"sensor": "Temperature_1", "value": 28.5, "ts": 1700000000.0, "status": "OK"}
```
## Maintenannce Tab Password
```
admin123
```
**Protocol Functions:**
- See `src/common/protocol.py` for encoding/decoding details.

---
---
## Internal API Documentation

### Summary Table

| Name | Type | Location | Purpose |
|------|------|----------|---------|
| `DashboardWindow` | Class | src/dashboard/ui.py | Main dashboard GUI, manages UI, notifications, and WebSocket streams |
| `_find_schema_path` | Function | src/dashboard/ui.py | Locates the sensor schema JSON file |
| `_load_sensor_limits` | Function | src/dashboard/ui.py | Loads sensor limits from schema |
| `_connect_host` | Function | src/dashboard/ui.py | Determines API host address |
| `SimControl` | Class | src/simulator/control.py | Controls simulator state, commands, and snapshots |
| `main` | Function | src/simulator/main.py | Entry point for simulator process |
| `SharedState` | Class | src/dashboard/state.py | Maintains dashboard state, readings, alarms |
| `Reading`, `Ack`, `Command` | Data Classes | src/common/models.py | Data structures for sensor readings, acknowledgments, and commands |

---

### Detailed Descriptions

#### `DashboardWindow` (src/dashboard/ui.py)
Main window for the dashboard application. Handles UI layout, sensor data display, alarm notifications, system tray, and WebSocket connections for live updates.

**Key Methods:**
- `__init__`: Initializes the dashboard window and UI components.
- `_maybe_notify_alarm`: Handles alarm notifications (desktop and webhook).
- `_webhook_worker`: Sends alarm payloads to a webhook endpoint.
- `_set_status_chip`: Updates status indicator in the UI.
- `_unlock_maintenance`: Unlocks maintenance controls with password.
- `_tail_logs_worker`, `_drain_log_queue`: Handles log file tailing and display.
- `_start_ws_stream`, `_stop_ws_stream`, `_ws_worker_entry`, `_ws_worker`: Manages WebSocket streams for live sensor/alarm data.
- `_on_clear_alarms`: Clears active alarms in the UI.

#### `_find_schema_path` (src/dashboard/ui.py)
Finds the path to the sensor schema JSON file, searching common locations.

#### `_load_sensor_limits` (src/dashboard/ui.py)
Loads sensor limits from the schema file for use in validation and display.

#### `_connect_host` (src/dashboard/ui.py)
Returns the correct API host address for client connections.

#### `SimControl` (src/simulator/control.py)
Manages simulator state, including pause/resume, frequency, forced faults, and snapshot requests. Thread-safe.

**Key Methods:**
- `__init__`: Initializes control state.
- `snapshot`: Returns current simulator state.
- `request_snapshot`, `consume_snapshot_request`: Handles snapshot requests.
- `apply_cmd`: Applies a command to the simulator.

#### `main` (src/simulator/main.py)
Entry point for the simulator process. Starts the simulator and handles shutdown.

#### `SharedState` (src/dashboard/state.py)
Maintains the latest sensor readings, alarm log, active alarms, and time-series buffers for the dashboard. Thread-safe.

**Key Methods:**
- `__init__`: Initializes state and buffers.
- `update`: Updates state with new sensor readings and checks for alarms.

#### `Reading`, `Ack`, `Command` (src/common/models.py)
Data classes representing a sensor reading, an acknowledgment message, and a command structure, respectively. Used for data exchange between components.


## API Documentation

The dashboard exposes a FastAPI server with the following endpoints:

### REST Endpoints
- `GET /api/sensors` — Latest readings for all sensors
- `GET /api/alarms?limit=100` — Alarm log (most recent entries)
- `GET /api/active_alarms` — Currently active alarms
- `POST /api/alarms/clear` — Clear all active alarms
- `POST /api/cmd/pause` — Pause the simulator
- `POST /api/cmd/resume` — Resume the simulator
- `POST /api/cmd/rate` — Set simulator rate (Hz)
- `POST /api/cmd/fault` — Force fault on a sensor
- `POST /api/cmd/out_of_range` — Force out-of-range on a sensor
- `POST /api/cmd/self_test` — Run self-test
- `POST /api/cmd/snapshot` — Take a snapshot
- `GET /api/cmd/status` — TCP connection and command status

### WebSocket Endpoints
- `http://127.0.0.1:8000/api/sensors` — Live sensor data stream
- `http://127.0.0.1:8000/api/alarms` — Live alarm notifications

---

## Sensor Configuration

Sensor limits and simulation profiles are defined in `src/common/sensors.json`.

---

## Note

- Code is organized under `src/`:
  - `dashboard/` — GUI, API, and state management
  - `simulator/` — Sensor data simulation and TCP client
  - `common/` — Shared configs, models, protocol, and sensor schema
- Tests can be added using `pytest`.

---




