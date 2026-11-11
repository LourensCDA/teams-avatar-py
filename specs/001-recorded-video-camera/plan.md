# Implementation Plan: Recorded Video Camera

**Branch**: `feature/recorded-video-camera` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-recorded-video-camera/spec.md`

## Summary

A Windows command-line tool that records the user from a physical camera (or imports an existing
video), keeps the recordings in a local library, and presents a chosen recording through the
"OBS Virtual Camera" device so Teams and Zoom show it in place of the live camera. The recording
repeats with a one-second crossfade at the repeat point; a neutral placeholder is shown when not
presenting; an optional "RECORDED VIDEO" indicator is off by default.

Technical approach (from [research.md](research.md)): PyAV captures, encodes and decodes video;
pyvirtualcam feeds OBS Virtual Camera; a background camera service owns the virtual camera and
is controlled over a local named pipe; all logic sits behind an application facade so a window
or tray front end can be added later.

## Technical Context

**Language/Version**: Python 3.12 (managed with `uv`)

**Primary Dependencies**: `pyvirtualcam==0.15.0` (virtual camera), `av==19.0.2` (capture,
H.264 encode/decode), `opencv-python==5.0.0.93` (preview window, overlays, scaling),
`numpy==2.5.4` (frame arrays). Dev: `pytest`, `mypy` (strict), `ruff`. Justification per
dependency in research R1–R3, R7, R9.

**Storage**: Local files under `%LOCALAPPDATA%\TeamsAvatar\` — JSON metadata and settings,
MP4 (H.264) recordings, JSON-lines activity log (research R8, [data-model.md](data-model.md)).

**Testing**: pytest. Unit tests use fakes for camera, virtual camera and control channel; codec
and named-pipe contract tests run without hardware; camera/OBS contract tests run only with
`TEAMS_AVATAR_HW=1`.

**Target Platform**: Windows 11 desktop with OBS Studio installed (provides OBS Virtual Camera);
Teams and Zoom desktop applications.

**Project Type**: Single-project CLI application with a background service process.

**Performance Goals**: 30 pictures per second delivered to the virtual camera with ≤ 1% dropped
or repeated over 10 minutes (Quality Bar, SC-004); stop → placeholder ≤ 2 s (SC-005); `present`
→ recording visible ≤ 30 s from a cold start (SC-002).

**Constraints**: No network connections (Principle I); live camera opened only by `record` and
never by the camera service (FR-011); memory bounded by caching only the first second of a
recording (~83 MB at 720p, ~187 MB at 1080p); presenting must leave the computer usable in a
meeting — target ≤ 25% of one CPU core at 720p (gives SC-007 a number; measured from the
process's CPU seconds, research R10).

**Interpretation of SC-001**: "installing the tool" includes installing OBS Studio, which this
plan makes a prerequisite (research R1). The 5-minute clock starts with both installed.

**Scale/Scope**: One user, tens of recordings, each a few minutes; ~15 CLI commands.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / rule | Status | How the plan complies |
|------------------|--------|-----------------------|
| I. Recordings local only; no network | Pass | All data in `%LOCALAPPDATA%`; control over a named pipe, not a socket (R4); no HTTP client dependency. |
| I. Least device/system access | Pass | Camera: `record`/`preview` of live feed only. Storage: own data folder. One-time component install: OBS Studio (admin rights only for that install). No other elevation. |
| I. Live camera never reaches a meeting | Pass | Camera service has no `CameraSource`; enforced by test that the service module does not import the capture adapter. |
| I. Disclosure indicator available, may be off by default; state visible at all times while presenting; first-use notice | Pass | FR-017 indicator; state in the camera service window title for the whole presentation (research R11) and in `present`/`status`; first-use notice in the facade and CLI contract. |
| I. Own likeness only | Pass | Tool records/imports the user's own video; first-use notice states it. No likeness generation. |
| I. Validate files before use | Pass | Import validation (R3); JSON files validated on load with `schema_version`, unreadable files reported not partially used (R8); control messages are JSON, never unpickled (R4). |
| II. Test-first; unit tests offline; contract tests per boundary | Pass | Fakes for all device boundaries; contract tests listed in [contracts/app-api.md](contracts/app-api.md); hardware-gated tests skip with a recorded reason. |
| III. Typed, docstrings, mypy + ruff clean, pinned justified deps | Pass | Tooling fixed in R9; 4 runtime deps each justified. |
| III. Build only what the spec needs | Pass | No GUI; facade is the minimum seam FR-019 requires. Camera service is required by FR-008–FR-010 and FR-020 (see Complexity Tracking). |
| IV. Structured logs with correlation id; no picture content | Pass | JSON log formatter; `session_id` per recording/presentation session. |
| IV. Idempotent retries; confirm destructive ops | Pass | `present` of the recording already presenting returns success; `stop`/`shutdown` are no-ops when already stopped; startup reconciliation repairs interrupted writes (R8); `delete` confirms. |
| IV. Explicit failures; never fall back to live camera | Pass | Failure or decoder stall → placeholder within 200 ms (pacer thread, R4; FR-020); failures shown in the service window (R11); service never opens a real camera. |
| IV. Device timeouts | Pass | Camera open 10 s; pipe connect 1 s; service start 10 s. |
| Technical Constraints (Python, Windows, pytest, `pyproject.toml` + lock) | Pass | As above; this plan fixes the Python version, linter, type checker and libraries for the repo. |
| Workflow (branch, gates before PR) | Pass | Work on `feature/recorded-video-camera`; gates: pytest, mypy, ruff, code review. |

**Post-design re-check (after Phase 1 and plan review)**: Pass. Design added no network access,
no new dependency and no elevated access beyond the OBS install. The only structural addition is
the camera service, justified below. The plan review's blocker (indicator state not visible at
all times) is resolved by research R11.

## Project Structure

### Documentation (this feature)

```text
specs/001-recorded-video-camera/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── testing-guide.md     # Phase 1 output, finalized by /speckit-implement
├── contracts/
│   ├── cli.md           # Command-line contract
│   └── app-api.md       # Facade, device boundaries, control messages
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
pyproject.toml
uv.lock
src/teams_avatar/
├── __init__.py
├── __main__.py          # python -m teams_avatar → cli.main
├── cli.py               # argparse commands; formats facade results (contracts/cli.md)
├── app.py               # Application facade (contracts/app-api.md)
├── errors.py            # TeamsAvatarError hierarchy with plain-language messages
├── models.py            # Recording, Settings, ActivityRecord, session dataclasses
├── paths.py             # Data folder resolution (TEAMS_AVATAR_HOME override)
├── library.py           # library.json load/validate/atomic save; name rules; default
├── settings.py          # settings.json
├── activity.py          # activity.jsonl append/read
├── logging_setup.py     # JSON log formatter, session correlation id
├── recording.py         # Recording session: capture loop, partial file, free-space checks
├── importer.py          # Import validation and re-encode
├── analysis.py          # too_short / loop_ghosting checks
├── frames.py            # Fit/scale rules, placeholder and indicator drawing
├── loop_player.py       # Crossfade loop over a VideoReader
├── reconcile.py         # Startup reconciliation of partial/orphaned files (research R8)
├── locks.py             # service.lock and data.lock (msvcrt)
├── service.py           # Camera service: pacer/decoder/control threads, state machine
├── service_window.py    # Console title and state/failure lines (research R11)
├── control.py           # Named-pipe client/server and message schema
└── adapters/
    ├── __init__.py
    ├── pyav_capture.py  # CameraSource via DirectShow
    ├── camera_list.py   # CameraLister via PowerShell CIM
    ├── pyav_codec.py    # VideoWriter / VideoReader
    ├── virtual_camera.py# FrameSink via pyvirtualcam (obs backend)
    └── preview_window.py# RecordingUI via OpenCV window

