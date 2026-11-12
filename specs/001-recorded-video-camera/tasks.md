---

description: "Task list for the Recorded Video Camera feature"
---

# Tasks: Recorded Video Camera

**Input**: Design documents from `/specs/001-recorded-video-camera/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli.md, contracts/app-api.md, quickstart.md, testing-guide.md

**Tests**: Included. Constitution Principle II (Test-First, NON-NEGOTIABLE) requires a failing test before every behaviour change, so every implementation task is preceded by its test task. Tests run without a camera, OBS, a meeting app or network; hardware contract tests are gated by `TEAMS_AVATAR_HW=1` and skip with the reason "needs camera/OBS; set TEAMS_AVATAR_HW=1".

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

Single project with the `src/` layout from plan.md: package `src/teams_avatar/`, device code in `src/teams_avatar/adapters/`, tests in `tests/unit/`, `tests/contract/`, `tests/integration/`. "R*n*" refers to sections of research.md.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 [mode:direct] Create `pyproject.toml` at repo root: project `teams-avatar`, `requires-python = ">=3.12,<3.13"`, src layout, console script `teams-avatar = "teams_avatar.cli:main"`, runtime dependencies pinned exactly `pyvirtualcam==0.15.0`, `av==19.0.2`, `opencv-python==5.0.0.93`, `numpy==2.5.4` (R9); dev dependency group `pytest`, `mypy`, `ruff` (latest at implementation time); then run `uv lock` and `uv sync` and commit `uv.lock`
- [X] T002 [mode:direct] Create package skeleton per plan.md: `src/teams_avatar/__init__.py` (version string), `src/teams_avatar/__main__.py` (calls `cli.main`), `src/teams_avatar/adapters/__init__.py`, and empty `tests/unit/`, `tests/contract/`, `tests/integration/` with `__init__.py` files
- [X] T003 [P] [mode:direct] Configure ruff (lint rules incl. `D` docstrings with Google convention, `ANN`, `I`, `B`, `UP`; line length 100) and `ruff format` in `pyproject.toml`
- [X] T004 [P] [mode:direct] Configure mypy strict for `src` and `tests` in `pyproject.toml` (ignore missing imports only for `pyvirtualcam`, `cv2`, with a comment giving the reason)
- [X] T005 [P] [mode:direct] Configure pytest in `pyproject.toml`: `testpaths = ["tests"]`, marker `hardware` with description, `-ra` so skip reasons are always printed
- [X] T006 [mode:direct] Create `tests/conftest.py` with: fixture `data_home` that points `TEAMS_AVATAR_HOME` at `tmp_path`; autouse fixture that makes `socket.socket` raise in unit and integration tests (enforces "no network", Principle I); `hardware` marker handling that skips unless `TEAMS_AVATAR_HW=1` with reason "needs camera/OBS; set TEAMS_AVATAR_HW=1"; factory fixture `make_clip(path, frames, width, height, fps=30, distinct=True)` that writes a synthetic MP4 with a frame counter drawn in each frame (uses PyAV directly)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests for Foundational (write first, confirm they FAIL)

- [X] T007 [P] [mode:direct] Unit tests for data folder resolution in `tests/unit/test_paths.py`: default `%LOCALAPPDATA%\TeamsAvatar\`, `TEAMS_AVATAR_HOME` override, sub-paths `library.json`, `recordings/`, `settings.json`, `activity.jsonl`, `logs/teams-avatar.log`, `service.key`, `service.lock`, `data.lock`; folders created on first use
- [X] T008 [P] [mode:direct] Unit tests for the error hierarchy in `tests/unit/test_errors.py`: every error has a plain-language message of the form "<cause>. <what to do next>" (FR-014) and a CLI exit code per contracts/cli.md (1 failed, 2 usage, 3 refused, 4 virtual camera unavailable)
- [X] T009 [P] [mode:direct] Unit tests for the library store in `tests/unit/test_library.py`: names "1–64 characters; letters, digits, spaces, `-`, `_`, `.`; trimmed" and "Unique case-insensitively across the library"; default name "`Recording YYYY-MM-DD HH-MM-SS` (local time)"; "At most one recording has `true`" for `is_default`; lookup by name (case-insensitive) or unique id prefix of ≥ 6 characters; atomic save (temp + `os.replace`); `"schema_version": 1` written and an unknown version refused with a message, not partly read; corrupt JSON refused with a message
- [X] T010 [P] [mode:direct] Unit tests for settings in `tests/unit/test_settings.py`: defaults `indicator_on=false`, `presentation_quality="720p"` (enum `720p` | `1080p`), `first_use_acknowledged=false`; persisted with `schema_version: 1`; invalid values refused
- [X] T011 [P] [mode:direct] Unit tests for the activity log in `tests/unit/test_activity.py`: append one JSON object per line with fields `at`, `event` (enum `recorded` | `imported` | `presented` | `presentation_ended` | `renamed` | `deleted` | `record_failed` | `import_rejected`), `recording_id`, `recording_name`, `session_id`, `outcome`, `detail`; read last N in order; no field may hold bytes/arrays (no picture content, FR-016)
- [X] T012 [P] [mode:direct] Unit tests for locks in `tests/unit/test_locks.py`: `data.lock` serialises two writers (threads); `service.lock` held by one holder makes a second `try_acquire` return false immediately
- [X] T013 [P] [mode:direct] Unit tests for JSON logging in `tests/unit/test_logging_setup.py`: each line is JSON with `ts`, `level`, `msg`, `session_id` (when set via context); arrays/bytes in `extra` are rejected (Principle IV: no picture content)
- [X] T014 [P] [mode:direct] Contract tests (no hardware) for the codec adapter in `tests/contract/test_pyav_codec.py`: write N synthetic frames at 1280×720/30 fps with `libx264` preset `veryfast`, CRF 20, `yuv420p`, keyframe every 30 frames, then read back same size, count, fps; `seek(30)` returns frame 30 exactly; probe returns duration, frame_count, width, height; a text file renamed `.mp4` and a half-truncated MP4 raise `UnreadableVideoError`
- [X] T015 [P] [mode:direct] Unit tests for loop analysis in `tests/unit/test_analysis.py`: `too_short` when "`duration_s < 5.0`"; `loop_ghosting` when mean absolute difference of first and last frames at 160×90 greyscale exceeds the named constant `GHOST_THRESHOLD = 12` (R5); identical first/last frames give no warning
- [X] T016 [P] [mode:direct] Unit tests for frame geometry and drawing in `tests/unit/test_frames.py`: size cap rule "Longer side ≤ 1920 and shorter side ≤ 1080 … so portrait video keeps its orientation", even dimensions, never scales up; `fit_on_canvas` scales down only, centres smaller frames at their own size on the neutral background (R6) including portrait and 4:3; `placeholder(w, h)` returns a dark grey frame with the "Camera paused" text region and no input-derived pixels; `draw_indicator` changes only the lower-left banner region and scales with frame height at 720p and 1080p
- [X] T017 [P] [mode:direct] Unit tests for startup reconciliation in `tests/unit/test_reconcile.py`: `*.partial.mp4` not held by a running recording is deleted; an `<id>.mp4` with no library entry is added as `origin=imported` named "`Recovered YYYY-MM-DD HH-MM-SS`" and reported once; entries whose file is missing are kept and flagged `missing`
- [X] T018 [P] [mode:direct] Unit tests for the first-use flow in `tests/unit/test_app_first_use.py`: `App.first_use_notice()` returns text stating the indicator is off by default and that disclosure and meeting-rule compliance are the user's responsibility, and returns `None` after `acknowledge_first_use()`
- [X] T019 [P] [mode:direct] Unit tests for the CLI frame in `tests/unit/test_cli_base.py`: `--json` prints one JSON object; errors print `error: <cause>. <what to do next>` to stderr with the mapped exit code; first-use notice requires `y` interactively; with `--json` or non-tty stdin it fails with exit code 3 and the notice unless `--accept-notice` is given; the hidden `service` command is exempt and starts without the notice even when it was never acknowledged (contracts/cli.md)

### Implementation for Foundational

- [X] T020 [P] [mode:direct] Implement data folder resolution in `src/teams_avatar/paths.py` (passes T007)
- [X] T021 [P] [mode:direct] Implement `TeamsAvatarError` hierarchy in `src/teams_avatar/errors.py` (`CameraUnavailableError`, `NameClashError`, `StorageFullError`, `ImportRejectedError`, `NotFoundError`, `InUseError`, `NoDefaultError`, `AlreadyPresentingError`, `RecordingUnreadableError`, `UnreadableVideoError`, `VirtualCameraError` with `kind` `not_installed` | `in_use`, `ServiceError`, `ServiceNotRunning`, `DeviceError`, `NoticeNotAcknowledgedError`), each with message and exit code (passes T008)
- [X] T022 [mode:direct] Implement dataclasses in `src/teams_avatar/models.py`: `Recording` (fields and rules exactly as data-model.md, incl. `origin` enum `recorded` | `imported`, `warnings` list of `too_short` | `loop_ghosting`, `source_name` "Imported only: original file name (not full path)"), `Settings`, `ActivityRecord`, `RecordingSessionInfo`, `PresentationStatus` (service_running, presenting, recording name/id, elapsed, canvas size, quality, indicator), `CameraInfo`, `RecordResult`, `RecordingView` (adds `presenting`, `missing`)
- [X] T023 [P] [mode:direct] Implement `src/teams_avatar/locks.py` with `msvcrt.locking` based `DataLock` (blocking, short) and `ServiceLock` (`try_acquire`) (passes T012)
- [X] T024 [mode:direct] Implement `src/teams_avatar/library.py` (load/validate/atomic save under `DataLock`, name rules, default handling, lookup) (passes T009; depends on T020–T023) — covers FR-004, FR-005
- [X] T025 [P] [mode:direct] Implement `src/teams_avatar/settings.py` (passes T010; depends on T022–T023) — covers FR-017, FR-023
- [X] T026 [P] [mode:direct] Implement `src/teams_avatar/activity.py` (append under `DataLock`, read last N) (passes T011; depends on T022–T023) — covers FR-016
- [X] T027 [P] [mode:direct] Implement `src/teams_avatar/logging_setup.py` (JSON formatter, `session_id` context var, file handler at `logs/teams-avatar.log`) (passes T013)
- [X] T028 [P] [mode:bestof:3] Implement `VideoWriter`/`VideoReader`/`probe` protocols and PyAV adapter in `src/teams_avatar/adapters/pyav_codec.py` (passes T014) — covers FR-004, FR-021, FR-022
- [X] T029 [P] [mode:direct] Implement `src/teams_avatar/analysis.py` (passes T015; depends on T028) — covers FR-015
- [X] T030 [P] [mode:bestof:3] Implement `src/teams_avatar/frames.py` with OpenCV drawing for placeholder ("Camera paused", camera-off symbol) and the "RECORDED VIDEO" banner (passes T016) — covers FR-017, FR-020, FR-022, FR-023
- [X] T031 [mode:direct] Implement `src/teams_avatar/reconcile.py` (passes T017; depends on T024, T028) — covers FR-004
- [X] T032 [mode:direct] Implement `App` facade skeleton in `src/teams_avatar/app.py`: constructor wiring (paths, library, settings, activity, adapters injected for tests), one-time reconciliation per process, `first_use_notice()`, `acknowledge_first_use()`, `history(limit)` (passes T018; depends on T024–T026, T031) — covers FR-019
- [X] T033 [mode:direct] Implement CLI frame in `src/teams_avatar/cli.py`: argparse with sub-commands registry, global `--json` and `--accept-notice`, first-use prompt, error-to-exit-code mapping, `history [--limit N]` command (default 20) (passes T019; depends on T032) — covers FR-014, FR-016, FR-018

**Checkpoint**: Foundation ready - `uv run pytest`, `uv run mypy src tests`, `uv run ruff check .` all pass; user story implementation can now begin

---

## Phase 3: User Story 1 - Present a stored recording as my camera in a meeting (Priority: P1) 🎯 MVP

**Goal**: Import a video, present it through "OBS Virtual Camera" with a blended repeat, show the placeholder when not presenting, report status, switch the indicator, and stop within 2 s.

**Independent Test**: `import` a clip, `present` it, select "OBS Virtual Camera" in Teams and Zoom and see it repeat without a jump; `status` shows name, elapsed time and indicator; `stop` shows "Camera paused" within 2 s (testing-guide TS-001 to TS-006, TS-011, TS-012).

### Tests for User Story 1 (write first, confirm they FAIL)

- [X] T034 [P] [US1] [mode:direct] Unit tests for import in `tests/unit/test_importer.py`: valid file re-encoded into `recordings/<id>.mp4` at 30 fps, audio dropped, size capped per frames rule; original file bytes unchanged (hash before/after); written to `recordings/<id>.partial.mp4` and renamed only on success; non-video and truncated files rejected with reason and library unchanged; name clash raises `NameClashError` and nothing is replaced; `source_name` is the file name only; warnings computed; `imported` / `import_rejected` activity records written (FR-021, FR-022, FR-015, FR-016)
- [X] T035 [P] [US1] [mode:direct] Unit tests for the crossfade loop in `tests/unit/test_loop_player.py` with a fake `VideoReader`: B = 30; first pass yields frames 0…N−1 with the last B blended; each repeat yields frames B…N−B−1 then frames N−B…N−1 mixed with cached start frame i at weight (i+1)/(B+1), then continues at frame B; visible loop length N−B; no blend and plain repeat when N < 150; only B frames cached; frames passed through `fit_on_canvas`
- [X] T036 [P] [US1] [mode:direct] Contract tests (no hardware) for the control channel in `tests/contract/test_control.py`: named pipe `\\.\pipe\teams-avatar-<user>-test-<uuid>`; JSON via `send_bytes`/`recv_bytes` round-trips `present`, `stop`, `status`, `indicator`, `shutdown`; wrong key rejected; non-JSON payload rejected without executing anything; client with no server raises `ServiceNotRunning` within 1 s
- [X] T037 [P] [US1] [mode:direct] Unit tests for the camera service in `tests/unit/test_service.py` with `FakeFrameSink`, fake reader and fake clock: starts idle sending the placeholder every frame; `present` switches to recording frames; same recording again returns status, different one raises `already_presenting`; `stop` → placeholder on the next frame; decoder that blocks > 200 ms → placeholder sent and `end_reason=failed`; decode error → placeholder and `failed`; indicator on/off changes the banner on the next frame; `status` reports name, elapsed, canvas, quality, indicator; `shutdown` sends the placeholder for 0.5 s then closes the sink; activity `presented`/`presentation_ended` written; service module does not import `adapters.pyav_capture` (FR-011)
- [X] T038 [P] [US1] [mode:direct] Unit tests for the service window in `tests/unit/test_service_window.py`: title strings `Teams Avatar — PRESENTING "<name>" — indicator OFF|ON` and `Teams Avatar — IDLE — camera paused`; first line "Keep this window open during meetings; closing it stops the camera."; failure line format per contracts/cli.md (title setter injected, no real console)
- [X] T039 [P] [US1] [mode:direct] Hardware contract tests for the virtual camera adapter in `tests/contract/test_virtual_camera.py` (`@pytest.mark.hardware`): open 1280×720 at 30 fps and send 90 frames; a second sender raises `VirtualCameraError(kind="in_use")`; with the registry check patched to "missing" raises `kind="not_installed"`
- [X] T040 [P] [US1] [mode:direct] Unit tests for presentation facade methods in `tests/unit/test_app_present.py` with a fake control channel and fake service launcher: `present(None)` uses the default or raises `NoDefaultError`; missing/unreadable file raises `RecordingUnreadableError` naming the recording; starts the service when `ServiceNotRunning` and waits up to 10 s; `stop`, `status`, `set_indicator` (saves and forwards when running), `set_quality` (`applies_at_next_service_start` true while running), `shutdown`
- [X] T041 [P] [US1] [mode:direct] Unit tests for US1 CLI commands in `tests/unit/test_cli_present.py`: `import`, `present`, `stop`, `status`, `indicator on|off`, `quality 720p|1080p`, `shutdown` output text and exit codes match contracts/cli.md examples, including "If it is not listed, close and reopen Teams or Zoom, or reopen its camera settings." (spec edge case: meeting app opened before presentation) and "Keep the "Teams Avatar" window open during meetings; closing it stops the camera."
- [X] T042 [US1] [mode:direct] Integration test in `tests/integration/test_present_flow.py`: real codec + real named pipe + service running in a thread with `FakeFrameSink`; import a 10 s synthetic clip, present, collect 400 frames, assert blend at the repeat point and no repeated or blank frames, stop → placeholder within 2 s of wall time — covers FR-007, FR-008, FR-020, SC-004, SC-005

### Implementation for User Story 1

- [X] T043 [P] [US1] [mode:bestof:3] Implement `src/teams_avatar/importer.py` (passes T034) — covers FR-015, FR-021, FR-022
- [X] T044 [P] [US1] [mode:gauntlet] Implement `src/teams_avatar/loop_player.py` (passes T035) — covers FR-007
- [X] T045 [P] [US1] [mode:bestof:3] Implement `src/teams_avatar/control.py`: per-user pipe name, key file written by the service before listening, JSON messages, client with 1 s connect timeout (passes T036) — covers FR-008, FR-009, FR-010, FR-012
- [X] T046 [P] [US1] [mode:direct] Implement `FrameSink` protocol and pyvirtualcam `obs` adapter in `src/teams_avatar/adapters/virtual_camera.py`, mapping "device not found! Did you install OBS?" to `not_installed` and "virtual camera output could not be started" to `in_use` (R1) (passes T039 on hardware) — covers FR-006
- [X] T047 [P] [US1] [mode:direct] Implement `src/teams_avatar/service_window.py` (console title via `SetConsoleTitleW` through `ctypes`, timestamped state/failure lines) (passes T038) — covers FR-009, FR-014, FR-017
- [X] T048 [US1] [mode:gauntlet] Implement `src/teams_avatar/service.py`: `ServiceLock` acquired before the pipe opens (second instance exits); pacer thread (one frame per 33 ms via `sleep_until_next_frame`, placeholder on underrun > 200 ms), decoder thread (read-ahead queue of up to 60 frames from `LoopPlayer`), control thread (accept loop → command queue); fixed canvas from the quality setting; Presentation Session per data-model.md; `python -m teams_avatar service` entry (passes T037; depends on T044–T047) — covers FR-006, FR-007, FR-008, FR-010, FR-011, FR-020, FR-023, SC-004, SC-005
- [X] T049 [US1] [mode:bestof:3] Add service launcher and presentation methods to `src/teams_avatar/app.py`: `import_file`, `present`, `stop`, `status`, `set_indicator`, `set_quality`, `shutdown`; launcher starts `python -m teams_avatar service` with `CREATE_NEW_CONSOLE` and minimised window (`STARTUPINFO` `SW_SHOWMINNOACTIVE`), then polls the pipe up to 10 s (passes T040; depends on T043, T045, T048) — covers FR-006, FR-008, FR-009, FR-017, FR-019, FR-023, SC-002
- [X] T050 [US1] [mode:direct] Add `import`, `present`, `stop`, `status`, `indicator`, `quality`, `shutdown`, and hidden `service` sub-commands to `src/teams_avatar/cli.py` (passes T041, T042; depends on T049) — covers FR-018

**Checkpoint**: User Story 1 is fully functional with an imported file; run quickstart steps 2, 4–8 and testing-guide TS-001/TS-002 first (plan risk: Teams/Zoom must list the camera)

---

## Phase 4: User Story 2 - Record a video of myself (Priority: P2)

**Goal**: Record from a chosen physical camera with a live preview, save under a given or default name, or cancel leaving nothing behind.

**Independent Test**: `record`, record 30 s, press `Space`; the saved file in `recordings\` has the expected duration and size and plays in any media player (testing-guide TS-007 to TS-009).

### Tests for User Story 2 (write first, confirm they FAIL)

- [X] T051 [P] [US2] [mode:direct] Unit tests for camera listing in `tests/unit/test_camera_list.py` with captured PowerShell output fixtures: parses CIM `Win32_PnPEntity` rows of class `Camera`/`Image`; excludes "OBS Virtual Camera"; duplicates become `Name`, `Name #2` with a device number; command failure raises `DeviceError`
- [X] T052 [P] [US2] [mode:direct] Unit tests for the capture mode ladder in `tests/unit/test_pyav_capture.py` with a fake opener: tries 1920×1080 MJPEG, 1920×1080 raw, 1280×720 MJPEG, 1280×720 raw, 960×540 raw, 640×480 raw in order; keeps the first that opens and measures ≥ 27 fps over 1 s; a camera failing all modes raises `CameraUnavailableError` ("unusable", e.g. infrared); "OBS Virtual Camera" refused; open timeout 10 s
- [X] T053 [P] [US2] [mode:direct] Unit tests for the recording session in `tests/unit/test_recording.py` with `FakeCameraSource` and fake `RecordingUI`: transitions "`starting → recording → (saved | cancelled | failed)`" and "`starting → failed` when the camera is unavailable or in use (no file is created)"; preview frames shown with "REC" and elapsed time; `Space`/`Enter` save, `Esc` or window closed cancel; partial file deleted on cancel/failure; free space < 200 MB before start refuses, during recording stops and keeps a valid shorter recording; name clash checked before recording starts; warnings computed; `recorded`/`record_failed` activity records with `session_id`
- [X] T054 [P] [US2] [mode:direct] Hardware contract tests in `tests/contract/test_camera_hw.py` (`@pytest.mark.hardware`): listed camera names open via DirectShow by name; 3 s capture at the chosen mode delivers ≥ 27 fps; camera held by another process raises `CameraUnavailableError` within 10 s
- [X] T055 [P] [US2] [mode:direct] Unit tests for US2 facade and CLI in `tests/unit/test_cli_record.py`: `App.list_cameras()`, `App.record(name, camera, ui)` returns `RecordResult` or `None` on cancel; `cameras` and `record [--name NAME] [--camera NAME]` output and exit codes per contracts/cli.md

