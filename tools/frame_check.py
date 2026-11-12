"""Count dropped and repeated pictures in an ffmpeg ``framemd5`` capture (research R10).

Test tool, not shipped. Capture the camera from the consumer side, for example::

    ffmpeg -f dshow -video_size 1280x720 -framerate 30 -i video="OBS Virtual Camera" \
        -t 600 -f framemd5 capture.framemd5
    uv run python -m tools.frame_check capture.framemd5 --seconds 600

The test clip must have no two identical consecutive frames, so any identical consecutive hash
is a repeated picture; gaps in the timeline are dropped pictures.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

FPS = 30


@dataclass(frozen=True)
class FrameReport:
    """Result of one capture analysis."""

    expected: int
    received: int
    repeated: int
    dropped: int

    @property
    def bad_percent(self) -> float:
        """Return dropped plus repeated pictures as a percentage of the expected count."""
        return 100 * (self.repeated + self.dropped) / self.expected if self.expected else 0.0


def parse_framemd5(path: Path) -> list[tuple[int, str]]:
    """Return ``(pts, hash)`` for every video frame line, skipping ``#`` comments."""
    frames: list[tuple[int, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        fields = [f.strip() for f in raw.split(",")]
        if fields[0] != "0":
            continue
        frames.append((int(fields[2]), fields[5]))
    return frames


def analyse(
    path: Path, seconds: float, fps: int = FPS, ignore_start_duplicate: bool = False
) -> FrameReport:
    """Compare a capture with ``seconds`` × ``fps`` expected pictures.

    Args:
        path: framemd5 file whose time base is one frame (``#tb 0: 1/30``).
        seconds: Capture length.
        fps: Expected rate.
        ignore_start_duplicate: Don't count one duplicate between the first two frames, which
            DirectShow capture start-up produces regardless of the sender.

    Returns:
        Expected, received, repeated and dropped counts.
    """
    frames = parse_framemd5(path)
    expected = round(seconds * fps)
    repeated = sum(1 for a, b in zip(frames, frames[1:], strict=False) if a[1] == b[1])
    if ignore_start_duplicate and len(frames) > 1 and frames[0][1] == frames[1][1]:
        repeated -= 1
    gaps = sum(max(0, b[0] - a[0] - 1) for a, b in zip(frames, frames[1:], strict=False))
    dropped = max(gaps, expected - len(frames))
    return FrameReport(expected=expected, received=len(frames), repeated=repeated, dropped=dropped)


def main(argv: list[str] | None = None) -> int:
    """Print a one-line summary; exit 1 if more than 1% of pictures were bad."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--keep-start-duplicate", action="store_true")
    args = parser.parse_args(argv)
    r = analyse(args.path, args.seconds, ignore_start_duplicate=not args.keep_start_duplicate)
    print(
        f"received {r.received}/{r.expected}, repeated {r.repeated}, dropped {r.dropped}"
        f" -> {r.bad_percent:.2f}% bad (target <= 1%)"
    )
    return 0 if r.bad_percent <= 1 else 1


if __name__ == "__main__":
    sys.exit(main())
