"""Pure parts of the OpenCV preview window: overlay drawing and key mapping (no window opened)."""

import numpy as np
import pytest

from teams_avatar.adapters.preview_window import draw_rec_overlay, key_action


def test_overlay_marks_rec_and_time_without_touching_input() -> None:
    frame = np.full((720, 1280, 3), 100, dtype=np.uint8)
    out = draw_rec_overlay(frame, 75.4)
    assert (frame == 100).all()
    changed = (out != frame).any(axis=2)
    assert changed[: 720 // 5, : 1280 // 3].any()  # top-left corner
    assert not changed[720 // 2 :, :].any()  # nothing over the lower half
    reds = out[(out[:, :, 2] > 180) & (out[:, :, 1] < 80) & (out[:, :, 0] < 80)]
    assert len(reds) > 50  # a red REC dot


@pytest.mark.parametrize(
    ("code", "action"),
    [(32, "save"), (13, "save"), (27, "cancel"), (-1, None), (ord("x"), None)],
)
def test_key_action(code: int, action: str | None) -> None:
    assert key_action(code) == action
