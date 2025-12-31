'''
Dashboard main entry point file - starts UI, API, TCP server threads 
and handles fatal startup errors.

'''
#--------------------------------imports--------------------------------#
import sys
import threading
import queue
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox

from ..common.logging_setup import setup_logger

#--------------------------------logging--------------------------------#
log = setup_logger("dashboard", "dashboard.log")


#-------------------------------functions-------------------------------#
'''Show a fatal error message box and exit.

Args:
    title: Title of the message box.
    message: Message to show.
    '''
def _show_fatal_error(title: str, message: str) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.critical(None, title, message)
    sys.exit(1)

#-------------------------------main-----------------------------------#
def main():
    try:
        log.info("Dashboard starting...")

# import here to avoid circular import
        from .state import SharedState  # Shared state for dashboard
        from .tcp_server import tcp_server_worker # TCP server thread
        from .api import run_api # API server thread
        from .ui import DashboardWindow # Dashboard UI

# Setup shared state, queues, flags, and start threads
        state = SharedState()
        rx_queue: "queue.Queue" = queue.Queue()
        cmd_queue: "queue.Queue" = queue.Queue()
        stop_event = threading.Event()

# Setup connection flag dictionary
        conn_flag = {
            "connected": False,
            "last_cmd": "-",
            "last_cmd_ts": "-",
            "last_ack": "-",
            "last_ack_ok": None,
            "last_ack_ts": "-",
            "last_ack_detail": "",
        }

# Start TCP server thread
        threading.Thread(
            target=tcp_server_worker,
            args=(rx_queue, cmd_queue, conn_flag, stop_event),
            daemon=True
        ).start()
        log.info("TCP server thread started")
# Start API server thread
        threading.Thread(
            target=run_api,
            args=(state, cmd_queue, conn_flag),
            daemon=True
        ).start()
        log.info("API server thread started")

# Start Dashboard UI
        app = QApplication(sys.argv)
        w = DashboardWindow(state, rx_queue, cmd_queue, conn_flag, stop_event)
        w.resize(1550, 900)
        w.show()

        log.info("Dashboard UI shown")
        sys.exit(app.exec())

    except Exception as e:
        err_details = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        log.exception("Dashboard failed to start: %s", e)

        msg = (
            "Dashboard failed to start.\n\n"
            "Most common reasons:\n"
            "- sensors.json is missing\n"
            "- sensors.json is invalid JSON\n"
            "- 'limits' section is missing/empty\n\n"
            f"Error:\n{e}\n\n"
            f"Details:\n{err_details}"
        )
        _show_fatal_error("Startup Error", msg)


if __name__ == "__main__":
    main()