### Implementation for User Story 2

- [X] T056 [P] [US2] [mode:direct] Implement `CameraLister` in `src/teams_avatar/adapters/camera_list.py` (PowerShell `Get-CimInstance Win32_PnPEntity` via `subprocess`, 10 s timeout) (passes T051) — covers FR-002
- [X] T057 [P] [US2] [mode:bestof:3] Implement `CameraSource` with the mode ladder in `src/teams_avatar/adapters/pyav_capture.py` (DirectShow by name, `video_device_number` for duplicates) (passes T052, T054) — covers FR-001, FR-002, FR-022
- [X] T058 [P] [US2] [mode:direct] Implement `RecordingUI` in `src/teams_avatar/adapters/preview_window.py` (OpenCV window, red "REC" mark, elapsed time, key polling, window-closed detection) — covers FR-001
- [X] T059 [US2] [mode:bestof:3] Implement `src/teams_avatar/recording.py` (passes T053; depends on T057) — covers FR-001, FR-003, FR-004, FR-015
- [X] T060 [US2] [mode:direct] Add `list_cameras` and `record` to `src/teams_avatar/app.py` and `cameras`, `record` sub-commands to `src/teams_avatar/cli.py` (passes T055; depends on T056, T058, T059) — covers FR-002, FR-018, FR-019

