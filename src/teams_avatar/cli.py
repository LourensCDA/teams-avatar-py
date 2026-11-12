"""Command-line front end (contracts/cli.md): parses arguments and formats facade results."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from teams_avatar.app import App, PlaybackUI
from teams_avatar.errors import (
    ConfirmationRequiredError,
    NoticeNotAcknowledgedError,
    TeamsAvatarError,
)
from teams_avatar.models import QUALITIES, PresentationStatus
from teams_avatar.recording import RecordingUI

PROG = "teams-avatar"


@dataclass(frozen=True)
class Output:
    """What a command produced: text for people and data for ``--json``."""

    text: str
    data: Any


Handler = Callable[[App, argparse.Namespace], Output]


@dataclass(frozen=True)
class Command:
    """A sub-command: its parser setup and the handler that calls the facade."""

    name: str
    help: str | None
    configure: Callable[[argparse.ArgumentParser], None]
    run: Handler
    needs_notice: bool = True


COMMANDS: dict[str, Command] = {}


@dataclass
class _Streams:
    """The streams of the running command, for handlers that must prompt (`delete`)."""

    stdin: TextIO
    stdout: TextIO


_io = _Streams(sys.stdin, sys.stdout)


def command(
    name: str, help: str | None, needs_notice: bool = True
) -> Callable[[Callable[[argparse.ArgumentParser], None]], Callable[[Handler], Handler]]:
    """Register a sub-command; decorate the configure function, then the handler.

    Usage: ``@command("history", "...")(configure)`` returns a decorator for the handler.
    """

    def with_configure(
        configure: Callable[[argparse.ArgumentParser], None],
    ) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            COMMANDS[name] = Command(name, help, configure, handler, needs_notice)
            return handler

        return register

    return with_configure


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser from the command registry."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument(
        "--accept-notice", action="store_true", help="accept the first-use notice without a prompt"
    )
    parser = argparse.ArgumentParser(
        prog=PROG, description="Present a recording of yourself as your camera."
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    for cmd in COMMANDS.values():
        kwargs: dict[str, Any] = {"parents": [common]}
        if cmd.help is not None:
            kwargs["help"] = cmd.help
        sp = sub.add_parser(cmd.name, **kwargs)
        cmd.configure(sp)
    return parser


def _first_use_gate(app: App, args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> None:
    """Show the disclosure notice once and require consent (constitution Principle I).

    Raises:
        NoticeNotAcknowledgedError: If the notice is not accepted.
    """
    notice = app.first_use_notice()
    if notice is None:
        return
    if args.accept_notice:
        app.acknowledge_first_use()
        return
    if args.json or not stdin.isatty():
        raise NoticeNotAcknowledgedError(notice)
    stdout.write(notice + "\n\nType y to continue: ")
    stdout.flush()
    if stdin.readline().strip().lower() != "y":
        raise NoticeNotAcknowledgedError(notice)
    app.acknowledge_first_use()


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    app_factory: Callable[[], App] = App,
) -> int:
    """Run one command and return its exit code (contracts/cli.md)."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    _io.stdin, _io.stdout = stdin, stdout
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 0 for --help and 2 for usage errors, matching contracts/cli.md.
        return 0 if exc.code in (0, None) else 2
    cmd = COMMANDS[args.command]
    as_json = bool(getattr(args, "json", False))
    try:
        app = app_factory()
        if cmd.needs_notice:
            _first_use_gate(app, args, stdin, stdout)
        result = cmd.run(app, args)
    except TeamsAvatarError as exc:
        if isinstance(exc, NoticeNotAcknowledgedError) and not as_json and not stdin.isatty():
            stdout.write(exc.notice + "\n\n")
        stderr.write(f"error: {exc.message}\n")
        if as_json:
            payload: dict[str, Any] = {
                "type": type(exc).__name__,
                "message": exc.message,
                "exit_code": exc.exit_code,
            }
            if isinstance(exc, NoticeNotAcknowledgedError):
                payload["notice"] = exc.notice
            stdout.write(json.dumps({"error": payload}) + "\n")
        return exc.exit_code
    except KeyboardInterrupt:
        stderr.write("error: Interrupted. Run the command again when ready.\n")
        return 1
    if as_json:
        stdout.write(json.dumps(result.data, default=str) + "\n")
    elif result.text:
        stdout.write(result.text + "\n")
    return 0


