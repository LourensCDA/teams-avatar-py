# Research: Recorded Video Camera

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-11-11

Findings marked **Probed** were checked on the development machine (Windows 11, Python 3.12.1,
Integrated Camera, OBS Studio installed, new Teams running, Zoom installed) with throwaway
scripts outside the repository. Library behaviour not probed was checked against the pyvirtualcam
documentation (Context7, `/letmaik/pyvirtualcam`).

## R1. How the tool's camera becomes visible to Teams and Zoom

- **Decision**: Use `pyvirtualcam` 0.15.0 with its `obs` backend, which writes frames into the
  "OBS Virtual Camera" device that OBS Studio installs and registers.
- **Rationale**:
  - Writing a Windows camera driver or a Media Foundation virtual camera source needs native
    (C++/COM) code, which conflicts with Principle III (small, typed Python) and needs signing
    and admin rights for every change.
  - OBS Virtual Camera is already installed here, is listed by both Teams and Zoom, and is the
    quality bar in the spec, so the comparison runs over the same device.
  - **Probed**: a 1280×720, 30 fps test pattern sent through pyvirtualcam was received by a
    separate consumer (ffmpeg via DirectShow) at 30.0 fps; 181 frames in 6 s, no duplicates.
  - Satisfies the spec assumption that installing a camera component once (OBS) is acceptable.
