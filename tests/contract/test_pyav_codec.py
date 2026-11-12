"""Contract tests for the PyAV codec adapter (no hardware needed)."""

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from teams_avatar.adapters.pyav_codec import PyAVReader, PyAVWriter, VideoInfo, probe
from teams_avatar.errors import UnreadableVideoError
from tests.clips import counter_frame, read_counter

W, H, N = 1280, 720, 75


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("codec") / "clip.mp4"
    with PyAVWriter(path, W, H, fps=30) as writer:
        for i in range(N):
            writer.write(counter_frame(i, W, H))
    return path


def test_round_trip_size_count_fps(clip: Path) -> None:
    with PyAVReader(clip) as reader:
        assert (reader.width, reader.height, reader.fps) == (W, H, 30)
        assert reader.frame_count == N
        frames = []
        while (frame := reader.read()) is not None:
            frames.append(frame)
    assert len(frames) == N
    assert frames[0].shape == (H, W, 3)
    assert frames[0].dtype == np.uint8
    assert [read_counter(f) for f in frames] == list(range(N))


def test_encoder_settings(clip: Path) -> None:
    with av.open(str(clip)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.name == "h264"
        assert stream.codec_context.pix_fmt == "yuv420p"
        keyframes = [i for i, p in enumerate(container.demux(stream)) if p.is_keyframe]
    assert keyframes[:3] == [0, 30, 60]


def test_seek_lands_exactly(clip: Path) -> None:
    with PyAVReader(clip) as reader:
        reader.seek(30)
        frame = reader.read()
        assert frame is not None
        assert read_counter(frame) == 30
        assert (nxt := reader.read()) is not None and read_counter(nxt) == 31
        reader.seek(0)
        assert (first := reader.read()) is not None and read_counter(first) == 0


def test_probe(clip: Path) -> None:
    info = probe(clip)
    assert isinstance(info, VideoInfo)
    assert (info.width, info.height, info.frame_count) == (W, H, N)
    assert info.duration_s == pytest.approx(N / 30, abs=0.05)
    assert info.fps == pytest.approx(30)


def test_text_file_renamed_mp4_raises(tmp_path: Path) -> None:
    fake = tmp_path / "notes.mp4"
    fake.write_text("this is not a video", encoding="utf-8")
    with pytest.raises(UnreadableVideoError):
        probe(fake)
    with pytest.raises(UnreadableVideoError):
        PyAVReader(fake)


def test_truncated_file_raises(clip: Path, tmp_path: Path) -> None:
    data = clip.read_bytes()
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(data[: len(data) // 2])
    with pytest.raises(UnreadableVideoError):
        probe(broken)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(UnreadableVideoError):
        probe(tmp_path / "nope.mp4")


FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg to build test files")


def run_ffmpeg(*args: str) -> None:
    assert FFMPEG is not None
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args], check=True)


def make_rotated(src: Path, dst: Path, degrees: int) -> Path:
    """Copy ``src`` and add a display-matrix rotation without touching the pixels."""
    run_ffmpeg("-display_rotation", str(degrees), "-i", str(src), "-c", "copy", str(dst))
    return dst


def make_vfr(tmp_path: Path) -> Path:
    """Build a 2 s file: 30 frames at 30 fps followed by 10 frames at 10 fps (real step VFR)."""
    out = tmp_path / "vfr.mp4"
    with av.open(str(out), "w") as container:
        stream = container.add_stream("libx264", rate=30)
        stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
        stream.time_base = Fraction(1, 600)
        stream.options = {"bf": "0", "g": "30", "sc_threshold": "0"}
        pts = 0
        for i in range(40):
            frame = av.VideoFrame.from_ndarray(
                counter_frame(i if i < 30 else i - 30, 320, 180), "bgr24"
            )
            frame.pts = pts
            frame.time_base = Fraction(1, 600)
            pts += 20 if i < 30 else 60
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return out


@needs_ffmpeg
@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_rotation_metadata_applied(clip: Path, tmp_path: Path, degrees: int) -> None:
    rotated = make_rotated(clip, tmp_path / "rot.mp4", degrees)
    expected = (H, W) if degrees in (90, 270) else (W, H)
    info = probe(rotated)
    assert (info.width, info.height) == expected
    with PyAVReader(rotated) as reader:
        assert (reader.width, reader.height) == expected
        reader.seek(10)
        later = reader.read()
    assert later is not None
    assert later.shape[:2] == (expected[1], expected[0])
    # Rotating back by the same quarter turns must give the upright source picture.
    assert read_counter(np.rot90(later, -(degrees // 90))) == 10


@needs_ffmpeg
def test_frame_time_follows_real_timestamps_for_vfr(tmp_path: Path) -> None:
    with PyAVReader(make_vfr(tmp_path)) as reader:
        assert reader.frame_count == 40
        assert reader.frame_time(0) == 0
        assert reader.frame_time(29) == pytest.approx(29 / 30, abs=1e-3)
        assert reader.frame_time(30) == pytest.approx(1.0, abs=1e-3)
        assert reader.frame_time(31) == pytest.approx(1.1, abs=1e-3)
        with pytest.raises(ValueError):
            reader.frame_time(40)


def test_frame_time_cfr(clip: Path) -> None:
    with PyAVReader(clip) as reader:
        assert reader.frame_time(30) == pytest.approx(1.0, abs=1e-3)


def test_unreadable_error_has_reason(tmp_path: Path) -> None:
    with pytest.raises(UnreadableVideoError) as info:
        PyAVReader(tmp_path / "missing.mp4")
    assert info.value.reason == "the file does not exist"
    assert info.value.reason in str(info.value)