**Checkpoint**: User Stories 1 and 2 both work independently; a recording made with `record` can be presented

---

## Phase 5: User Story 3 - Manage stored recordings (Priority: P3)

**Goal**: List, preview, rename, set default and delete recordings, with refusals while presenting.

**Independent Test**: With three recordings: `list`, `preview` one, `rename` one, `default` one, `delete` one, then `present` with no name presents the default (testing-guide TS-010).

### Tests for User Story 3 (write first, confirm they FAIL)

- [X] T061 [P] [US3] [mode:direct] Unit tests for library management facade methods in `tests/unit/test_app_manage.py` with fake control channel: `list_recordings()` marks `presenting` (from service status) and `missing`; `rename` and `delete` raise `InUseError` while that recording is presenting (FR-013); `delete` removes entry and file and clears default in the same write; `set_default` clears the previous default; `renamed`/`deleted` activity records; missing entries can still be deleted
- [X] T062 [P] [US3] [mode:direct] Unit tests for preview in `tests/unit/test_app_preview.py` with fake `PlaybackUI`: plays once at 30 fps including the end blend; `Esc` closes; never touches the control channel or virtual camera; unreadable file raises `RecordingUnreadableError`
- [X] T063 [P] [US3] [mode:direct] Unit tests for US3 CLI in `tests/unit/test_cli_manage.py`: `list` table columns (name, duration, recorded/imported date, size, picture size, origin, default mark, warnings, `presenting`/`missing` marks) and empty-library hint; `preview`, `rename`, `delete [--yes]` with `[y/N]` confirmation, `default`; refusal messages and exit code 3 per contracts/cli.md example

