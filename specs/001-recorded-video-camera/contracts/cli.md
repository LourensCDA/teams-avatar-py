# Contract: Command-Line Interface

**Feature**: [../spec.md](../spec.md) | **Data model**: [../data-model.md](../data-model.md)

Entry point: `teams-avatar` (console script) or `python -m teams_avatar`. Every command is a thin
wrapper over the application facade in [app-api.md](app-api.md) (FR-018, FR-019).

## Conventions

- Output is human-readable text on stdout. Every command also accepts `--json`, which prints one
  JSON object (the facade's result) instead; used by tests and a later window/tray front end.
- Errors go to stderr as one line `error: <cause>. <what to do next>` (FR-014).
- `<recording>` arguments accept a recording name (case-insensitive) or a unique id prefix of at
  least 6 characters.
- Destructive commands ask `[y/N]`; `--yes` skips the prompt (Principle IV).
- On first use of any command, the disclosure notice (Principle I) is printed once and the user
  must type `y` to continue; `settings.first_use_acknowledged` is then set. With `--json`, or
  when stdin is not a terminal, the command does not prompt: it fails with exit code 3 and the
  notice text unless `--accept-notice` is given, which acknowledges it. The hidden `service`
  command (started by `present`, never typed by the user) is exempt from this gate.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Operation failed (camera unavailable, file unreadable, storage full, service error) |
| 2 | Invalid usage (unknown command, bad arguments) |
| 3 | Refused by a rule (name clash, already presenting, recording in use, not confirmed) |
| 4 | Virtual camera unavailable: OBS not installed, or OBS Virtual Camera already in use by another program (each with its own message) |

## Commands

| Command | Arguments / options | Behaviour | FR |
|---------|--------------------|-----------|----|
| `cameras` | — | Lists physical cameras by name, in the order `record` uses for its default. | FR-002 |
| `record` | `[--name NAME] [--camera NAME]` | Opens a preview window with a red "REC" mark and elapsed time. Keys in the window: `Space`/`Enter` stop and save, `Esc` cancel. Closing the window cancels. Prints the saved recording's details and any warnings. | FR-001–FR-004, FR-015, FR-022 |
| `import` | `PATH [--name NAME]` | Validates and copies the file into the library (re-encoded per research R3). Original untouched. Prints details and warnings, or the rejection reason. | FR-021, FR-022, FR-015 |
| `list` | — | Table: name, duration, recorded/imported date, size, picture size, origin, default mark, warnings, `presenting` / `missing` marks. Empty library prints a hint to `record` or `import`. | FR-005, US3-1 |
| `preview` | `<recording>` | Plays the recording once in a window (with the loop blend shown at the end). `Esc` closes. Never touches the virtual camera. | FR-005 |
| `rename` | `<recording> NEW_NAME` | Renames. Refused while presenting or on a name clash. | FR-005 |
| `delete` | `<recording> [--yes]` | Confirms, then removes entry and file. Refused while presenting. | FR-005, FR-013, US3-2 |
| `default` | `<recording>` | Marks as default; clears the previous default. | FR-005, US3-3 |
| `present` | `[<recording>]` | Starts the camera service if needed (its own minimised window, research R4/R11), then presents the named recording or the default. Returns once the recording is showing; prints which recording, canvas size and indicator state. Same recording already presenting: prints the status and exits 0. A different recording presenting: refused (exit 3). | FR-006–FR-010, FR-023 |
| `stop` | — | Stops presentation; the camera shows the placeholder. No-op with a message if not presenting. | FR-008, FR-020 |
| `status` | — | Service running or not; quality setting and canvas size; if presenting: recording name, elapsed time (h:mm:ss), indicator on/off. | FR-009, FR-017 |
| `indicator` | `on` \| `off` | Saves the setting; applies immediately if presenting. Prints the new state. | FR-017 |
| `quality` | `720p` \| `1080p` | Saves the presentation quality. Takes effect when the camera service next starts; if it is running, says to run `shutdown` and then `present`. | FR-023 |
| `shutdown` | — | Stops presentation if running and ends the camera service, releasing the virtual camera. | FR-020 |
| `history` | `[--limit N]` | Prints the last N activity records (default 20). | FR-016 |

Scope note: `history`, `quality`, `shutdown`, `cameras` and refusing `rename` while presenting
go slightly beyond the spec's text. Each is the smallest way to make an FR usable or testable
from the command line (FR-016, FR-023, FR-020, FR-002, FR-009 respectively).

## Camera service window

While running, the service has its own minimised console window (research R11):

- Title: `Teams Avatar — PRESENTING "<name>" — indicator OFF` or `Teams Avatar — IDLE — camera paused`.
- Body: one line per state change or failure, e.g.
  `14:02:11 Presentation of "Office blue shirt" failed: the recording file can no longer be read. The camera now shows "Camera paused".`
- Closing the window ends the service (same as `shutdown`). The window's first line says
  "Keep this window open during meetings; closing it stops the camera."

## Example output

```text
> teams-avatar present
Presenting "Office blue shirt" (recording 1280x720) on OBS Virtual Camera, canvas 1280x720 at 30 fps.
Recording indicator: OFF
In Teams or Zoom, choose "OBS Virtual Camera" as your camera.
If it is not listed, close and reopen Teams or Zoom, or reopen its camera settings.
Keep the "Teams Avatar" window open during meetings; closing it stops the camera.

> teams-avatar status
Presenting "Office blue shirt" for 0:12:41. Canvas 1280x720 (quality 720p). Recording indicator: OFF

> teams-avatar delete "Office blue shirt"
error: "Office blue shirt" is being presented. Run "teams-avatar stop" first, then delete it.
```
