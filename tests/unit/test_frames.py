import numpy as np
import pytest

from teams_avatar.frames import (
    BACKGROUND,
    PLACEHOLDER_GREY,
    cap_size,
    draw_indicator,
    fit_on_canvas,
    indicator_region,
    placeholder,
)


def solid(w: int, h: int, value: int = 200) -> np.ndarray:
    return np.full((h, w, 3), value, dtype=np.uint8)


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((1280, 720), (1280, 720)),
        ((1920, 1080), (1920, 1080)),
        ((3840, 2160), (1920, 1080)),
        ((1080, 1920), (1080, 1920)),  # portrait kept as is
        ((2160, 3840), (1080, 1920)),
        ((640, 480), (640, 480)),  # never scaled up
        ((641, 481), (640, 480)),  # even dimensions
        ((4000, 1000), (1920, 480)),
    ],
)
def test_cap_size(size: tuple[int, int], expected: tuple[int, int]) -> None:
    assert cap_size(*size) == expected


def test_cap_size_keeps_aspect() -> None:
    w, h = cap_size(2880, 2160)  # 4:3, shorter side 2160 > 1080
    assert h <= 1080 and w <= 1920
    assert w / h == pytest.approx(4 / 3, rel=0.01)


def test_fit_same_size_unchanged() -> None:
    f = solid(1280, 720)
    assert np.array_equal(fit_on_canvas(f, 1280, 720), f)


def test_fit_scales_down_larger() -> None:
    out = fit_on_canvas(solid(1920, 1080), 1280, 720)
    assert out.shape == (720, 1280, 3)
    assert (out == 200).all()


def test_fit_centres_smaller_at_own_size() -> None:
    src = solid(640, 480, 222)
    out = fit_on_canvas(src, 1280, 720)
    assert out.shape == (720, 1280, 3)
    assert np.array_equal(out[120:600, 320:960], src)
    mask = np.ones((720, 1280), dtype=bool)
    mask[120:600, 320:960] = False
    assert (out[mask] == np.array(BACKGROUND, dtype=np.uint8)).all()


def test_fit_portrait_pillarboxed() -> None:
    out = fit_on_canvas(solid(1080, 1920, 222), 1280, 720)
    assert out.shape == (720, 1280, 3)
    cols = np.where((out[360] == 222).all(axis=1))[0]
    assert len(cols) == pytest.approx(405, abs=2)
    assert cols[0] == pytest.approx((1280 - 405) // 2, abs=2)
    assert tuple(out[360, 0]) == BACKGROUND


def test_fit_4_3_scaled_to_height() -> None:
    out = fit_on_canvas(solid(1440, 1080, 222), 1280, 720)
    cols = np.where((out[360] == 222).all(axis=1))[0]
    assert len(cols) == pytest.approx(960, abs=2)


@pytest.mark.parametrize(("w", "h"), [(1280, 720), (1920, 1080)])
def test_placeholder(w: int, h: int) -> None:
    p = placeholder(w, h)
    assert p.shape == (h, w, 3) and p.dtype == np.uint8
    grey = np.array(PLACEHOLDER_GREY, dtype=np.uint8)
    is_grey = (p == grey).all(axis=2)
    assert max(PLACEHOLDER_GREY) < 80  # dark
    assert is_grey.mean() > 0.8
    # Symbol and "Camera paused" text sit in the centre; the edges are plain grey.
    assert not is_grey[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4].all()
    assert is_grey[: h // 8].all() and is_grey[-h // 8 :].all()
    assert np.array_equal(p, placeholder(w, h))


def test_placeholder_cache_cannot_be_corrupted() -> None:
    p = placeholder(1280, 720)
    with pytest.raises(ValueError):
        p[0, 0] = 255
    with pytest.raises(ValueError):
        p.flags.writeable = True
    assert tuple(placeholder(1280, 720)[0, 0]) == PLACEHOLDER_GREY


@pytest.mark.parametrize(("w", "h"), [(1280, 720), (1920, 1080)])
def test_indicator_only_changes_lower_left_banner(w: int, h: int) -> None:
    src = solid(w, h, 90)
    out = draw_indicator(src)
    assert (src == 90).all()  # input not modified
    x0, y0, x1, y1 = indicator_region(w, h)
    assert x0 < w // 4 and y1 > h * 0.8
    changed = (out != src).any(axis=2)
    assert changed[y0:y1, x0:x1].any()
    changed[y0:y1, x0:x1] = False
    assert not changed.any()


def test_indicator_scales_with_height() -> None:
    _, y0a, _, y1a = indicator_region(1280, 720)
    _, y0b, _, y1b = indicator_region(1920, 1080)
    assert (y1b - y0b) / (y1a - y0a) == pytest.approx(1.5, rel=0.1)