### Implementation for User Story 3

- [X] T064 [US3] [mode:direct] Add `list_recordings`, `rename`, `delete`, `set_default` to `src/teams_avatar/app.py` (passes T061) — covers FR-005, FR-013
- [X] T065 [US3] [mode:direct] Add `PlaybackUI` to `src/teams_avatar/adapters/preview_window.py` and `preview(ref, ui)` to `src/teams_avatar/app.py` using `LoopPlayer` for one pass (passes T062) — covers FR-005
- [X] T066 [US3] [mode:direct] Add `list`, `preview`, `rename`, `delete`, `default` sub-commands to `src/teams_avatar/cli.py` (passes T063; depends on T064, T065) — covers FR-005, FR-018

**Checkpoint**: All user stories are independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T067 [P] [mode:direct] Add measurement helper `tools/frame_check.py` (test tool, not shipped): reads an ffmpeg `framemd5` file and reports received vs expected frames, consecutive identical hashes, and dropped + repeated percentage (R10, Quality Bar); unit-test it in `tests/unit/test_frame_check.py`
- [X] T068 [P] [mode:direct] Add `README.md` at repo root: prerequisites (Windows 11, Python 3.12, `uv`, OBS Studio), install, the commands from contracts/cli.md, the disclosure responsibility note, data folder location, and how to run tests (incl. `TEAMS_AVATAR_HW=1`)
- [X] T069 [P] [mode:direct] Add `settings.example.json` at repo root listing every setting with its default (Technical Constraints: committed example of settings)
- [X] T070 [mode:direct] Run the hardware contract tests on the development machine: `$env:TEAMS_AVATAR_HW=1; uv run pytest tests/contract` and record results in `specs/001-recorded-video-camera/testing-guide.md` section 2
- [ ] T071 [mode:direct] Tune `GHOST_THRESHOLD` in `src/teams_avatar/analysis.py` against at least three real recordings (still, small movement, sitting vs standing) and record the chosen value and evidence in `specs/001-recorded-video-camera/research.md` R5
- [X] T072 [mode:direct] Security pass: confirm no module imports `socket`, `http`, `urllib` or `requests` (add `tests/unit/test_no_network_imports.py` scanning `src/teams_avatar`), logs and `activity.jsonl` hold no picture data, pipe rejects wrong key and non-JSON (T036) — covers FR-012, SC-006
- [ ] T073 [mode:direct] Run quickstart.md validation end to end on the development machine
- [X] T074 [mode:direct] Finalize testing-guide.md against the built feature (real commands, devices, sample data) and run its automated checks
- [X] T075 [mode:direct] Run `/code-review` read-only on the uncommitted diff of `src/` and `tests/` per the collaboration rule; fix confirmed blocker/high findings (one re-review only) and report any unresolved ones

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3–5)**: All depend on Foundational completion
  - Can proceed in parallel, or sequentially in priority order (P1 → P2 → P3)
