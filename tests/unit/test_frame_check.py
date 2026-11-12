from pathlib import Path

import pytest

from tools.frame_check import FrameReport, analyse, parse_framemd5

HEADER = """#format: frame checksums
#version: 2
#hash: MD5
#tb 0: 1/30
#media_type 0: video
#codec_id 0: rawvideo
#dimensions 0: 1280x720
#sar 0: 1/1
#stream#, dts,        pts, duration,     size, hash
"""


def line(pts: int, digest: str) -> str:
    return f"0,{pts:10d},{pts:10d},        1,  1382400, {digest}\n"


def write(tmp_path: Path, entries: list[tuple[int, str]]) -> Path:
    path = tmp_path / "cap.framemd5"
    path.write_text(HEADER + "".join(line(p, d) for p, d in entries), encoding="utf-8")
    return path


def test_parse(tmp_path: Path) -> None:
    frames = parse_framemd5(write(tmp_path, [(0, "a"), (1, "b")]))
    assert frames == [(0, "a"), (1, "b")]


def test_clean_capture(tmp_path: Path) -> None:
    report = analyse(write(tmp_path, [(i, f"h{i}") for i in range(300)]), seconds=10)
    assert report == FrameReport(expected=300, received=300, repeated=0, dropped=0)
    assert report.bad_percent == 0


def test_repeats_and_drops(tmp_path: Path) -> None:
    entries = [(i, f"h{i}") for i in range(300)]
    entries[10] = (10, "h9")  # repeated picture
    entries[11] = (11, "h9")  # and again
    del entries[200:203]  # three pictures missing from the timeline
    report = analyse(write(tmp_path, entries), seconds=10)
    assert report.repeated == 2
    assert report.dropped == 3
    assert report.received == 297
    assert report.bad_percent == pytest.approx(5 / 300 * 100)


def test_ignores_single_duplicate_at_start(tmp_path: Path) -> None:
    entries = [(0, "x"), (1, "x")] + [(i, f"h{i}") for i in range(2, 300)]
    report = analyse(write(tmp_path, entries), seconds=10, ignore_start_duplicate=True)
    assert report.repeated == 0


def test_main_prints_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.frame_check import main

    assert main([str(write(tmp_path, [(i, f"h{i}") for i in range(30)])), "--seconds", "1"]) == 0
    out = capsys.readouterr().out
    assert "received 30/30" in out and "0.00%" in out
