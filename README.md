# Teams Avatar

A Windows command-line tool that plays a recording of you through a virtual camera, so Teams or
Zoom show the recording instead of your live webcam.

You record yourself once, or import a video you already have. The tool keeps your recordings in
a local library and plays the one you pick through **OBS Virtual Camera**. The recording repeats
with a one-second crossfade so there's no visible jump. When nothing is playing, the camera shows
a neutral placeholder. You can turn on a "RECORDED VIDEO" banner so people in the meeting know
it isn't live; it's off by default.

> **Status:** design only. The spec, plan and task list are written; implementation hasn't
> started. The commands below describe the planned tool.

## Privacy and responsible use

- Your recordings stay on your computer in `%LOCALAPPDATA%\TeamsAvatar\`. The tool makes no
  network connections.
- Use it only with your own likeness.
- If you present a recording with the banner off, you are responsible for telling meeting
  participants and for following your organisation's and meeting hosts' rules.

## Requirements

- Windows 11
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- [OBS Studio](https://obsproject.com/) installed (it provides OBS Virtual Camera). OBS doesn't
  need to be running, and its own virtual camera must be off.
- A webcam for recording, and Teams or Zoom desktop

## Usage (planned)

```powershell
uv sync                                        # install dependencies
uv run teams-avatar cameras                    # list webcams
uv run teams-avatar record --name "Morning"    # record; Space to save, Esc to cancel
uv run teams-avatar import C:\clips\me.mp4 --name "Phone clip"
uv run teams-avatar list                       # show your recordings
uv run teams-avatar present "Morning"          # start playing it through the virtual camera
uv run teams-avatar status
uv run teams-avatar indicator on               # show the "RECORDED VIDEO" banner
uv run teams-avatar stop                       # back to the placeholder
uv run teams-avatar shutdown                   # release the virtual camera
```

Then choose **OBS Virtual Camera** as the camera in Teams or Zoom.

The full command list, options and exit codes are in
[contracts/cli.md](specs/001-recorded-video-camera/contracts/cli.md).

## Development

```powershell
uv run pytest                                  # tests (no camera or OBS needed)
$env:TEAMS_AVATAR_HW = "1"; uv run pytest      # also run camera/OBS hardware tests
uv run mypy src tests
uv run ruff check . ; uv run ruff format --check .
```

The project follows the Spec Kit flow (specify → plan → tasks → implement), and the
[constitution](.specify/memory/constitution.md) sets the ground rules, including test-first
development. Design documents for the first feature:

- [Spec](specs/001-recorded-video-camera/spec.md)
- [Plan](specs/001-recorded-video-camera/plan.md) and
  [research decisions](specs/001-recorded-video-camera/research.md)
- [Tasks](specs/001-recorded-video-camera/tasks.md)
- [Quickstart](specs/001-recorded-video-camera/quickstart.md) and
  [testing guide](specs/001-recorded-video-camera/testing-guide.md)