tests/
├── conftest.py          # tmp data folder, fakes, synthetic clip factory, hw marker
├── fakes.py             # FakeCameraSource, FakeFrameSink, FakeControlChannel, FakeCameraLister
├── unit/                # library, settings, activity, analysis, frames, loop_player,
│                        # service state machine, recording session, importer, app, cli
├── contract/            # pyav_codec, control (no hardware); pyav_capture, virtual_camera,
│                        # camera_list (TEAMS_AVATAR_HW=1)
└── integration/         # app + real codec + fake sink end to end; service process + pipe
```

**Structure Decision**: Single project with the `src/` layout. Device-specific code is confined
to `adapters/`, each behind a protocol so unit tests use fakes (Principle II) and a later front
end reuses `app.py` unchanged (FR-019).

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|----------|------------|-------------------------------------|
| Background camera service process + named-pipe control | FR-008/FR-009 need `stop`/`status` from another command while presenting; FR-020 needs a placeholder between presentations; FR-010 needs one owner of the camera | Foreground `present` stopped with Ctrl+C cannot answer `status`, cannot show a placeholder after stop, and leaves the user's terminal blocked. |
| Dependency on OBS Studio being installed | Provides a signed virtual camera that Teams and Zoom list | A native Windows virtual camera needs C++/COM code outside Principle III; Unity Capture leaves a frozen picture of the user after stop (research R1). |

## Risks

| Risk | Mitigation |
|------|------------|
| Teams/Zoom do not list OBS Virtual Camera while pyvirtualcam drives it (not yet probed, R1) | TS-001/TS-002 run first in manual testing; if it fails, stop and revisit R1 before other work. |
| An already-connected meeting app keeps showing the last frame after the service dies (R1) | Service sends the placeholder for 0.5 s before any planned close; crash case checked manually; if last frame persists, document and add to known issues. |
| OBS's own virtual camera running blocks the tool | Detected by message and reported (exit code 4, `in_use`). |
| Camera names from the PnP query differ from DirectShow names | Contract test on hardware; on mismatch, the error suggests `--camera` with the exact name. |
| 720p recording looks small on the 1080p canvas (R6) | Literal FR-023 behaviour; TS-012 shows it for the user's confirmation. |
| Ghosting threshold mis-tuned | Named constant; tuned against real recordings during implementation; recorded in testing guide. |

## Plan Review (collaboration rule)

A fresh read-only review ran on 2026-11-11 against spec and constitution: 1 blocker, 3 high,
8 medium, 8 low. Device behaviour behind three findings was probed before revising.

| Finding | Severity | Resolution |
|---------|----------|------------|
| B1 Indicator state not visible at all times | blocker | Accepted: service window title (R11). |
| H1 Stalled decode can freeze the user's picture | high | Accepted: pacer/decoder/control threads, 200 ms underrun → placeholder (R4). |
| H2 No way to find the camera's highest mode | high | Accepted: mode ladder with measured fps (R2); probed webcam modes. |
| H3 Output size changes per recording | high | Accepted: fixed canvas, smaller recordings centred (R6). |
| M1 Device behaviour unprobed | medium | Partly accepted: crash/close/in-use probed (R1); Teams/Zoom listing stays a first manual check (no way to automate it). |
| M2 Facade lacks preview and first-use | medium | Accepted (contracts/app-api.md). |
| M3 Seek to non-keyframe at repeat | medium | Accepted: keyframe every 30 frames (R3). |
| M4 Service lifecycle unspecified | medium | Accepted: R4 lifecycle; idle auto-shutdown deliberately rejected (would change what a meeting shows). |
| M5 `present` retry not idempotent | medium | Accepted. |
| M6 No crash reconciliation, no schema version | medium | Accepted (R8). |
| M7 OBS missing vs in use indistinguishable | medium | Rejected after probe: messages differ (R1). |
| M8 SC-001 unverifiable | medium | Accepted: interpretation above; timed scenario TS-014. |
| L1–L8 | low | All accepted: JSON pipe messages; `status` shows quality; scope note in CLI contract; CPU method; non-interactive first-use; IR/duplicate cameras; data lock; repeated stop-latency runs. |
