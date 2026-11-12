# T028 judge verdict: pyav_codec.py

**Pick: candidate B**

B indexes frames from demuxed packet pts, so frame numbers stay correct on VFR, B-frame, offset-start, webm, mov and 25/29.97 fps files. It also maps errors to the clearest plain-language reasons (missing file, permission denied, invalid or incomplete video, no video track).
A breaks on VFR because it turns timestamps into frame numbers with the nominal fps: 113 of 120 seeks are wrong on a VFR mkv. C has B's bugs plus a wrong `probe` duration on offset-start files.

## Evidence (scratch harness, PyAV 19.0.2, ffmpeg 8.1.1)

Ground truth came from a plain PyAV decode of counter-frame clips. Each seek was tested by jumping
to every index from a distant position.

| File | A | B | C |
|---|---|---|---|
| PyAV writer mp4, ffmpeg `-bf 2` mp4, `-bf 3` mov + aac, `-output_ts_offset 10` mp4/mkv, vp9 webm, 25 fps, 29.97 fps, long GOP, open GOP | ok | ok | ok |
| VFR mkv (50/20 ms gaps) | 113/120 seeks wrong | ok | ok |
| VFR mp4 (ffmpeg passthrough) | 28/120 seeks wrong | 17/120 wrong (frames 103+) | 16/120 wrong |
| Stream-copy trimmed mp4 (edit list, 15 discarded packets) | frame_count 150, should be 135; seeks ok | frame_count 150; 111/135 seeks wrong | frame_count 150; 111/135 seeks wrong |
| Errors (missing, directory, empty, truncated mp4, corrupt mid-file, audio-only) | all `UnreadableVideoError`, but the message carries raw ffmpeg text and full path | all `UnreadableVideoError`, plain reasons | all `UnreadableVideoError`; a directory/permission problem is reported as "not a video or is damaged" |
| `probe` duration, mkv with 10 s start offset | 5.0 s | 5.0 s | 15.0 s (wrong) |
| 1080p read p50 / seek(30)+read average | 2.6 / 29 ms | 2.4 / 23 ms | 2.4 / 23 ms |

## Defects in B to fix before adoption

1. **high**: the edit-list trimmed MP4/MOV gives the wrong `frame_count` and wrong seeks. Location: `_index_packets`, around lines 242-247. Evidence: a stream-copy trim (`ffmpeg -ss 0.5 -c copy`) reports 150 frames but decodes 135, and 111/135 seeks return the wrong frame. Fix: skip packets with `packet.is_discard` as well as `pts is None`. Phone "trim" exports often look like this. Confidence: high (fixed in a scratch copy).
2. **high**: B assumes the container seek lands exactly on the keyframe it asked for. Location: `_start_decoding`, around lines 258-260. Evidence: on a VFR mp4 the mov demuxer lands one keyframe earlier (frame 80 instead of 104), so every seek into the last GOPs is off by 24 frames. Fix: keep the sorted `pts_list`. Peek at the first decoded frame and set `_next = bisect_left(pts_list, first.pts)`. Then put that frame back in front of the iterator (`itertools.chain`). Confidence: high (fixed in a scratch copy).
3. **low**: a write failure in `PyAVWriter` raises `UnreadableVideoError`, which reads "The video cannot be read: encoding failed". The message is misleading for a write path. Consider a write-specific message or error.
4. **low**: `_index_packets` drops packets that have no pts. Raw elementary streams and some AVIs then report "the video contains no frames". C's fallback to `dts` is the better pattern.

With fixes 1 and 2 applied to a scratch copy of B, every seek on every test file returned the exact frame (0 wrong). `frame_count` matched the decoded count everywhere, including 135 on the trimmed file. The error mapping and timing were unchanged.

Note for all candidates: seek(30)+read at 1080p takes 23-35 ms, close to the 33 ms frame budget. This is mostly container seek, decoder flush and frame-thread warm-up. The service's 60-frame decoder read-ahead (plan, R5) must absorb it. Do not seek on the pacer thread.