# --- history ---------------------------------------------------------------------------------


def _configure_history(p: argparse.ArgumentParser) -> None:
    p.add_argument("--limit", type=int, default=20, help="number of records (default 20)")


@command("history", "show recent activity")(_configure_history)
def _history(app: App, args: argparse.Namespace) -> Output:
    records = app.history(args.limit)
    if not records:
        text = "No activity yet."
    else:
        lines = []
        for r in records:
            local = r.at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            name = f' "{r.recording_name}"' if r.recording_name else ""
            detail = f" - {r.detail}" if r.detail else ""
            lines.append(f"{local}  {r.event}{name}: {r.outcome}{detail}")
        text = "\n".join(lines)
    return Output(text, {"records": [r.to_dict() for r in records]})


# --- shared formatting -----------------------------------------------------------------------

WARNING_TEXT = {
    "too_short": "it is shorter than 5 seconds, so it repeats without a blend",
    "loop_ghosting": (
        "its first and last moments differ a lot, so the 1-second blend may show a double image"
    ),
}


def hms(seconds: float) -> str:
    """Format seconds as ``h:mm:ss``."""
    total = int(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _onoff(on: bool) -> str:
    return "ON" if on else "OFF"


def _warning_lines(warnings: list[str]) -> list[str]:
    return [f"Warning: {WARNING_TEXT.get(w, w)}." for w in warnings]


def _no_args(p: argparse.ArgumentParser) -> None:
    """Configure a command that takes no arguments."""


# --- import ----------------------------------------------------------------------------------


def _configure_import(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", type=Path, help="video file to copy into the library")
    p.add_argument("--name", help="name for the recording (default: date and time)")


@command("import", "copy a video file into the library")(_configure_import)
def _import(app: App, args: argparse.Namespace) -> Output:
    result = app.import_file(args.path, args.name)
    rec = result.recording
    lines = [
        f'Imported "{rec.name}": {hms(rec.duration_s)}, {rec.width}x{rec.height}, 30 fps.',
        *_warning_lines(list(result.warnings)),
    ]
    return Output("\n".join(lines), {"recording": rec.to_dict(), "warnings": result.warnings})


# --- present / stop / status -----------------------------------------------------------------


def _configure_present(p: argparse.ArgumentParser) -> None:
    p.add_argument("recording", nargs="?", help="name or id prefix (default: the default one)")


@command("present", "show a recording on OBS Virtual Camera")(_configure_present)
def _present(app: App, args: argparse.Namespace) -> Output:
    s = app.present(args.recording)
    size = f" (recording {s.recording_width}x{s.recording_height})" if s.recording_width else ""
    lines = [
        f'Presenting "{s.recording_name}"{size} on OBS Virtual Camera, canvas'
        f" {s.canvas_width}x{s.canvas_height} at 30 fps.",
        f"Recording indicator: {_onoff(s.indicator_on)}",
        'In Teams or Zoom, choose "OBS Virtual Camera" as your camera.',
        "If it is not listed, close and reopen Teams or Zoom, or reopen its camera settings.",
        'Keep the "Teams Avatar" window open during meetings; closing it stops the camera.',
        *_warning_lines(list(s.warnings)),
    ]
    return Output("\n".join(lines), s.to_dict())


def status_text(s: PresentationStatus) -> str:
    """Describe a presentation status in one line (contracts/cli.md ``status``)."""
    indicator = f"Recording indicator: {_onoff(s.indicator_on)}"
    if not s.service_running:
        note = f" ({s.last_failure})" if s.last_failure else ""
        return f"The camera service is not running{note}. Quality {s.quality}. {indicator}"
    canvas = f"Canvas {s.canvas_width}x{s.canvas_height} (quality {s.quality})."
    if s.presenting:
        return f'Presenting "{s.recording_name}" for {hms(s.elapsed_s or 0)}. {canvas} {indicator}'
    return f'Not presenting; the camera shows "Camera paused". {canvas} {indicator}'


@command("status", "show what the camera is showing")(_no_args)
def _status(app: App, args: argparse.Namespace) -> Output:
    s = app.status()
    return Output(status_text(s), s.to_dict())


@command("stop", 'stop presenting; the camera shows "Camera paused"')(_no_args)
def _stop(app: App, args: argparse.Namespace) -> Output:
    was_presenting = app.status().presenting
    s = app.stop()
    text = (
        'Stopped. The camera now shows "Camera paused".'
        if was_presenting
        else "Not presenting; nothing to stop."
    )
    return Output(text, s.to_dict())


# --- indicator / quality / shutdown ----------------------------------------------------------


def _configure_indicator(p: argparse.ArgumentParser) -> None:
    p.add_argument("state", choices=["on", "off"])


@command("indicator", 'turn the "RECORDED VIDEO" banner on or off')(_configure_indicator)
def _indicator(app: App, args: argparse.Namespace) -> Output:
    on = args.state == "on"
    app.set_indicator(on)
    return Output(f"Recording indicator: {_onoff(on)}", {"indicator_on": on})


def _configure_quality(p: argparse.ArgumentParser) -> None:
    p.add_argument("quality", choices=list(QUALITIES))


@command("quality", "set the presentation picture size")(_configure_quality)
def _quality(app: App, args: argparse.Namespace) -> Output:
    change = app.set_quality(args.quality)
    text = f"Presentation quality set to {args.quality}."
    if change.applies_at_next_service_start:
        text += (
            " The camera is already running at the old size."
            ' Run "teams-avatar shutdown", then "teams-avatar present" to use it.'
        )
    else:
        text += " It applies the next time you present."
    return Output(
        text,
        {
            "presentation_quality": args.quality,
            "applies_at_next_service_start": change.applies_at_next_service_start,
        },
    )


@command("shutdown", "stop presenting and release OBS Virtual Camera")(_no_args)
def _shutdown(app: App, args: argparse.Namespace) -> Output:
    running = app.status().service_running
    app.shutdown()
    text = (
        "Released OBS Virtual Camera; the Teams Avatar window has closed."
        if running
        else "Nothing to release; the camera is not running."
    )
    return Output(text, {"was_running": running})


# --- hidden: the camera service process ------------------------------------------------------


def _run_service(app: App, args: argparse.Namespace) -> Output:
    # Imported here so ordinary commands never load the service and its device adapters.
    from teams_avatar.service import serve_main

    code = serve_main()
    if code:
        raise SystemExit(code)
    return Output("", {})


COMMANDS["service"] = Command("service", None, _no_args, _run_service, needs_notice=False)


# --- library management (US3) ----------------------------------------------------------------


def make_playback_ui() -> PlaybackUI:
    """Open the preview window (a seam so tests can replace it)."""
    from teams_avatar.adapters.preview_window import OpenCvPlaybackUI

    return OpenCvPlaybackUI()


def _human_size(size: int) -> str:
    """Format a byte count as KB/MB/GB."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


_SHORT_WARNING = {"too_short": "too short", "loop_ghosting": "may ghost at repeat"}


@command("list", "list stored recordings")(_no_args)
def _list(app: App, args: argparse.Namespace) -> Output:
    views = app.list_recordings()
    data = {
        "recordings": [
            {"recording": v.recording.to_dict(), "presenting": v.presenting, "missing": v.missing}
            for v in views
        ]
    }
    if not views:
        return Output(
            'No recordings yet. Run "teams-avatar record" or "teams-avatar import <file>".', data
        )
    header = ("Name", "Duration", "Added", "Size", "Picture", "Origin", "Notes")
    rows: list[tuple[str, ...]] = []
    for v in views:
        r = v.recording
        notes = [
            *(["default"] if r.is_default else []),
            *(["presenting"] if v.presenting else []),
            *(["missing"] if v.missing else []),
            *(_SHORT_WARNING.get(w, w) for w in r.warnings),
        ]
        rows.append(
            (
                r.name,
                hms(r.duration_s),
                r.created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                _human_size(r.size_bytes),
                f"{r.width}x{r.height}",
                r.origin,
                ", ".join(notes),
            )
        )
    widths = [max(len(row[i]) for row in (header, *rows)) for i in range(len(header))]
    lines = [
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in (header, *rows)
    ]
    return Output("\n".join(lines), data)


def _configure_ref(p: argparse.ArgumentParser) -> None:
    p.add_argument("recording", help="name or id prefix (6+ characters)")


@command("preview", "play a recording once in a window")(_configure_ref)
def _preview(app: App, args: argparse.Namespace) -> Output:
    app.preview(args.recording, make_playback_ui())
    return Output("", {"previewed": args.recording})


def _configure_rename(p: argparse.ArgumentParser) -> None:
    _configure_ref(p)
    p.add_argument("new_name", help="new name")


@command("rename", "rename a recording")(_configure_rename)
def _rename(app: App, args: argparse.Namespace) -> Output:
    rec = app.rename(args.recording, args.new_name)
    return Output(f'Renamed to "{rec.name}".', {"recording": rec.to_dict()})


def _configure_delete(p: argparse.ArgumentParser) -> None:
    _configure_ref(p)
    p.add_argument("--yes", action="store_true", help="delete without asking")


@command("delete", "delete a recording and its file")(_configure_delete)
def _delete(app: App, args: argparse.Namespace) -> Output:
    target = app.library.find(args.recording)
    if not args.yes:
        stdin, stdout = _io.stdin, _io.stdout
        if args.json or not stdin.isatty():
            raise ConfirmationRequiredError(target.name)
        stdout.write(f'Delete "{target.name}" and its video file? [y/N] ')
        stdout.flush()
        if stdin.readline().strip().lower() != "y":
            raise ConfirmationRequiredError(target.name, declined=True)
    app.delete(target.id)
    return Output(f'Deleted "{target.name}".', {"deleted": target.to_dict()})


@command("default", "use a recording when present is run without a name")(_configure_ref)
def _default(app: App, args: argparse.Namespace) -> Output:
    rec = app.set_default(args.recording)
    return Output(f'"{rec.name}" is now the default recording.', {"recording": rec.to_dict()})


# --- recording (US2) ---------------------------------------------------------------------------


def make_recording_ui() -> RecordingUI:
    """Open the recording preview window (a seam so tests can replace it)."""
    from teams_avatar.adapters.preview_window import OpenCvRecordingUI

    return OpenCvRecordingUI()


@command("cameras", "list the physical cameras you can record from")(_no_args)
def _cameras(app: App, args: argparse.Namespace) -> Output:
    cameras = app.list_cameras()
    data = {"cameras": [dataclasses.asdict(c) for c in cameras]}
    if not cameras:
        return Output("No camera found. Connect a camera, then try again.", data)
    lines = [f"{c.name} (default)" if i == 0 else c.name for i, c in enumerate(cameras)]
    return Output("\n".join(lines), data)


def _configure_record(p: argparse.ArgumentParser) -> None:
    p.add_argument("--name", help="name for the recording (default: date and time)")
    p.add_argument("--camera", help='camera name from "teams-avatar cameras" (default: first)')


@command("record", "record yourself; Space or Enter saves, Esc cancels")(_configure_record)
def _record(app: App, args: argparse.Namespace) -> Output:
    result = app.record(args.name, args.camera, make_recording_ui())
    if result is None:
        return Output("Cancelled; nothing was saved.", {"recording": None})
    rec = result.recording
    lines = [
        f'Saved "{rec.name}": {hms(rec.duration_s)}, {rec.width}x{rec.height}, 30 fps.',
        *_warning_lines(list(result.warnings)),
    ]
    return Output("\n".join(lines), {"recording": rec.to_dict(), "warnings": result.warnings})
