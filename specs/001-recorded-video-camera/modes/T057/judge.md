# T057 judge: `adapters/pyav_capture.py`

**Pick: candidate C.**

C is the only one whose open/timeout hand-off has no race: `abandon()` takes any published stream
under the lock and `publish()` closes late streams, so a timed-out `open()` can never strand an
open camera. A and B both leave a narrow window where a stream published just after the deadline
is never closed. All three behave the same on this real camera (10 s timeout, misleading message),
so the hand-off safety decides it.

## Setup

Each candidate went into its own scratch copy (`uv sync`). All pass the unit tests (7/7), mypy
strict and ruff. I ran them on the real "Integrated Camera" one at a time, with an instrumented
opener that logs each mode's open/close time, how many reads happened in the 1 s window, and the
mean luma.

## Measurements (real camera)

The camera itself, outside any candidate, delivers **7.6 to 8.0 fps in every mode that opens**
(1280x720, 960x540 and 640x480, all `yuyv422`). Mean luma is **13 to 16 out of 255**, so the
picture is nearly black. Either the room is dark or the privacy shutter is closed, and the exposure
priority setting is cutting the frame rate. 1080p MJPEG and 1080p raw both fail with
`[Errno 5] I/O error` in 0.03 to 0.4 s. The 1280x720 "MJPEG" rung opens, but the stream is actually
`yuyv422`. Opening a mode takes about 1.1 to 1.25 s, measuring takes 1 s and closing takes about
0.9 s, so each mode that opens costs about 3 s.

| | `open()` with default 10 s | Message | Device after timeout | `open()` with 60 s timeout | Reads in 1 s window |
|---|---|---|---|---|---|
| A | 10.00 s | "did not open within 10 s **(in use elsewhere?)**" | Opened 640x480 at 10.56 s, held until **12.09 s** | 11.83 s, "no mode reached 27 frames per second" | 7, 12, 7, 13 |
| B | 10.00 s | "did not open within 10 seconds" | Opened 640x480 at 10.20 s, **closed at once** (0 reads) | 11.69 s, "no supported mode reaches 27 fps" | 12, 8, 7 |
| C | 10.02 s | "did not open within 10 seconds" | Opened 640x480 at 10.28 s, held until **11.38 s** | 12.14 s, "no mode delivered 27 frames per second" | 8, 8, 7, 12 |

- **HW contract tests:** 0 passed, 3 failed for every candidate (about 10.7 to 11.2 s per test).
  The ladder needs about 12 s on this camera, so the honest "unusable" reason never shows up within
  10 s. The user always sees a timeout message that tells them to close other programs.
- **Camera held elsewhere:** the Windows Frame Server lets a second open share the camera. All
  modes still open, and every candidate ends in the same 10 s timeout. None of the "in use" code
  paths was reached. Unsupported modes also return EIO, so neither the errno nor the error text can
  tell "in use" apart from "mode refused".
- **Buffered frames inflate the rate:** frames queued during open come back at once (two reads at
  t=0.000, then 0.031). Counting reads gave 12 frames in the first second, while the frame
  timestamps gave 7.7 fps. All three count reads.
- **No worker threads were left running** after any run. Late streams were always closed in the end.

## Ranking

1. **Hang/leak.** C has a race-free hand-off. B closes late streams fastest and checks `abandoned`
   between reads. However, B's `open()` can set `abandoned` after the worker has published but
   before `done.set()`, which strands that stream. A has the same race through `is_alive()` and
   holds a late stream the longest (2.1 s). None of the three bounds a blocking read. FFmpeg's
   dshow `read_packet` waits `INFINITE`, and as far as I can tell C's `av.open(timeout=5.0)` is not
   honoured by dshow (medium confidence, from the FFmpeg source; I did not reproduce a stall).
2. **Mode choice and messages.** All three pick the same mode and have the same 10 s problem on
   this camera. A's "(in use elsewhere?)" is the most misleading, and A pastes the raw
   `[Errno 5] I/O error: 'video=…'` text into a user message.
3. **Low fps.** All three fail, which is correct (see below). None fail fast or name the measured
   rate.
4. **Guessing "in use" from error text.** A does no text matching, which is best here. B and C
   match text that dshow never produces (`could not run graph` goes to av_log, not into the
   exception) and stop the ladder early if anything matches.
