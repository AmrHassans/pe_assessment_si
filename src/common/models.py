from dataclasses import dataclass
from typing import Optional


@dataclass
class Reading:
    sensor: str
    value: float
    ts: str
    status: str  # "OK" or "FAULT"


@dataclass
class Ack:
    cmd: str
    ok: bool
    ts: str
    detail: str = ""


@dataclass
class Command:
    cmd: str
    sensor: Optional[str] = None
    enable: Optional[bool] = None
    hz: Optional[float] = None
    mode: Optional[str] = None