- **Polish (Phase 6)**: Depends on all user stories being complete (T067–T069 can start any time after Phase 2)

### User Story Dependencies

- **User Story 1 (P1)**: After Foundational. Independent: uses `import` to get a recording.
- **User Story 2 (P2)**: After Foundational. Independent: the saved file is checked with the codec `probe` and opened in any media player; `preview` (US3) and `present` (US1) are only needed for the in-tool playback check.
- **User Story 3 (P3)**: After Foundational. `presenting` marks and in-use refusals use the control channel from US1 (T045); without US1 the fake channel reports "not running" and refusals are tested with fakes only.

### Within Each User Story

- Tests MUST be written and FAIL before implementation (Principle II)
- Adapters and pure modules before the facade; facade before CLI
- Story complete before moving to next priority

### Parallel Opportunities

- Setup: T003, T004, T005 together
- Foundational tests: T007–T019 all [P]
- Foundational implementation: T020, T021, T023, T027, T028, T030 together; then T025, T026, T029 after models/locks
- US1 tests T034–T041 together; implementation T043–T047 together, then T048 → T049 → T050
- US2 tests T051–T055 together; implementation T056–T058 together, then T059 → T060
- US3 tests T061–T063 together; T064 then T065 (both edit `app.py`)
- Within a phase, a [P] task may still wait on an earlier task it names (e.g. T025/T026 need T022–T023, T029 needs T028); [P] means it can run alongside the other tasks ready at that point
- Different stories can run in parallel after Phase 2, except that edits to `app.py` and `cli.py` must be serialised

