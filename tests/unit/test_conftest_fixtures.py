import socket
from pathlib import Path

import av
import pytest

from tests.clips import read_counter
from tests.conftest import ClipFactory


def test_socket_is_blocked_in_unit_tests() -> None:
    with pytest.raises(RuntimeError, match="network"):
        socket.socket()


def test_data_home_sets_env(data_home: Path) -> None:
    import os

    assert os.environ["TEAMS_AVATAR_HOME"] == str(data_home)


def test_make_clip_frames_carry_their_index(tmp_path: Path, make_clip: ClipFactory) -> None:
    path = make_clip(tmp_path / "c.mp4", 40, 320, 180)
    with av.open(str(path)) as container:
        indexes = [read_counter(f.to_ndarray(format="bgr24")) for f in container.decode(video=0)]
    assert indexes == list(range(40))
