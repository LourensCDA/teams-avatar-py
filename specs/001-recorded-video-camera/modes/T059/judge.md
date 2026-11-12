# T059 judge verdict: `src/teams_avatar/recording.py`

**Pick: candidate A.**

A is the only candidate where every non-Ctrl+C write or finish failure (writer create/write/close, `os.replace`, probe, warnings, lock timeout) leaves no partial file, closes the camera and window, logs `record_failed/failed` and raises a plain `LibraryWriteError` "<cause>. <next>".
B raises raw `PermissionError`/`UnreadableVideoError` ("Use a different video file") with no activity record for any failure after capture. C has the same gaps, logs nothing on writer failures, and can leave a library entry whose file is deleted.

## How it was judged

All three passed `tests/unit/test_recording.py`; A also re-checked with mypy strict. A fault-injection harness with fakes only (scratch copy: `judge-T059/repo/judge_faults.py`) ran 27 cases per candidate. It raised an exception at camera open (unavailable, generic `TimeoutError`, Ctrl+C), first read, mid read, a frame-shape change, writer create/write (ENOSPC chained)/close, `os.replace`, probe, `compute_warnings`, `library.add` (write error, name clash race, lock timeout), activity append (on save and on failure), and a `KeyboardInterrupt` at each step.

| | A | B | C |
|---|---|---|---|
| Partial file left behind | never | never | never |
| Camera or window left open | never | never | never |
| Library entry with no file | no | no | **yes**: activity append fails or Ctrl+C after `library.add` (`_record` deletes `final`) |
| Activity record for writer and finish failures | yes, except `library.add` domain errors | **no** for replace, probe, warnings and all `library.add` failures | **no** for any writer or finish failure |
| Activity record for Ctrl+C | **no** | yes (`cancelled`, "interrupted") | no |
| Error messages | clean `LibraryWriteError` | garbled double message ("could not be saved: The video cannot be read: ... Use a different video file. Check that ..."), raw `PermissionError` | raw `UnreadableVideoError` and `PermissionError` |
| Disk full during encode becomes `StorageFullError` | no (checks `errno` only on the outer error) | yes (walks the cause chain) | no |
| Name clash-checked before camera opens | yes | yes | **no** for the default name (built at save time) |
| Work per frame | disk check every 30 frames, no per-frame logging | same | same |

## Defects in A to fix before adoption

1. **Ctrl+C leaves no activity record** (high, FR-016). Fault cases open_ki, read_ki, write_ki, close_ki, replace_ki, probe_ki and add_ki all end with no record. In `_record`, before the `except BaseException`, add `except KeyboardInterrupt:` that deletes the partial and logs `record_failed/cancelled` with detail "interrupted", then re-raises, as B does at candidate-B.py:175-178.
2. **A failure while writing the activity record replaces the real outcome** (high).
   - In the act_saved_fail case, the `TimeoutError` from `ActivityLog.append` after `library.add` reaches the CLI as a raw traceback, but the recording is saved and in the library.
   - In act_failed_fail, a lock timeout replaces the `CameraUnavailableError`.
   - Fix: wrap `_log` and the `recorded` append in `contextlib.suppress(OSError)` plus `logger.warning("activity record not written")`, as B's `_log` does at candidate-B.py:266-267.
3. **`library.add` domain errors are not logged** (medium). `LibraryWriteError`, `DataFileError` and a `NameClashError` race are not `OSError`/`UnreadableVideoError`, so `_save` (lines 237-240) removes the file but writes no `record_failed`. Also catch `TeamsAvatarError` there and log `exc.message` without wrapping it again.
4. **Disk full during encode is reported as a generic write error** (medium). `PyAVWriter` wraps ENOSPC in `UnreadableVideoError`, so `_write_error` (lines 257-258) never returns `StorageFullError`. Use B's cause-chain `_is_disk_full` (candidate-B.py:281-289).
5. **Unexpected errors are neither logged nor translated** (medium).
   - If the camera changes frame size mid-recording, `PyAVWriter.write` raises a raw `ValueError`.
   - A non-`CameraUnavailableError` from `source.open` (for example a 10 s open `TimeoutError`) escapes raw.
   - Both escape with no activity record. Map them to `CameraUnavailableError` or `LibraryWriteError` and log `failed`.
6. **The `clock` parameter is never used** (low). The test passes it, so either use it for the duration log line (as B does) or document it as reserved.
7. **The activity `detail` holds the whole message, including the next step** (low). data-model.md says `failure_reason` is the plain-language cause, so `exc.cause` fits better.

Not counted against any candidate: a Ctrl+C that lands inside `Library.add` after its write completes (one line wide) can leave an entry with no file in all three. Startup reconciliation marks it `missing`.
