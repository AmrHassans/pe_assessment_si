import json
from typing import Any, Dict, List, Tuple


def encode_ndjson(obj: Dict[str, Any]) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def try_parse_json_line(line: bytes) -> Dict[str, Any]:
    return json.loads(line.decode("utf-8", errors="replace"))


def extract_messages(rx_buf: bytes) -> Tuple[List[Dict[str, Any]], bytes]:
    """
    Extract newline-delimited JSON objects (NDJSON) from rx_buf.
    Returns (messages, remaining_buffer).
    """
    msgs: List[Dict[str, Any]] = []
    while b"\n" in rx_buf:
        raw, rx_buf = rx_buf.split(b"\n", 1)
        raw = raw.strip()
        if not raw:
            continue
        try:
            msgs.append(try_parse_json_line(raw))
        except Exception:
            continue
    return msgs, rx_buf
