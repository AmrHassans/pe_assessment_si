import threading

from ..common.logging_setup import setup_logger
from .tcp_client import run_simulator_forever

log = setup_logger("simulator.main", "simulator.log")


def main():
    stop_all = threading.Event()
    try:
        run_simulator_forever(stop_all)
    except KeyboardInterrupt:
        log.warning("KeyboardInterrupt received, stopping simulator...")
        stop_all.set()


if __name__ == "__main__":
    main()
