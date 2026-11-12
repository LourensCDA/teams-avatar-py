# Teams Avatar

A Windows command-line tool that plays a recording of you through a virtual camera, so Teams or
Zoom show the recording instead of your live webcam.

You record yourself once, or import a video you already have. The tool keeps your recordings in
a local library and plays the one you pick through **OBS Virtual Camera**. The recording repeats
with a one-second crossfade so there's no visible jump. When nothing is playing, the camera shows
a neutral placeholder. You can turn on a "RECORDED VIDEO" banner so people in the meeting know
it isn't live; it's off by default.

> **Status:** first feature implemented (record, import, present, manage). Manual checks in
> Teams and Zoom are listed in the [testing guide](specs/001-recorded-video-camera/testing-guide.md).

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
- A webcam for recording (it must deliver 27+ pictures per second; in a dim room most webcams
  slow down, and `record` says so), and Teams or Zoom desktop

## Usage

```powershell
uv sync                                        # install dependencies
uv run teams-avatar cameras                    # list webcams; the first is the default
uv run teams-avatar record --name "Morning"    # record; Space/Enter saves, Esc cancels
uv run teams-avatar import C:\clips\me.mp4 --name "Phone clip"   # original is left untouched
uv run teams-avatar list                       # your recordings, with warnings and marks
uv run teams-avatar preview "Morning"          # play once in a window (Esc closes)
uv run teams-avatar default "Morning"          # used when present has no name
uv run teams-avatar present "Morning"          # start playing it through the virtual camera
uv run teams-avatar status                     # what the camera shows, for how long
uv run teams-avatar indicator on               # show the "RECORDED VIDEO" banner
uv run teams-avatar stop                       # back to the "Camera paused" placeholder
uv run teams-avatar rename "Morning" "Office"
uv run teams-avatar delete "Office"            # asks [y/N]; --yes skips the question
uv run teams-avatar quality 1080p              # applies at the next service start
uv run teams-avatar history                    # recent activity
uv run teams-avatar shutdown                   # release the virtual camera
```

Then choose **OBS Virtual Camera** as the camera in Teams or Zoom. `present` opens a minimised
"Teams Avatar" window; its title always shows what is presented and whether the banner is on.
Keep it open during meetings: closing it stops the camera.

The first command you run shows a one-time notice about disclosure; type `y` to accept (or pass
`--accept-notice` in scripts). Every command accepts `--json`. Exit codes: 0 ok, 1 failed,
2 bad usage, 3 refused by a rule, 4 OBS Virtual Camera missing or in use. The full contract is in
[contracts/cli.md](specs/001-recorded-video-camera/contracts/cli.md).

Data lives in `%LOCALAPPDATA%\TeamsAvatar\` (`library.json`, `settings.json`, `activity.jsonl`,
`recordings\`, `logs\`). Set `TEAMS_AVATAR_HOME` to use another folder.
[settings.example.json](settings.example.json) lists every setting with its default.

## Development

```powershell
uv run pytest                                  # tests (no camera or OBS needed)
$env:TEAMS_AVATAR_HW = "1"; uv run pytest      # also run camera/OBS hardware tests
uv run mypy src tests tools
uv run ruff check . ; uv run ruff format --check .
uv run python -m tools.frame_check capture.framemd5 --seconds 600   # dropped/repeated pictures
```

Hardware tests open your webcam (its light turns on) and drive OBS Virtual Camera; they skip with
"needs camera/OBS; set TEAMS_AVATAR_HW=1" otherwise.

The project follows the Spec Kit flow (specify → plan → tasks → implement), and the
[constitution](.specify/memory/constitution.md) sets the ground rules, including test-first
development. Design documents for the first feature:

- [Spec](specs/001-recorded-video-camera/spec.md)
- [Plan](specs/001-recorded-video-camera/plan.md) and
  [research decisions](specs/001-recorded-video-camera/research.md)
- [Tasks](specs/001-recorded-video-camera/tasks.md)
- [Quickstart](specs/001-recorded-video-camera/quickstart.md) and
  [testing guide](specs/001-recorded-video-camera/testing-guide.md)
