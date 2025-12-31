import json
import os
import time
import queue
import threading
import asyncio
import urllib.request
import urllib.error
import inspect
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QListWidget,
    QSystemTrayIcon,
    QStyle,
)

import pyqtgraph as pg
import websockets  # pip install websockets

from ..common.config import API_HOST, API_PORT, PLOT_WINDOW_SEC
from .state import SharedState

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


def _load_sensor_limits() -> Dict[str, Dict[str, Any]]:
    schema_path = _find_schema_path()
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    limits = data.get("limits", {})
    if not isinstance(limits, dict) or not limits:
        raise ValueError(f"{SCHEMA_FILENAME} missing/invalid 'limits'")
    return limits


def _connect_host() -> str:
    # 0.0.0.0 is bind-only; clients must use a real address
    if API_HOST in ("0.0.0.0", "", None):
        return "127.0.0.1"
    return str(API_HOST)


def _enable_plot_perf(pw: pg.PlotWidget) -> None:
    """
    Make pyqtgraph plots faster but also compatible with multiple pyqtgraph versions.
    Avoid calling methods that may not exist (like setAutoDownsample).
    """
    # Clip
    try:
        pi = pw.getPlotItem()
    except Exception:
        pi = None

    try:
        if pi is not None:
            pi.setClipToView(True)
        else:
            pw.setClipToView(True)
    except Exception:
        pass

    # Downsampling (version differences)
    if pi is not None:
        try:
            sig = inspect.signature(pi.setDownsampling)
            params = sig.parameters
            if "auto" in params and "method" in params:
                pi.setDownsampling(auto=True, method="peak")
                return
            if "auto" in params and "mode" in params:
                pi.setDownsampling(auto=True, mode="peak")
                return
        except Exception:
            pass

        # positional fallback
        try:
            pi.setDownsampling(1, True, "peak")
            return
        except Exception:
            pass


SENSOR_LIMITS = _load_sensor_limits()


