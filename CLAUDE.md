# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

Teams Avatar is a Windows CLI that records the user (or imports a video) and presents a chosen
recording through **OBS Virtual Camera** so Teams/Zoom show it instead of the live webcam.

As of this writing the repo holds only Spec Kit design docs — no source code yet. The first
feature is `specs/001-recorded-video-camera/`; implementation follows `tasks.md` there (75 tasks,
starting with T001 `pyproject.toml`). Read `plan.md`, `research.md` (decisions R1–R11),
`data-model.md`, `contracts/cli.md` and `contracts/app-api.md` before writing code — they fix
module names, message formats, exit codes and timeouts. `.specify/memory/constitution.md`
supersedes other practice where they conflict.

## Commands (once T001 lands)

Tooling is fixed repo-wide: Python 3.12 managed by `uv`, pytest, mypy (strict), ruff (lint +
format, line length 100, Google docstrings).

```powershell
uv sync                                   # install from uv.lock
uv run teams-avatar <command>             # e.g. cameras, record, import, list, present, status, stop, shutdown
uv run pytest                             # all tests (hardware tests skip)
uv run pytest tests/unit/test_library.py::test_name   # single test
$env:TEAMS_AVATAR_HW = "1"; uv run pytest tests/contract   # include camera/OBS hardware tests
uv run mypy src tests
uv run ruff check . ; uv run ruff format --check .
```

Gates before a PR: pytest, mypy, ruff all pass, plus a read-only code review of the diff.

## Architecture (planned, see plan.md)

- **`src/` layout**, package `teams_avatar`. `cli.py` (argparse) only formats results from
  `app.py`, the application facade; all logic sits behind the facade so a GUI/tray front end can
  reuse it unchanged (FR-019).
- **Device code lives only in `adapters/`** (PyAV capture/codec, pyvirtualcam sink, OpenCV
  preview window, PowerShell camera listing), each behind a protocol. Unit tests use fakes from
  `tests/fakes.py` at that boundary.
- **Camera service** (`service.py`): a separate background process (`python -m teams_avatar
  service`, own minimised console) that owns the virtual camera. `present` starts it if needed;
  `stop`/`status`/`indicator`/`shutdown` talk to it over a per-user named pipe
  (`multiprocessing.connection` `AF_PIPE`, key in `service.key`). Messages are **JSON only, never
  pickle**. Single instance via `msvcrt` lock on `service.lock`. Three threads: pacer (one frame
  every 33 ms; queue empty > 200 ms → placeholder + `failed`), decoder (read-ahead ≤ 60 frames
  from `loop_player`), control (pipe accept). The service never shuts itself down when idle.
- **The service must never open a real camera** — only `record`/`preview` do. A test enforces
  that `service.py` does not import the capture adapter. On any failure while presenting, show the
  placeholder; never fall back to the live camera.
- **Storage**: `%LOCALAPPDATA%\TeamsAvatar\` (override with `TEAMS_AVATAR_HOME`, which tests
  use): `library.json`, `settings.json`, `activity.jsonl`, `recordings/<id>.mp4`, `logs/`.
  JSON writes are atomic (temp + `os.replace`) under `data.lock` (CLI and service both write);
  every JSON file has `schema_version: 1` and unknown versions are refused, not partly read.
  Recordings are written as `<id>.partial.mp4` and renamed on success; `reconcile.py` cleans up
  after crashes on every command start.
- **Video**: fixed output canvas, smaller recordings centred (R6); loop repeats with a 1 s
  crossfade (R5); keyframe every 30 frames so the repeat seek is cheap (R3). Only the first
  second of a recording is cached in memory.
- CLI exit codes: 0 ok, 1 failed, 2 bad usage, 3 refused by a rule, 4 virtual camera unavailable
  (OBS missing vs in use have distinct messages).

## Rules that shape code here (from the constitution)

- **Test-first is non-negotiable**: write the failing test before each behaviour change. Unit and
  integration tests must run with no camera, OBS, meeting app or network — `conftest.py` makes
  `socket.socket` raise. Hardware tests use the `hardware` marker and skip unless
  `TEAMS_AVATAR_HW=1`, with reason "needs camera/OBS; set TEAMS_AVATAR_HW=1".
- **No network connections** at all; no HTTP client dependency.
- Runtime deps are pinned exactly (`pyvirtualcam==0.15.0`, `av==19.0.2`,
  `opencv-python==5.0.0.93`, `numpy==2.5.4`). A new runtime dep needs a justification in the
  plan.
- Full type annotations and docstrings on non-trivial functions; no unexplained suppressions.
- Structured JSON logs with a `session_id` per recording/presentation; never log frame content.
- State-changing ops are idempotent on retry (`present` of the current recording succeeds, `stop`
  when stopped is a no-op); destructive ops (`delete`) confirm. Device ops have timeouts (camera
  open 10 s, pipe connect 1 s, service start 10 s).
- The "RECORDED VIDEO" indicator is off by default but its on/off state must always be visible
  while presenting (service window title, `present`/`status` output).

## Spec Kit workflow

Work follows specify → plan → tasks → implement via the `speckit-*` skills in `.claude/skills/`
(PowerShell helper scripts in `.specify/scripts/powershell/`). Each plan needs a Constitution
Check. Tasks in `tasks.md` carry `[mode:direct|bestof:N|gauntlet]` tags from the Build Modes
extension (`.specify/extensions/modes/`, default `direct`); run them with `speckit-modes-implement`
to honour the tags. `testing-guide.md` is a draft that `/speckit-implement` finalizes with real
output text and the tuned ghosting threshold.

Biggest open risk: whether Teams/Zoom list OBS Virtual Camera while pyvirtualcam drives it is
unprobed — manual tests TS-001/TS-002 run first, and a failure there means revisiting R1 before
other work.
