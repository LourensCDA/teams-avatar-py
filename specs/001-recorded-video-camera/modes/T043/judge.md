# T043 judge verdict: `src/teams_avatar/importer.py`

**Pick: candidate C.**

Reason: All three handle every real-world input and every Ctrl+C point the same way. C is the only one
that checks the default name for a clash before encoding, gives a clean rejection reason ("the file has
no video track" instead of "The video cannot be read: the file has no video track"), and does not crash
on very short sources.
It is also the clearest of the three: it resizes only when the source frame changes, uses one try block
for the encode and rename and another for the library entry, and has no dead code.

## How it was judged

The repo was copied to `scratchpad/judge-T043/repo` and `uv sync` run there. Each candidate was loaded
as a module and run against the same files. Counter clips (`tests/clips.counter_frame`) let us read the
source frame index back from every output frame and compare it with the ideal frame: the latest source
frame with pts <= k/30.

| Input | A | B | C |
|---|---|---|---|
| 25 / 29.97 / 60 / 24 fps CFR, B-frames on (x264 medium) | exact (0 frame error, 10.0 s) | exact | exact |
| 24 fps + AAC audio | audio dropped, exact | same | same |
| odd 641x361 (yuv444p), tiny 99x55 | 640x360 / 98x54 | same | same |
| portrait 1080x1920; portrait 2160x3840 | kept / capped to 1080x1920 | same | same |
| VFR jitter (30 fps +-12 ms) | max 55 ms off, mean 18 ms | same | same |
| VFR step (5 s at 30 fps, then 5 s at 10 fps) | max **2.37 s** off mid-clip, 9.8 s vs 10.0 s | same | same |
| rotated phone clip (display matrix 90 deg) | rotation ignored (sideways) | same | same |
| one frame at 60 fps | 1-frame recording | **raw `FileNotFoundError`** (total = round(0.5) = 0, no file to rename) | 1-frame recording |
| garbage bytes mid-stream / faststart truncated at 60% | rejected, clean, 1 `import_rejected` | same | same |
| text file, audio-only .m4a, .jpg, missing file | rejected, clean | same | same |
| Ctrl+C in encode / decode / after rename / in `library.add` | no partial file, library unchanged, no record | same | same |
| writer raises `UnreadableVideoError` (disk full) | rejected as a bad source | same | same |
| writer raises `OSError` | raw `OSError`, cleaned up | same | same |
| default name already used, 60 s 1080p source | `NameClashError` **after 25.8 s of encoding** | after 25.5 s | **0.0 s, before encoding** |
| recordings folder missing (paths not `ensure()`d) | `FileNotFoundError` | creates it | `FileNotFoundError` |
| rejection text | `"x" was not imported: The video cannot be read: ...` | same, plus the intended name in the record | `"x" was not imported: <reason>` |

Speed was the same within noise: 1080p30 60 s took 25.7 / 25.8 / 25.5 s, 1080p60 60 s took 31.8 /
33.5 / 32.6 s, and 2160x3840 portrait 20 s took 18.6 / 17.5 / 17.9 s. The builders' 41 / 74 / 82 s figures came
from their machines, not from the code. Decode and libx264 encode take nearly all the time, and all
three share them through the adapter. Resizing differs only on sources that need scaling: B resizes
every decoded source frame (twice per output frame at 60 fps), A resizes every output frame (again on
repeated frames when upsampling), and C resizes once per source frame it actually uses.

## VFR and the adapter's lack of per-frame timestamps

`PyAVReader` gives only `fps` (`average_rate`) and frame order, so all three candidates assume frame i
is at i / avg_fps. This does limit VFR correctness:

- **CFR at any rate (25, 29.97, 60): no effect.** We measured 0 frame error.
- **Jittery VFR (webcams, OBS and screen recorders, most phone clips in steady light): small.** We
  measured 55 ms at most, under 2 frames, which nobody will see in a looped head-and-shoulders clip.
- **Step VFR (a phone dropping from 30 to 15 fps in low light, or edited or concatenated clips):
  visible.** In our test, motion ran up to 2.4 s ahead of or behind real time in the middle of the
  clip, so one half plays fast and the other slow. The overall duration is close (within about one
  source frame interval). With no audio, the only symptom is speeded-up or slowed blinks and gestures.

It matters a little for this product. The fix belongs in the adapter, not the importer.
`PyAVReader` already holds the sorted pts list (`_pts`), so a `frame_time(index)` accessor, or
returning the time with each frame, would let the importer pick frames by real time with a
one-line change to the `wanted` calculation.

## Defects in C worth fixing before adoption

1. **High (shared, adapter):** C ignores display-matrix rotation. A portrait phone video stored as
   1920x1080 with rotate=90 imports sideways (we confirmed this with `rotated.mp4`). Phone videos
   are a main real-world source. The fix is to read the rotation side data in `PyAVReader` and
   rotate frames, swapping width and height, before `cap_size`.
2. **Medium (shared):** C blames the source for output-side failures. An encode error such as a full
   disk becomes `ImportRejectedError` ("Choose a playable video file") with an `import_rejected`
   record. An `OSError` from the writer, or a `PermissionError` from `os.replace` (antivirus or the
   indexer holding the new file on Windows), escapes raw with no activity record. Writer and rename
   failures should map to a failure such as `StorageFullError` or a generic failed error, not to
   "the file was not imported because it is bad".
3. **Medium (shared, adapter):** C picks frames by index and average rate, not by time (see the VFR
   section above).
4. **Low:** C does not turn an `UnreadableVideoError` from `probe(final)` or
   `analysis.compute_warnings(final)` into `ImportRejectedError` or write an `import_rejected` record.
   It escapes as a plain `UnreadableVideoError`; A and B do handle this case. The file is cleaned up
   correctly; only the record and the error type are missing. The fix is to wrap the second try in the
   same `except UnreadableVideoError` path.
5. **Low:** C decides `resize` once, from the header's `width`/`height`. If the decoded frame size
   differs (a mid-stream resolution change, or a header that disagrees with the frames),
   `writer.write` raises a raw `ValueError` instead of rejecting the file. A and B check every frame's
   shape. The fix is to compare `current.shape` when a new source frame is prepared.
6. **Low:** `_plain` strips the reason with `removeprefix("The video cannot be read: ")`, which depends
   on the exact wording of `UnreadableVideoError`. Storing `self.reason` on `UnreadableVideoError`
   (as `ImportRejectedError` already does) and using that is sturdier.
7. **Low:** `shutil.disk_usage(paths.recordings)` raises `FileNotFoundError` if the folder does not
   exist yet. `App` calls `ensure()`, so this is safe today. Calling `paths.recordings.mkdir(...)`
   first, as B does, removes the dependency.
8. **Low (shared, adapter):** A `.jpg` is rejected with the confusing reason "the video ended before
   the requested frame". At 60 fps, every skipped source frame is still converted to bgr24 because the
   adapter has no grab-without-convert, which wastes about 10-20% of import time.

Not chosen:
- **A**: same behaviour as C apart from the double-prefixed rejection text, the default name not being
  clash-checked before encoding, and the extra resize on repeated frames.
- **B**: crashes with a raw `FileNotFoundError` on sources shorter than 1/60 s because it lacks
  `max(1, ...)`. It also has dead code (an "out of order" check that can never fire), resizes frames
  it then drops, and shares A's message and default-name issues. On the plus side, it creates the
  recordings folder and puts the intended name in the rejection record.