- **Consequences**:
  - OBS Studio is a prerequisite. Only one program can drive OBS Virtual Camera at a time, so
    OBS's own "Start Virtual Camera" and this tool exclude each other; the tool reports that
    clearly (FR-014).
  - The device name seen in meetings is "OBS Virtual Camera", not a name of our own.
  - Native format is NV12; pyvirtualcam converts from RGB/BGR for us.
  - **Probed**: when no program is sending frames — never started, closed cleanly, or killed
    with `taskkill /F` — a newly connecting consumer (ffmpeg) cannot open the device at all, so
    no frozen frame is offered to it. What an application that was *already* connected (Teams,
    Zoom) shows after the sender dies is checked manually in the testing guide; if it is the last
    frame, the mitigation is to send the placeholder for 0.5 s before any planned close.
  - **Probed**: a second sender while one is running fails with `RuntimeError("'obs' backend:
    virtual camera output could not be started")`; OBS missing gives a different message ("…
    device not found! Did you install OBS?"), so the two cases can be told apart by message.
  - **Not probed**: that new Teams and Zoom list the device while pyvirtualcam drives it. OBS
    Virtual Camera is a standard DirectShow device both apps support; verified by TS-001/TS-002
    before any other manual scenario.
- **Alternatives considered**:
  - `unitycapture` backend (Unity Capture DirectShow filter): needs a separate unsigned install
    via `regsvr32`, keeps showing the last frame after the sender stops (would leave a frozen
    picture of the user, against FR-020), and uses nearest-neighbour scaling.
  - Windows 11 `MFCreateVirtualCamera`: correct long-term answer and gives our own device name,
    but requires a native COM media source DLL. Revisit if OBS dependency becomes a problem.
  - Joining meetings as a bot: out of scope per spec assumptions.

## R2. Capturing from the real camera

- **Decision**: Capture with PyAV 19.0.2 (`av`) from the DirectShow device by its name; show the
  preview with OpenCV (`cv2.imshow`). List cameras by querying Windows for devices of class
  `Camera` or `Image` (CIM `Win32_PnPEntity`) through a PowerShell subprocess.
- **Rationale**:
  - **Probed**: PyAV opened "Integrated Camera" by name at 1280×720 and delivered 60 frames
    (`yuyv422`) in 2.8 s including device start-up.
  - Capturing by name lets the user choose a camera (FR-002) and lets the tool refuse to record
    from "OBS Virtual Camera", which would otherwise feed its own output back in.
  - The PnP device query lists only physical cameras, so virtual cameras are excluded by
    construction.
  - **Probed**: PyAV could not list DirectShow devices (its `list_devices` output is not
    capturable through `av.logging`), so listing needs a separate mechanism.
  - **Highest mode (FR-022)**: modes cannot be listed through PyAV either, so the capture
    adapter tries a fixed ladder and keeps the first that opens and delivers ≥ 27 fps measured
    over 1 s: 1920×1080 MJPEG, 1920×1080 raw, 1280×720 MJPEG, 1280×720 raw, 960×540 raw,
    640×480 raw. **Probed**: the Integrated Camera offers only `yuyv422` (raw), 30 fps, up to
    1280×720, so it lands on 1280×720 raw.
  - **Device list caveats**: the `Camera` class can include infrared (Windows Hello) cameras;
    these fail the ladder and are reported as unusable rather than hidden. Two cameras with the
    same name are listed as `Name`, `Name #2` and opened with DirectShow's
    `video_device_number` option.
- **Alternatives considered**:
  - OpenCV `VideoCapture` by index (**Probed**: MSMF 1.05 s, DSHOW 2.43 s to open, 720p):
    indexes give no names, so the user cannot tell cameras apart, and OBS Virtual Camera appears
    as an index too.
  - `pygrabber` for DirectShow names: one more dependency for a list the OS already provides.

## R3. Encoding, storing and decoding video

- **Decision**: Encode recordings and imports to H.264 in MP4 with PyAV's bundled `libx264`
  (preset `veryfast`, CRF 20, `yuv420p`, 30 fps, keyframe every 30 frames). Decode recordings
  and imported files with PyAV. The 30-frame keyframe interval puts a keyframe at frame B (the
  loop re-entry point, R5), so each repeat seeks straight to it instead of decoding from frame 0.
- **Rationale**:
  - **Probed**: PyAV 19.0.2 wheels include `libx264` (plus `h264_mf`, `h264_qsv`, `h264_nvenc`,
    `h264_amf`). 60 frames of 720p encoded and decoded back in 0.02 s.
  - One library covers capture, encode, decode and import validation.
  - Imports are re-encoded into the library, which normalises frame rate to 30, caps size per
    R6 (FR-022), drops audio (video only per spec), and leaves the original file untouched
    (FR-021).
- **Licensing note**: the bundled FFmpeg includes GPL components (x264). Acceptable for a
  personal tool that is not redistributed; revisit before any distribution.
- **Alternatives considered**: OpenCV `VideoWriter` (`mp4v` codec gives poor quality per byte);
  calling the `ffmpeg` executable (an extra install outside the lock file).

## R4. Running presentation while the user keeps using the command line

- **Decision**: A background **camera service** process owns the virtual camera. Commands talk
  to it over a per-user Windows named pipe (`multiprocessing.connection`, family `AF_PIPE`,
  address `\\.\pipe\teams-avatar-<user>`), authenticated with a random key stored in the tool's
  data folder. `present` starts the service if it is not running.
- **Rationale**:
  - FR-008/FR-009/FR-010 need `stop` and `status` from a second command while presentation runs,
    and only one presentation at a time.
  - FR-020 needs a placeholder picture while the tool runs but is not presenting, so something
    must keep sending frames between commands.
  - A named pipe is local-only: no network socket, so Principle I's no-network rule holds.
  - A stop takes effect at the next frame (≤ 33 ms), well inside the 2-second limit (FR-008,
    SC-005).
- **Threads inside the service** (so a stalled decode can never freeze the user's picture):
  1. *Pacer* — sends one frame every 33 ms, always. It takes the next frame from a read-ahead
     queue; if the queue has been empty for more than 200 ms it sends the placeholder and marks
     the presentation `failed` (FR-020, spec edge case "never shows a frozen frame").
  2. *Decoder* — fills the read-ahead queue (up to 60 frames) from the loop player; errors are
     passed to the pacer, which ends the presentation.
  3. *Control* — accepts pipe connections (`Listener.accept()` blocks) and hands commands to the
     pacer through a thread-safe command queue.
- **Messages**: JSON over `send_bytes`/`recv_bytes`, never pickled objects, so a connecting
  process cannot run code in the service even if it had the key.
- **Process lifecycle**:
  - `present` starts the service as `python -m teams_avatar service` with `CREATE_NEW_CONSOLE`
    and a minimised window, so it survives the terminal closing and has a visible window of its
    own (see R11).
  - Only one service: it takes an exclusive lock on `service.lock` (`msvcrt.locking`) before
    creating the pipe; a second instance that cannot lock exits at once. Concurrent `present`
    commands that both try to start it end up talking to the one that won.
  - The pipe key is regenerated by each service start, written before the pipe is opened; a
    stale key from a crashed service is simply overwritten.
  - The service does not shut itself down when idle: ending it mid-meeting would change what
    participants see. `status` and the service window show that it holds the camera;
    `shutdown` releases it.
- **Alternatives considered**: localhost TCP socket (a network connection, against Principle I);
  a foreground-only `present` stopped with Ctrl+C (no `status` from elsewhere, no placeholder
  after stop); polling a control file (slower, racy).

## R5. Repeating the recording with a blend

- **Decision**: Crossfade loop. With a recording of N frames and blend length B = 30 frames
  (1 s at 30 fps):
  1. Frames 0…B−1 are kept in memory at output size after the first pass.
  2. Each repeat plays frames B…N−B−1 directly, then frames N−B…N−1 each mixed with the matching
     cached start frame at weight (i+1)/(B+1), then continues at frame B.
  3. The visible loop length is N−B frames; the blend replaces the jump.
- **Rationale**:
  - Meets FR-007 without holding the whole recording in memory. A 3-minute 720p recording is
    ~15 GB as raw frames; only the first second is cached (~83 MB at 720p).
  - Pure array arithmetic on numpy, so it is unit-testable without any device (Principle II).
- **Short recordings**: under 5 s (150 frames) the blend is skipped and the recording repeats
  plainly, with a warning at save time (FR-015).
- **Ghosting warning**: at save/import time, compare the first and last frames downscaled to
  160×90 greyscale; mean absolute difference above 12 (of 255) triggers the FR-015 warning. The
  threshold is a named constant, tuned during implementation against real recordings.
- **Tuned (T071, 2026-11-12)**: `GHOST_THRESHOLD = 40`. Three recordings on the Integrated Camera
  in normal light, presented in Teams via OBS Virtual Camera: still 8.5, small movement 7.3,
  sitting to standing 39.3. None showed a visible double image at the repeat, so the research
  value 12 warned falsely on the sit/stand clip. 40 is just above the highest score seen to be
  fine. No recording with a visible ghost was available, so the upper bound is untested: if a
  loop that scores under 40 ever shows a double image, lower the threshold to below its score.
- **Alternatives considered**: forward-then-backward (rejected in clarification); optical-flow
  morphing (heavy dependency, slow).

## R6. Picture size and quality

- **Decision**:
  - Recording: the camera's highest mode up to 1920×1080 at 30 fps (FR-022). **Probed**: the
    Integrated Camera's highest mode is 1280×720.
  - Import: scale down only if the longer side exceeds 1920 or the shorter side exceeds 1080,
    keeping orientation (a 1080×1920 portrait video is kept as is); never scale up. Same rule as
    `width`/`height` in [data-model.md](data-model.md).
  - Presentation output is a fixed 16:9 canvas at the presentation setting (1280×720 default,
    1920×1080 optional). The placeholder uses the same canvas.
  - A recording is scaled down to fit the canvas if larger, keeping its aspect ratio; if it is
    smaller in both directions it is shown at its own size, centred on the neutral background,
    never enlarged (FR-023). Portrait and 4:3 recordings therefore sit inside the canvas with
    neutral bars.
  - The virtual camera is opened once per service run at the canvas size; changing the
    quality setting takes effect at the next service start (`shutdown`, then `present`), which
    the `quality` command says. The device is never reopened between placeholder and
    presentation, so meetings never need the camera re-selected.
- **Trade-off**: a 720p recording with the 1080p setting appears smaller with a border. This is
  the literal reading of FR-023 ("not enlarged"); TS-012 shows it to the user to confirm.

## R7. Disclosure indicator and placeholder picture

- **Decision**: Both are drawn by the tool with OpenCV on each outgoing frame; no image assets.
  - Indicator (FR-017): a semi-transparent banner in the lower-left reading "RECORDED VIDEO",
    sized relative to frame height so it stays legible at 720p and 1080p. Off by default.
  - Placeholder (FR-020): plain dark grey frame with a centred camera-off symbol and the text
    "Camera paused". Contains no image of the user.
- **Rationale**: no file to go missing; trivially testable by pixel checks on the frame array.

## R8. Storage layout and safety

- **Decision**: All data under `%LOCALAPPDATA%\TeamsAvatar\` (overridable with
  `TEAMS_AVATAR_HOME` for tests):
  - `library.json` (recording metadata), `recordings/<id>.mp4`, `settings.json`,
    `activity.jsonl` (FR-016), `logs/teams-avatar.log`, `service.key` (pipe key).
  - JSON files written atomically (write temp file, `os.replace`).
  - Recordings and imports are written to `recordings/<id>.partial.mp4` and renamed only after a
    successful finish; cancel or failure deletes the partial file (FR-003, storage-full edge case).
  - Before recording starts and every second during it, free space is checked; below 200 MB the
    recording stops and is finalised as a valid shorter recording (spec edge case: "keeps whatever
    was recorded … as a valid recording").
  - Every JSON file carries `schema_version: 1`; a file with an unknown version is refused with a
    message, not partly read (Principle I).
  - **Startup reconciliation** (any command, under the library lock): `*.partial.mp4` files not
    held by a running recording are deleted; an `<id>.mp4` with no library entry (crash between
    rename and metadata write) is added back as an `imported` recording named
    `Recovered YYYY-MM-DD HH-MM-SS` and reported once; entries whose file is missing stay
    `missing`.
  - Writes to `library.json`, `settings.json` and `activity.jsonl` take a short exclusive lock on
    `data.lock`, because the CLI and the camera service both write activity records.
- **Rationale**: per-user, local-only (FR-012, Principle I), survives restarts (FR-004).

## R9. Tooling (fixed for the repo by this first plan, per Technical Constraints)

- **Decision**:
  - Python 3.12, managed with `uv`; dependencies in `pyproject.toml` with `uv.lock` committed.
  - Runtime: `pyvirtualcam==0.15.0`, `av==19.0.2`, `opencv-python==5.0.0.93`,
    `numpy==2.5.4` (versions probed above; pinned exactly).
  - Dev: `pytest`, `mypy` (strict), `ruff` (lint + format), latest at implementation time,
    pinned in the lock file.
  - CLI with standard-library `argparse`; logging with standard-library `logging` and a JSON
    formatter.
- **Rationale**: smallest set that covers the feature (Principle III); every runtime dependency
  is justified in R1–R3 and R7.
- **Alternatives considered**: `click`/`typer` (argparse suffices for ~15 commands); `pyright`
  (mypy chosen for strict mode and simple CI use).

## R10. Measuring the quality bar and success criteria

- **Decision**: Measure from the consumer side with ffmpeg reading the DirectShow device:
  - Frame rate and duplicates: present a test clip whose every frame differs, capture 10 minutes
    with `-f framemd5`, and count consecutive identical hashes (repeated pictures) and the gap
    between expected and received frame count (dropped pictures). **Probed**: method works (181
    frames, all unique, in 6 s).
  - Stop latency: timestamp of `stop` command vs first placeholder frame in the capture, repeated
    10 times (SC-005 requires 100% of attempts).
  - CPU: `(Get-Process -Id <service pid>).CPU` sampled 60 s apart; seconds of CPU used ÷ 60 =
    fraction of one core (Task Manager shows a share of all cores, so it is not used).
- **Rationale**: matches the Quality Bar section; ffmpeg is already installed here. It is a test
  tool only, not a runtime dependency.

## R11. Keeping the indicator state visible while presenting

- **Decision**: The camera service's own console window (R4) is the always-visible status. Its
  title reads `Teams Avatar — PRESENTING "<name>" — indicator OFF` (or `IDLE — camera paused`),
  so the state shows on the taskbar button at all times without opening anything; the window
  body logs state changes and any failure in plain language (FR-014 for failures mid-meeting,
  when no command is running).
- **Rationale**: constitution Principle I says whether the indicator is on "MUST be visible to the
  user at all times while presenting". `present` and `status` output alone show it only on
  request. A console title needs no extra dependency (`SetConsoleTitleW` via `ctypes`).
- **Alternatives considered**: system-tray icon (a later front end, out of scope by
  clarification); Windows toast notifications (transient, not "at all times").
