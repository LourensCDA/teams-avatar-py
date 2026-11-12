import numpy as np
import pytest

from teams_avatar.loop_player import BLEND_FRAMES, MIN_BLEND_FRAMES, LoopPlayer
from tests.fakes import FakeVideoReader

B = BLEND_FRAMES


def values(player: LoopPlayer, count: int) -> list[int]:
    return [int(player.next_frame()[0, 0, 0]) for _ in range(count)]


def blended(end: int, start: int, i: int) -> int:
    w = (i + 1) / (B + 1)
    return round(end * (1 - w) + start * w)


def assert_close(got: list[int], want: list[int]) -> None:
    assert len(got) == len(want)
    assert all(abs(g - w) <= 1 for g, w in zip(got, want, strict=True)), (got, want)


def test_constants() -> None:
    assert B == 30
    assert MIN_BLEND_FRAMES == 150


def test_first_pass_and_repeats() -> None:
    n = 200
    reader = FakeVideoReader(n)
    player = LoopPlayer(reader, 8, 8)
    plain_first = list(range(0, n - B))
    blend = [blended(n - B + i, i, i) for i in range(B)]
    middle = list(range(B, n - B))
    expected = plain_first + blend + middle + blend + middle + blend
    assert_close(values(player, len(expected)), expected)


def test_visible_loop_length_is_n_minus_b() -> None:
    n = 160
    player = LoopPlayer(FakeVideoReader(n), 8, 8)
    first = values(player, n - B)
    second = values(player, n - B)
    third = values(player, n - B)
    assert second == third
    assert first[B:] == second[B:]
    assert player.loop_length == n - B


def test_repeat_seeks_to_b_and_never_redecodes_start() -> None:
    n = 200
    reader = FakeVideoReader(n)
    player = LoopPlayer(reader, 8, 8)
    values(player, (n - B) * 3)
    assert reader.seeks and set(reader.seeks) == {B}
    assert reader.reads.count(0) == 1
    assert reader.reads.count(B - 1) == 1


def test_only_b_frames_cached() -> None:
    player = LoopPlayer(FakeVideoReader(300), 8, 8)
    values(player, 600)
    assert player.cached_frames == B


@pytest.mark.parametrize("n", [1, 2, 60, 149])
def test_short_recording_repeats_plainly(n: int) -> None:
    player = LoopPlayer(FakeVideoReader(n), 8, 8)
    expected = [i % 256 for i in range(n)] * 3
    assert values(player, 3 * n) == expected
    assert player.cached_frames == 0
    assert player.loop_length == n


def test_exactly_150_frames_blends() -> None:
    n = 150
    player = LoopPlayer(FakeVideoReader(n), 8, 8)
    got = values(player, n)
    assert got[n - B] != n - B  # blended
    assert player.loop_length == n - B


def test_frames_fitted_on_canvas() -> None:
    player = LoopPlayer(FakeVideoReader(200, width=4, height=4), 16, 8)
    frame = player.next_frame()
    assert frame.shape == (8, 16, 3)
    assert frame.dtype == np.uint8


def test_single_pass_mode_stops_after_blend() -> None:
    n = 200
    player = LoopPlayer(FakeVideoReader(n), 8, 8)
    frames = list(player.one_pass())
    assert len(frames) == n
    assert_close(
        [int(f[0, 0, 0]) for f in frames[-B:]], [blended(n - B + i, i, i) for i in range(B)]
    )
