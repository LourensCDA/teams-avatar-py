import json
import logging

import numpy as np
import pytest

from teams_avatar.logging_setup import JsonFormatter, session


def make_record(msg: str = "hello", **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("teams_avatar", logging.INFO, __file__, 1, msg, None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_line_is_json_with_fields() -> None:
    line = JsonFormatter().format(make_record())
    data = json.loads(line)
    assert data["level"] == "INFO"
    assert data["msg"] == "hello"
    assert "ts" in data
    assert data["session_id"] is None


def test_session_id_from_context() -> None:
    with session("abc123"):
        data = json.loads(JsonFormatter().format(make_record()))
    assert data["session_id"] == "abc123"
    assert json.loads(JsonFormatter().format(make_record()))["session_id"] is None


def test_extra_fields_included() -> None:
    data = json.loads(JsonFormatter().format(make_record(recording_id="r1", frames=3)))
    assert data["recording_id"] == "r1"
    assert data["frames"] == 3


@pytest.mark.parametrize("value", [np.zeros((4, 4), dtype=np.uint8), b"\xff\xd8", bytearray(3)])
def test_picture_like_extra_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="picture"):
        JsonFormatter().format(make_record(frame=value))