5. **Clarity.** C is clearest. B is close. A puts the ladder thread, the error reasons and
   `BaseException` handling in one function.

## Low-light recommendation: keep failing, but fail fast and name the cause

This is a product decision for you. I recommend against silently picking the best mode and warning:

- The frames here are nearly black (luma about 14/255). A recording made from them is unusable at
  any frame rate.
- The tool has to pass as a live camera. An 8 fps clip shown on a 30 fps virtual camera and looped
  is an obvious giveaway.
- `recording.py` (T059) assumes 30 fps input (`test_recording`: 180 frames gives fps 30). Accepting
  8 fps would play back 3.75 times too fast unless recording re-times frames by wall clock. That is
  extra scope.
- The user can fix it in seconds by adding light, opening the shutter or turning off low-light
  compensation, but only if the message tells them so.

So: stop the ladder early and report something like *"it only delivers 8 frames per second, usually
because the room is too dark or the privacy shutter is closed; add light and try again"*. If dim
cameras turn out to be common, add an explicit opt-in later (`record --allow-low-fps`), with
recording re-timing frames. It should not happen automatically.

## Defects in C to fix before adoption

1. **High: the ladder runs past the 10 s budget, so the real cause is hidden** (`_run_ladder`,
   lines 247-271). Record the best measured fps on `_Attempt`. On timeout, report "only N fps" if
   any mode opened. Stop early once two opened modes both measure the same rate under 27, because
   the limit is exposure and a lower resolution will not help. Name the dark-room or shutter cause
   when mean luma is low.
2. **High: reads have no time limit** (`_PyAvStream.read`, line 97; `_measure`; `CameraSource.read`).
   A stalled camera blocks the worker inside `read()` while it holds the device, and blocks
   `record`. Add a per-read deadline, for example a reader thread with a bounded queue and
   `get(timeout=…)` that closes the container when it stalls. Remove or correct `timeout=5.0` on
   line 123, which suggests a protection that dshow probably does not give.
3. **Medium: the measurement counts buffered frames** (`_measure`, lines 273-286). Measure with
   frame timestamps (`frame.time`) across the window, or drain for about 0.3 s first. A 25 fps
   camera could otherwise pass the 27 fps gate.
4. **Medium: "in use" is guessed from error text** (`_looks_in_use`, `_IN_USE_HINTS`, lines 30
   and 127-131, and the early `return` at line 259). This code never runs on dshow (everything is
   EIO). EACCES more likely means Windows camera privacy is blocking access, and a false match stops
   the ladder. Remove it. If no mode opens at all, report "could not be opened (in use by another
   program, blocked in Windows camera privacy settings, or disconnected)".
5. **Medium: a late stream holds the device after the timeout** (measured 1.1 s). After
   `_open_device` (line 255), add `if attempt.abandoned: stream.close(); return`, and check
   `attempt.abandoned` inside the `_measure` loop, as B does.
6. **Low: `_PyAvStream.close()` (line 105) does not swallow errors from a dead device.** So
   `CameraSource.close()` is not always safe, and `abandon()` can raise a PyAV error in place of
   `CameraUnavailableError`. Wrap it the way B does.
7. **Low: `mode` can be wrong.** The 1280x720 MJPEG rung opened as `yuyv422` here, yet
   `mode.mjpeg` reports True. Check the stream's codec and treat a non-MJPEG stream on an MJPEG rung
   as a failed rung, or store the actual format.
8. **Low:** `size` returns `(0, 0)` when the camera is closed instead of raising.
9. **Outside this module, but needed for FR-014:** `CameraUnavailableError` always says "Close
   other programs using it", which is wrong for low fps and timeouts. It needs a hint that depends
   on the reason. Separately, `CameraLister` lists the "Kyocera ECOSYS M3645idn" scanner (it is in
   the Image class), so the hardware test tries to open it.

Scratch copies and probes are in
`C:\Users\LOUREN~1\AppData\Local\Temp\claude\7aae79889799a1eb\fd795c51-ddf7-4e61-9b04-d8cc5058a0f2\scratchpad\judge-T057\`
(`A\`, `B\`, `C\`, `probes\`).
