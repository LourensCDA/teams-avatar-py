from pathlib import Path

import numpy as np

from teams_avatar.analysis import GHOST_THRESHOLD, compute_warnings, ghost_score, warnings_for
from tests.conftest import ClipFactory


def test_threshold_constant() -> None:
    assert GHOST_THRESHOLD == 40  # tuned in T071 against three real recordings


def grey(level: int, w: int = 640, h: int = 360) -> np.ndarray:
    return np.full((h, w, 3), level, dtype=np.uint8)


def test_identical_frames_no_warning() -> None:
    assert ghost_score(grey(100), grey(100)) == 0
    assert warnings_for(10.0, grey(100), grey(100)) == []


def test_too_short() -> None:
    assert warnings_for(4.99, grey(100), grey(100)) == ["too_short"]
    assert warnings_for(5.0, grey(100), grey(100)) == []


def test_ghosting_above_threshold() -> None:
    assert warnings_for(10.0, grey(100), grey(100 + GHOST_THRESHOLD + 1)) == ["loop_ghosting"]
    assert warnings_for(10.0, grey(100), grey(100 + GHOST_THRESHOLD)) == []


def test_both_warnings() -> None:
    assert warnings_for(2.0, grey(0), grey(200)) == ["too_short", "loop_ghosting"]


def test_compares_at_160x90_greyscale() -> None:
    # A single bright pixel disappears at 160x90; colour differences with equal grey do not count.
    a = grey(100)
    b = a.copy()
    b[0, 0] = 255
    assert ghost_score(a, b) < 1


def test_compute_warnings_from_file(tmp_path: Path, make_clip: ClipFactory) -> None:
    still = make_clip(tmp_path / "still.mp4", 180, 320, 180, distinct=False)
    assert compute_warnings(still) == []
    short = make_clip(tmp_path / "short.mp4", 60, 320, 180, distinct=False)
    assert compute_warnings(short) == ["too_short"]