---

## Parallel Example: User Story 1

```text
# Write all US1 tests together:
Task: "Unit tests for import in tests/unit/test_importer.py"
Task: "Unit tests for the crossfade loop in tests/unit/test_loop_player.py"
Task: "Contract tests for the control channel in tests/contract/test_control.py"
Task: "Unit tests for the camera service in tests/unit/test_service.py"
Task: "Unit tests for the service window in tests/unit/test_service_window.py"

# Then the independent modules together:
Task: "Implement src/teams_avatar/importer.py"
Task: "Implement src/teams_avatar/loop_player.py"
Task: "Implement src/teams_avatar/control.py"
Task: "Implement src/teams_avatar/adapters/virtual_camera.py"
Task: "Implement src/teams_avatar/service_window.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: TS-001/TS-002 first — if Teams or Zoom do not list "OBS Virtual Camera" while the tool drives it, stop and revisit research R1 before anything else
5. Demo with an imported video

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. User Story 1 → validate → demo (MVP)
3. User Story 2 → validate → demo
4. User Story 3 → validate → demo
5. Polish, finalize testing guide, code review

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Every task follows test-first: confirm the test fails before implementing
- Commit after each task or logical group (only when the user asks)
- Stop at any checkpoint to validate a story independently
- Edits to `src/teams_avatar/app.py` and `src/teams_avatar/cli.py` happen in several stories; do not run those tasks in parallel
