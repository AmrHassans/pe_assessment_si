'''
tests 
'''

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
import json

from src.common.protocol import encode_ndjson, extract_messages


def test_extract_messages_multiple_and_partial():
    msg1 = {"sensor": "S1", "value": 1.23, "ts": "t1", "status": "OK"}
    msg2 = {"type": "ack", "cmd": "pause", "ok": True, "ts": "t2", "detail": "paused"}

    data = encode_ndjson(msg1) + encode_ndjson(msg2)

    msgs, rem = extract_messages(data)
    assert rem == b""
    assert len(msgs) == 2
    assert msgs[0]["sensor"] == "S1"
    assert msgs[1]["type"] == "ack"

    # now simulate partial chunk (cut mid-line)
    part = data[:10]
    msgs2, rem2 = extract_messages(part)
    assert msgs2 == []
    assert rem2 == part  # still waiting for newline / full json

    # append the rest, should decode both
    msgs3, rem3 = extract_messages(rem2 + data[10:])
    assert rem3 == b""
    assert len(msgs3) == 2