class DashboardWindow(QMainWindow):
    """
    Modern/Responsive improvements:
      - Dashboard uses QSplitter (resizable panels)
      - Separate timers: data drain (fast), table refresh (medium), plots refresh (medium)
      - Change-detection: table cells only updated when changed
      - Batch updates: disable updates while writing many cells
      - pyqtgraph performance flags (version-safe)
      - Compact active alarms summary
    Also keeps:
      - Maintenance console Bonus A (logs thread + WS monitor + access control + commands)
      - Bonus B notifications (system tray + webhook)
    """

    def __init__(self, state: SharedState, rx_queue, cmd_queue, conn_flag, stop_event) -> None:
        super().__init__()
        self.setWindowTitle("SI-WARE Sensor Dashboard")
        # Set window icon (logo)
        from PyQt6.QtGui import QIcon
        from pathlib import Path
        icon_path = str(Path(__file__).parent / "assets" / "siware_logo.png")
        if Path(icon_path).exists():
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1400, 850)

        self.state = state
        self.rx_queue = rx_queue
        self.cmd_queue = cmd_queue
        self.conn_flag = conn_flag
        self.stop_event = stop_event

        # ---------------- Bonus B: Notification settings ----------------
        self._notify_desktop = os.environ.get("NOTIFY_DESKTOP", "1").strip() != "0"
        self._webhook_url = os.environ.get("ALARM_WEBHOOK_URL", "").strip()

        # throttle duplicate notifications
        self._last_notified: Dict[Tuple[str, str], float] = {}
        self._notify_min_interval_sec = 1.0

        # Desktop notification (system tray)
        self.tray: Optional[QSystemTrayIcon] = None
        if self._notify_desktop:
            self.tray = QSystemTrayIcon(self)
            self.tray.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
            self.tray.setToolTip("Sensor Dashboard Notifications")
            self.tray.show()

        # Webhook poster thread (if URL provided)
        self._webhook_q: "queue.Queue[dict]" = queue.Queue()
        self._webhook_stop = threading.Event()
        self._webhook_thread: Optional[threading.Thread] = None
        if self._webhook_url:
            self._webhook_thread = threading.Thread(target=self._webhook_worker, daemon=True)
            self._webhook_thread.start()

        # Smooth UI caches
        self._latest_cache: Dict[str, Any] = {}
        self._active_cache: Dict[str, str] = {}
        self._buffers_cache: Dict[str, list] = {}
        self._pending_alarm_events: list[Tuple[Any, str]] = []

        # Change-detection for table updates
        self._table_last: Dict[str, Tuple[str, str, str]] = {}  # name -> (val_text, ts_text, status_text)
        self._active_last_keys: Tuple[Tuple[str, str], ...] = tuple()

        # ---------------- Root ----------------
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)
        root.setLayout(root_layout)

        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)

        # ===============================================================
        # TAB 1: Dashboard
        # ===============================================================
        dashboard_tab = QWidget()
        self.tabs.addTab(dashboard_tab, "Dashboard")
        dash_root = QVBoxLayout()
        dash_root.setContentsMargins(6, 6, 6, 6)
        dash_root.setSpacing(8)
        dashboard_tab.setLayout(dash_root)

        # Top "overview bar"
        overview = QGroupBox("Overview")
        ovl = QHBoxLayout()
        ovl.setContentsMargins(10, 10, 10, 10)
        ovl.setSpacing(10)
        overview.setLayout(ovl)

        self.status_chip = QLabel("WAITING FOR DATA")
        self.status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_chip.setMinimumWidth(220)
        self._set_status_chip("WAITING")

        self.conn_label = QLabel("TCP: DISCONNECTED")
        self.conn_label.setStyleSheet("color: #999;")

        self.ack_label = QLabel("ACK: -")
        self.ack_label.setStyleSheet("color: #999;")

        host_for_display = _connect_host()
        self.api_label = QLabel(
            f"API: http://{host_for_display}:{API_PORT}  |  WS: ws://{host_for_display}:{API_PORT}/ws/*"
        )
        self.api_label.setStyleSheet("color: #999;")

        ovl.addWidget(QLabel("System Status:"))
        ovl.addWidget(self.status_chip)
        ovl.addStretch(1)
        ovl.addWidget(self.conn_label)
        ovl.addSpacing(12)
        ovl.addWidget(self.ack_label)
        ovl.addSpacing(12)
        ovl.addWidget(self.api_label)

        dash_root.addWidget(overview)

        # Splitter for resizable layout
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        dash_root.addWidget(splitter, 1)

        # Left panel widget
        left_w = QWidget()
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        left_w.setLayout(left)

        # Sensor table group
        sensors_g = QGroupBox("Sensors")
        sensors_l = QVBoxLayout()
        sensors_l.setContentsMargins(10, 10, 10, 10)
        sensors_l.setSpacing(8)
        sensors_g.setLayout(sensors_l)

        self.table = QTableWidget(len(SENSOR_LIMITS), 4)
        self.table.setHorizontalHeaderLabels(["Sensor", "Latest Value", "Timestamp", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)

        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, header.ResizeMode.ResizeToContents)

        self.sensor_rows = {name: i for i, name in enumerate(SENSOR_LIMITS.keys())}
        for name, i in self.sensor_rows.items():
            self.table.setItem(i, 0, QTableWidgetItem(name))
            self.table.setItem(i, 1, QTableWidgetItem("-"))
            self.table.setItem(i, 2, QTableWidgetItem("-"))
            self.table.setItem(i, 3, QTableWidgetItem("NO DATA"))
            self._table_last[name] = ("-", "-", "NO DATA")

        sensors_l.addWidget(self.table)
        left.addWidget(sensors_g, 2)

        # Alarms group (compact)
        alarms_g = QGroupBox("Alarms")
        alarms_l = QVBoxLayout()
        alarms_l.setContentsMargins(10, 10, 10, 10)
        alarms_l.setSpacing(6)
        alarms_g.setLayout(alarms_l)

        active_row = QHBoxLayout()
        active_row.setSpacing(8)

        lbl_active = QLabel("Active (summary)")
        lbl_active.setStyleSheet("font-weight: 700;")
        active_row.addWidget(lbl_active)

        self.active_list = QListWidget()
        self.active_list.setFixedHeight(70)
        active_row.addWidget(self.active_list, 1)

        alarms_l.addLayout(active_row)

        self.alarm_box = QPlainTextEdit()
        self.alarm_box.setReadOnly(True)
        self.alarm_box.setPlaceholderText("Alarm log (time | sensor | value | type)...")
        self.alarm_box.setMaximumBlockCount(1000)
        alarms_l.addWidget(self.alarm_box, 1)

        left.addWidget(alarms_g, 2)

        # Right panel widget (plots)
        right_w = QWidget()
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right_w.setLayout(right)

        plots_g = QGroupBox("Trends")
        plots_l = QGridLayout()
        plots_l.setContentsMargins(10, 10, 10, 10)
        plots_l.setHorizontalSpacing(10)
        plots_l.setVerticalSpacing(10)
        plots_g.setLayout(plots_l)

        pg.setConfigOptions(antialias=True)
        self.plot_curves: Dict[str, pg.PlotDataItem] = {}

        for idx, name in enumerate(SENSOR_LIMITS.keys()):
            rr = idx // 2
            cc = idx % 2

            pw = pg.PlotWidget()
            _enable_plot_perf(pw)

            unit = SENSOR_LIMITS[name]["unit"]
            pw.setTitle(f"{name} ({unit})  •  last {int(PLOT_WINDOW_SEC)}s")
            pw.showGrid(x=True, y=True, alpha=0.25)

            curve = pw.plot([], [])
            # curve-level downsampling (some versions support this)
            try:
                curve_sig = inspect.signature(curve.setDownsampling)
                cparams = curve_sig.parameters
                if "auto" in cparams and "method" in cparams:
                    curve.setDownsampling(auto=True, method="peak")
                elif "auto" in cparams and "mode" in cparams:
                    curve.setDownsampling(auto=True, mode="peak")
                else:
                    curve.setDownsampling(1, True, "peak")
            except Exception:
                try:
                    curve.setDownsampling(1, True, "peak")
                except Exception:
                    pass

            self.plot_curves[name] = curve

            pw.addItem(pg.InfiniteLine(pos=float(SENSOR_LIMITS[name]["low"]), angle=0, movable=False))
            pw.addItem(pg.InfiniteLine(pos=float(SENSOR_LIMITS[name]["high"]), angle=0, movable=False))

            plots_l.addWidget(pw, rr, cc)

        right.addWidget(plots_g, 1)

        splitter.addWidget(left_w)
        splitter.addWidget(right_w)
        splitter.setSizes([520, 860])

        # ===============================================================
        # TAB 2: Maintenance
        # ===============================================================
        maintenance_tab = QWidget()
        self.tabs.addTab(maintenance_tab, "Maintenance")
        maint_layout = QVBoxLayout()
        maint_layout.setContentsMargins(10, 10, 10, 10)
        maint_layout.setSpacing(10)
        maintenance_tab.setLayout(maint_layout)

        maint_layout.addWidget(QLabel("Maintenance Console (restricted)"))

        pwd_row = QHBoxLayout()
        pwd_row.setSpacing(8)

        self.maint_pwd = QLineEdit()
        self.maint_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.maint_pwd.setPlaceholderText("Enter maintenance password (press Enter)")

        self.maint_unlock_btn = QPushButton("Unlock")
        self.maint_unlock_btn.setDefault(True)
        self.maint_unlock_btn.setAutoDefault(True)

        self.maint_status = QLabel("LOCKED")
        self.maint_status.setStyleSheet("font-weight: 700; color: red;")

        pwd_row.addWidget(self.maint_pwd, 3)
        pwd_row.addWidget(self.maint_unlock_btn, 1)
        pwd_row.addWidget(self.maint_status, 1)
        maint_layout.addLayout(pwd_row)

        self.maint_controls = QGroupBox("Maintenance Commands")
        self.maint_controls.setEnabled(False)
        ctl_layout = QHBoxLayout()
        ctl_layout.setContentsMargins(10, 10, 10, 10)
        ctl_layout.setSpacing(8)
        self.maint_controls.setLayout(ctl_layout)

        self.btn_m_restart = QPushButton("Restart Simulator")
        self.btn_m_refresh = QPushButton("Force Refresh")
        self.btn_m_clear = QPushButton("Clear Alarms")
        self.btn_m_selftest = QPushButton("Self-Test")
        self.btn_m_snapshot = QPushButton("Snapshot")

        ctl_layout.addWidget(self.btn_m_restart)
        ctl_layout.addWidget(self.btn_m_refresh)
        ctl_layout.addWidget(self.btn_m_clear)
        ctl_layout.addWidget(self.btn_m_selftest)
        ctl_layout.addWidget(self.btn_m_snapshot)

        maint_layout.addWidget(self.maint_controls)

        # WebSocket monitor (Bonus A.4)
        self.ws_group = QGroupBox("WebSocket Event Streaming (Live)")
        self.ws_group.setEnabled(False)
        ws_layout = QVBoxLayout()
        ws_layout.setContentsMargins(10, 10, 10, 10)
        ws_layout.setSpacing(8)
        self.ws_group.setLayout(ws_layout)

        ws_host = _connect_host()
        ws_info = QLabel(
            "WebSocket feeds:\n"
            f"  ws://{ws_host}:{API_PORT}/ws/sensors\n"
            f"  ws://{ws_host}:{API_PORT}/ws/alarms"
        )
        ws_info.setStyleSheet("color: #999;")
        ws_layout.addWidget(ws_info)

        ws_btn_row = QHBoxLayout()
        ws_btn_row.setSpacing(8)
        self.btn_ws_start = QPushButton("Start WS Stream")
        self.btn_ws_stop = QPushButton("Stop WS Stream")
        self.btn_ws_stop.setEnabled(False)
        ws_btn_row.addWidget(self.btn_ws_start)
        ws_btn_row.addWidget(self.btn_ws_stop)
        ws_layout.addLayout(ws_btn_row)

        self.ws_view = QPlainTextEdit()
        self.ws_view.setReadOnly(True)
        self.ws_view.setPlaceholderText("WebSocket stream output...")
        self.ws_view.setMaximumBlockCount(2000)
        ws_layout.addWidget(self.ws_view)

        maint_layout.addWidget(self.ws_group)

        # Live log viewer (Bonus A.1)
        maint_layout.addWidget(QLabel("Live Logs (collected by background thread)"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Logs will appear here...")
        self.log_view.setMaximumBlockCount(3000)
        maint_layout.addWidget(self.log_view, 1)

        # Hook unlock + commands
        self.maint_unlock_btn.clicked.connect(self._unlock_maintenance)
        self.maint_pwd.returnPressed.connect(self._unlock_maintenance)

        self.btn_m_restart.clicked.connect(lambda: self.cmd_queue.put({"type": "cmd", "cmd": "restart_simulator"}))
        self.btn_m_refresh.clicked.connect(lambda: self.cmd_queue.put({"type": "cmd", "cmd": "force_refresh"}))
        self.btn_m_clear.clicked.connect(self._on_clear_alarms)
        self.btn_m_selftest.clicked.connect(lambda: self.cmd_queue.put({"type": "cmd", "cmd": "self_test"}))
        self.btn_m_snapshot.clicked.connect(lambda: self.cmd_queue.put({"type": "cmd", "cmd": "snapshot"}))

        # --- Log tailer thread ---
        project_root = Path(__file__).resolve().parents[2]
        self._log_paths = [
            project_root / "logs" / "dashboard.log",
            project_root / "logs" / "simulator.log",
        ]
        self._log_q: "queue.Queue[str]" = queue.Queue()
        self._log_tail_stop = threading.Event()
        self._log_tail_thread = threading.Thread(target=self._tail_logs_worker, daemon=True)
        self._log_tail_thread.start()

        self.log_timer = QTimer(self)
        self.log_timer.setInterval(200)
        self.log_timer.timeout.connect(self._drain_log_queue)
        self.log_timer.start()

        # --- WebSocket worker thread ---
        self._ws_q: "queue.Queue[str]" = queue.Queue()
        self._ws_stop = threading.Event()
        self._ws_thread: Optional[threading.Thread] = None

        self.btn_ws_start.clicked.connect(self._start_ws_stream)
        self.btn_ws_stop.clicked.connect(self._stop_ws_stream)

        self.ws_timer = QTimer(self)
        self.ws_timer.setInterval(200)
        self.ws_timer.timeout.connect(self._drain_ws_queue)
        self.ws_timer.start()

        # ===============================================================
        # Timers for smooth UI
        # ===============================================================
        self.data_timer = QTimer(self)
        self.data_timer.setInterval(80)  # drain queue often
        self.data_timer.timeout.connect(self._drain_data)
        self.data_timer.start()

        self.table_timer = QTimer(self)
        self.table_timer.setInterval(250)
        self.table_timer.timeout.connect(self._refresh_table_and_status)
        self.table_timer.start()

        self.plot_timer = QTimer(self)
        self.plot_timer.setInterval(160)
        self.plot_timer.timeout.connect(self._refresh_plots)
        self.plot_timer.start()

        # Styling
        self.setStyleSheet("""
        QGroupBox { border: 1px solid #333; border-radius: 10px; margin-top: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
        QTableWidget, QListWidget, QPlainTextEdit { border: 1px solid #333; border-radius: 10px; }
        QPushButton { border-radius: 10px; padding: 6px 10px; }
        QLineEdit { border: 1px solid #333; border-radius: 10px; padding: 6px 10px; }
        """)

    # ================= Bonus B helpers =================
    def _maybe_notify_alarm(self, payload: dict, signature: str) -> None:
        sensor = str(payload.get("sensor", "UNKNOWN"))
        key = (sensor, signature)

        now = time.monotonic()
        last = self._last_notified.get(key, 0.0)
        if (now - last) < self._notify_min_interval_sec:
            return
        self._last_notified[key] = now

        if self.tray is not None:
            title = f"ALARM: {payload.get('sensor', 'UNKNOWN')}"
            msg = f"{payload.get('alarm_type','')}\nValue: {payload.get('value','-')} @ {payload.get('ts','-')}"
            self.tray.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Warning, 3500)

        if self._webhook_url:
            try:
                self._webhook_q.put_nowait(payload)
            except Exception:
                pass

    def _webhook_worker(self) -> None:
        while not self._webhook_stop.is_set():
            try:
                payload = self._webhook_q.get(timeout=0.5)
            except Exception:
                continue

            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    self._webhook_url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    _ = resp.read()
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
                pass
            except Exception:
                pass

    # ================= UI helpers =================
    def _set_status_chip(self, level: str) -> None:
        level = level.upper()
        if level == "OK":
            bg, fg, text = "#123a1b", "#a8f0b2", "OK"
        elif level == "WARN":
            bg, fg, text = "#3a2a10", "#ffd28a", "WARNING"
        elif level == "ERROR":
            bg, fg, text = "#3a1010", "#ff9a9a", "ERROR"
        else:
            bg, fg, text = "#222", "#ddd", "WAITING"

        self.status_chip.setText(text)
        self.status_chip.setStyleSheet(
            f"QLabel {{ font-weight: 800; padding: 6px 12px; border-radius: 12px; "
            f"background: {bg}; color: {fg}; }}"
        )

    # ================= Maintenance =================
    def _unlock_maintenance(self) -> None:
        expected = os.environ.get("MAINT_PASSWORD", "admin123")
        if self.maint_pwd.text() == expected:
            self.maint_controls.setEnabled(True)
            self.ws_group.setEnabled(True)
            self.maint_status.setText("UNLOCKED")
            self.maint_status.setStyleSheet("font-weight: 800; color: #3ddc84;")
        else:
            QMessageBox.warning(self, "Wrong Password", "Maintenance password is incorrect.")
            self.maint_controls.setEnabled(False)
            self.ws_group.setEnabled(False)
            self.maint_status.setText("LOCKED")
            self.maint_status.setStyleSheet("font-weight: 800; color: #ff6b6b;")

    def _tail_logs_worker(self) -> None:
        positions: Dict[Path, int] = {p: 0 for p in self._log_paths}
        while not self._log_tail_stop.is_set():
            for p in self._log_paths:
                try:
                    if not p.exists():
                        continue
                    last = positions.get(p, 0)
                    with p.open("r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last)
                        chunk = f.read()
                        positions[p] = f.tell()
                    if chunk:
                        self._log_q.put(f"\n--- {p.name} ---\n{chunk.rstrip()}")
                except Exception:
                    pass
            time.sleep(0.3)

    def _drain_log_queue(self) -> None:
        while True:
            try:
                msg = self._log_q.get_nowait()
            except Exception:
                break
            self.log_view.appendPlainText(msg)

    def _start_ws_stream(self) -> None:
        if self._ws_thread and self._ws_thread.is_alive():
            return
        self._ws_stop.clear()
        self.btn_ws_start.setEnabled(False)
        self.btn_ws_stop.setEnabled(True)
        self.ws_view.appendPlainText("Starting WebSocket streams...")
        self._ws_thread = threading.Thread(target=self._ws_worker_entry, daemon=True)
        self._ws_thread.start()

    def _stop_ws_stream(self) -> None:
        self._ws_stop.set()
        self.btn_ws_start.setEnabled(True)
        self.btn_ws_stop.setEnabled(False)
        self.ws_view.appendPlainText("Stopping WebSocket streams...")

    def _ws_worker_entry(self) -> None:
        try:
            asyncio.run(self._ws_worker())
        except Exception as e:
            self._ws_q.put(f"[WS] worker crashed: {e}")

    async def _ws_worker(self) -> None:
        host = _connect_host()
        sensors_url = f"ws://{host}:{API_PORT}/ws/sensors"
        alarms_url = f"ws://{host}:{API_PORT}/ws/alarms"

        async def consume(url: str, tag: str):
            while not self._ws_stop.is_set():
                try:
                    async with websockets.connect(url) as ws:
                        self._ws_q.put(f"[WS] connected: {tag} -> {url}")
                        while not self._ws_stop.is_set():
                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                                self._ws_q.put(f"[{tag}] {msg}")
                            except asyncio.TimeoutError:
                                continue
                except Exception as e:
                    if self._ws_stop.is_set():
                        break
                    self._ws_q.put(f"[WS] {tag} reconnecting after error: {e}")
                    await asyncio.sleep(1)

        await asyncio.gather(
            consume(sensors_url, "SENSORS"),
            consume(alarms_url, "ALARMS"),
        )

    def _drain_ws_queue(self) -> None:
        while True:
            try:
                line = self._ws_q.get_nowait()
            except Exception:
                break
            self.ws_view.appendPlainText(line)

    # ================= Dashboard logic =================
    def _on_clear_alarms(self) -> None:
        self.state.clear_alarms()
        self._pending_alarm_events.clear()
        self.active_list.clear()
        self.alarm_box.clear()
        self._active_last_keys = tuple()

    def _apply_status_colors(self, row: int, status: str) -> None:
        """
        Color rules:
          - FAULT   -> red
          - WARNING -> orange
          - OK      -> green
        """
        st_item = self.table.item(row, 3)
        if not st_item:
            return
        if status == "FAULT":
            st_item.setForeground(QColor("red"))
        elif status == "WARNING":
            st_item.setForeground(QColor("orange"))
        else:
            st_item.setForeground(QColor("green"))

    def _drain_data(self) -> None:
        self.conn_label.setText(
            f"TCP: {'CONNECTED' if self.conn_flag.get('connected') else 'DISCONNECTED'}"
        )

        ack_ok = self.conn_flag.get("last_ack_ok")
        ok_txt = "-" if ack_ok is None else ("OK" if ack_ok else "ERROR")
        detail = self.conn_flag.get("last_ack_detail", "")
        self.ack_label.setText(
            f"ACK: {self.conn_flag.get('last_ack','-')} ({ok_txt})"
            + (f" | {detail}" if detail else "")
        )

        now_mono = time.monotonic()
        while True:
            try:
                r = self.rx_queue.get_nowait()
            except Exception:
                break

            alarm_msg = self.state.update(r, now_mono)
            if alarm_msg:
                self._pending_alarm_events.append((r, alarm_msg))

        latest, _alarms, active, buffers = self.state.snapshot()
        self._latest_cache = latest
        self._active_cache = active
        self._buffers_cache = buffers

        if self._pending_alarm_events:
            for r, alarm_msg in self._pending_alarm_events:
                sensor = getattr(r, "sensor", "UNKNOWN")
                alarm_type = active.get(sensor, "")
                up = alarm_msg.upper()
                if not alarm_type:
                    if "LOW" in up:
                        alarm_type = "LOW"
                    elif "HIGH" in up:
                        alarm_type = "HIGH"
                    elif "FAULT" in up:
                        alarm_type = "FAULT"
                    elif "CLEARED" in up:
                        alarm_type = "CLEARED"
                    else:
                        alarm_type = "ALARM"

                payload = {
                    "event": "alarm",
                    "sensor": sensor,
                    "value": getattr(r, "value", None),
                    "ts": getattr(r, "ts", ""),
                    "status": getattr(r, "status", ""),
                    "alarm_type": alarm_type,
                    "message": alarm_msg,
                }
                signature = f"{alarm_type}:{alarm_msg[:40]}"
                self._maybe_notify_alarm(payload, signature)

    def _refresh_table_and_status(self) -> None:
        latest = self._latest_cache or {}
        active = self._active_cache or {}

        # System chip:
        # - ERROR if any sensor reading status is FAULT OR any active alarm type is FAULT
        # - WARNING if any active alarm exists (LOW/HIGH/...)
        # - OK if we have readings and no active alarms
        any_fault = any(
            (latest.get(name) is not None and getattr(latest[name], "status", "") == "FAULT")
            for name in self.sensor_rows.keys()
        ) or any(v == "FAULT" for v in active.values())

        any_alarm = bool(active)

        if any_fault:
            self._set_status_chip("ERROR")
        elif any_alarm:
            self._set_status_chip("WARN")
        elif latest:
            self._set_status_chip("OK")
        else:
            self._set_status_chip("WAITING")

        # Per-sensor status:
        # - FAULT if reading status is FAULT
        # - WARNING if sensor has active alarm (LOW/HIGH) and not FAULT
        # - OK otherwise
        self.table.setUpdatesEnabled(False)
        try:
            for name, row in self.sensor_rows.items():
                r = latest.get(name)
                if r is None:
                    continue

                unit = SENSOR_LIMITS[name]["unit"]
                val_text = f"{r.value:.3f} {unit}"
                ts_text = r.ts

                base_status = getattr(r, "status", "OK")
                if base_status == "FAULT":
                    st_text = "FAULT"
                elif name in active:
                    st_text = "WARNING"
                else:
                    st_text = "OK"

                last_val, last_ts, last_st = self._table_last.get(name, ("", "", ""))
                if val_text != last_val:
                    self.table.item(row, 1).setText(val_text)
                if ts_text != last_ts:
                    self.table.item(row, 2).setText(ts_text)
                if st_text != last_st:
                    self.table.item(row, 3).setText(st_text)

                self._table_last[name] = (val_text, ts_text, st_text)
                self._apply_status_colors(row, st_text)
        finally:
            self.table.setUpdatesEnabled(True)

        if self._pending_alarm_events:
            for _, alarm_msg in self._pending_alarm_events:
                self.alarm_box.appendPlainText(alarm_msg)
            self._pending_alarm_events.clear()

        items = tuple(sorted(active.items()))
        if items != self._active_last_keys:
            self._active_last_keys = items
            self.active_list.clear()
            max_show = 3
            for sensor, alarm_type in items[:max_show]:
                self.active_list.addItem(f"{sensor}: {alarm_type}")
            if len(items) > max_show:
                self.active_list.addItem(f"... +{len(items) - max_show} more")

    def _refresh_plots(self) -> None:
        buffers = self._buffers_cache or {}
        for name, points in buffers.items():
            if not points:
                continue

            # Limit points for extra smoothness (avoid huge draw)
            max_points = 600
            if len(points) > max_points:
                step = max(1, len(points) // max_points)
                points = points[::step]

            t_vals = [p[0] for p in points]
            v_vals = [p[1] for p in points]
            t0 = t_vals[-1]
            x = [t - t0 for t in t_vals]
            self.plot_curves[name].setData(x, v_vals)

    def closeEvent(self, event) -> None:
        self.stop_event.set()
        self._log_tail_stop.set()
        self._ws_stop.set()
        self._webhook_stop.set()
        super().closeEvent(event)
