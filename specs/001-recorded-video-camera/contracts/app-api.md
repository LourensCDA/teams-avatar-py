# Contract: Application Facade and Device Boundaries

**Feature**: [../spec.md](../spec.md) | **Data model**: [../data-model.md](../data-model.md)

The facade is the only surface a front end uses (FR-019): the CLI now, a window or tray icon later.
It returns plain result objects and raises `TeamsAvatarError` subclasses; it never prints.
Signatures are indicative; exact typing is settled in implementation under mypy strict.

## Facade: `teams_avatar.app.App`

| Method | Returns | Raises |
|--------|---------|--------|
| `list_cameras()` | `list[CameraInfo]` | `DeviceError` |
| `record(name: str \| None, camera: str \| None, ui: RecordingUI)` | `RecordResult` (recording, warnings) or `None` if cancelled | `CameraUnavailableError`, `NameClashError`, `StorageFullError` |
| `import_file(path: Path, name: str \| None)` | `RecordResult` | `ImportRejectedError` (reason), `NameClashError` |
| `first_use_notice()` | `str \| None` — notice text, or `None` once acknowledged | — |
| `acknowledge_first_use()` | `Settings` | — |
| `list_recordings()` | `list[RecordingView]` (adds `presenting`, `missing`) | — |
| `preview(ref: str, ui: PlaybackUI)` | `None` (plays once, blend shown at the end) | `NotFoundError`, `RecordingUnreadableError` |
| `rename(ref: str, new_name: str)` | `Recording` | `NotFoundError`, `NameClashError`, `InUseError` |
| `delete(ref: str)` | `None` | `NotFoundError`, `InUseError` |
| `set_default(ref: str)` | `Recording` | `NotFoundError` |
| `present(ref: str \| None)` | `PresentationStatus`. Presenting the recording that is already presenting returns the current status (idempotent retry, Principle IV). | `NotFoundError`, `NoDefaultError`, `AlreadyPresentingError` (a *different* recording is presenting), `RecordingUnreadableError`, `VirtualCameraError` (`kind`: `not_installed` \| `in_use`) |
| `stop()` | `PresentationStatus` | `ServiceError` |
| `status()` | `PresentationStatus` (service_running, presenting, recording, elapsed, canvas size, quality setting, indicator) | — |
| `set_indicator(on: bool)` | `Settings` | — |
| `set_quality(q: Quality)` | `Settings` plus `applies_at_next_service_start: bool` (true while the service runs) | — |
| `shutdown()` | `None` | `ServiceError` |
| `history(limit: int)` | `list[ActivityRecord]` | — |

`RecordingUI` and `PlaybackUI` are protocols the front end supplies for the preview window (show
frame, poll key, close), so recording and preview logic do not depend on OpenCV windows.

Every facade call first runs startup reconciliation (research R8) once per process.

## Device boundaries (fakes in unit tests; contract tests per Principle II)

| Protocol | Real adapter | Contract tests (success + failure) |
|----------|--------------|------------------------------------|
| `CameraSource` — `open(camera, ladder, fps)`, `read() -> Frame`, `close()` | PyAV DirectShow capture | Picks the first ladder mode that opens and measures ≥ 27 fps over 1 s (research R2); unknown/in-use camera raises `CameraUnavailableError` within 10 s. Hardware tests run only with `TEAMS_AVATAR_HW=1`. |
| `CameraLister` — `list() -> list[CameraInfo]` | PowerShell CIM query | Returns names (duplicates suffixed `#2`); excludes "OBS Virtual Camera"; command failure raises `DeviceError`. |
| `VideoWriter` / `VideoReader` — encode/decode MP4 | PyAV (`libx264`) | Round-trips synthetic frames (size, count, fps); keyframe every 30 frames; seek to frame 30 lands exactly; truncated/non-video file raises `UnreadableVideoError`. Runs without hardware. |
| `FrameSink` — `open(w, h, fps)`, `send(frame)`, `sleep_until_next_frame()`, `close()` | pyvirtualcam `obs` backend | OBS missing → `VirtualCameraError(kind="not_installed")`; already driven by another sender → `VirtualCameraError(kind="in_use")` (messages probed, research R1). Live send test only with `TEAMS_AVATAR_HW=1`. |
| `ControlChannel` — request/response between CLI and service | `multiprocessing.connection` named pipe, JSON via `send_bytes`/`recv_bytes` | Round-trips each message; wrong key rejected; non-JSON payload rejected; no service → `ServiceNotRunning` within 1 s. Runs without hardware. |

Unit tests on the service with fakes cover: decoder stall > 200 ms → placeholder sent and
presentation `failed`; stop → placeholder on the next frame; second service instance exits when
`service.lock` is held.

Hardware-gated tests are skipped with the recorded reason "needs camera/OBS; set
TEAMS_AVATAR_HW=1", satisfying Principle II's rule on skipped tests.

## Control messages (CLI → camera service)

| Request | Response |
|---------|----------|
| `{"op": "present", "recording_id", "path", "quality", "indicator"}` | `{"ok": true, "status": {...}}` or `{"ok": false, "error": "<code>", "message"}` |
| `{"op": "stop"}` | `{"ok": true, "status": {...}}` |
| `{"op": "status"}` | `{"ok": true, "status": {...}}` |
| `{"op": "indicator", "on": bool}` | `{"ok": true, "status": {...}}` |
| `{"op": "shutdown"}` | `{"ok": true}`, then the service exits |

Error codes: `already_presenting`, `unreadable`, `virtual_camera_not_installed`,
`virtual_camera_in_use`, `bad_request`.
