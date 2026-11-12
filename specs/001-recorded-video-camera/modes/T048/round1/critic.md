# T048 round 1: critic

## Pick: A

A participant is less likely to notice that A is a loop. B hard-cuts at the repeat. A crossfades
over 1 s, so there is no single-frame jump, but the crossfade has its own visible cost (see
defects below).

## Biggest gap in B (the one not picked)

**Hard cut at the repeat point.** At capture frames 653→654 and 1553→1554 (every 900 frames,
30.0 s), the gradient band jumps from a shallow diagonal to a near-vertical one in a single frame.
Frame-to-frame change there is 26.9 mean-abs RGB (160x90), against a median of 0.22, which is
**122x** normal motion. Against the source clip it is 12.07 mean-abs YUV, against a median of
0.0997 per frame (121x). Anyone watching sees this every 30 s.
Fix: blend the clip's tail into its head at the repeat. A 1 s linear crossfade from source frame
870+m into frame m, which is what A does, takes the worst frame-to-frame step down to about 2.8x
median.

## Defects in A (the pick), for the next round

1. **The crossfade shows as a 1 s double exposure every 29 s.** Capture frames 796–825 (and
   1666–1695) fit `(1-a)*src[870+m] + a*src[m]` exactly. Alpha ramps linearly from 0.01 to 0.94
   and the fit residual is about 0.3, the same as the normal match error. At mid-fade (frame 811)
   the picture holds two half-strength gradient edges crossing in an X. Its distance from the
   nearest real source frame peaks at 3.18, against a median of 0.36 for normal frames. Per-frame
   change during the fade is 2.5–2.8x median motion. On a gradient this looks like a faint
   rotation or smear. On a real person it would be two translucent heads or hands for a second,
   which is the most likely remaining cue that the video loops. Next step: pick the loop/fade
   window where the tail and head are most alike (search the last N s against the first N s), or
   shorten the fade when they already match. Measure ghosting at mid-fade, not only the
   frame-to-frame step.
2. **Small, systematic chroma bias versus the source.** Mean signed error is U −0.39 and V −0.57
   (max abs V 6, Y 3). This looks like truncation instead of rounding somewhere in the
   RGB→YUV path. A viewer would not see it, but it is a measurable bias.
3. **The loop is 29 s, not 30 s.** Source frames 870–899 are only ever shown blended, so the last
   second of the recording never appears at full strength. This is acceptable if R5 intends it,
   but it should be documented. A short or still recording loses proportionally more.
4. Timing and colour are otherwise clean: luma PSNR versus the source is 59.0 dB, and there are
   no held or skipped source frames outside the fade.

## Measured target (≤ 1% dropped or repeated at 720p30)

| Capture | Method | Result |
|---|---|---|
| A 20 s | `uv run python -m tools.frame_check A-20s.framemd5 --seconds 20` | 601/600 received, 0 repeated, 0 dropped: **0.00%** |
| B 20 s | same on `B-20s.framemd5` | 601/600, 0 repeated, 0 dropped: **0.00%** |
| A 10 min (real target) | same, `--seconds 600` | 18001/18000, 0 repeated, 0 dropped: **0.00%** |
| A 60 s mkv | each frame matched to its nearest source frame (YUV 160x90); source-index step must be +1 | 0 holds, 0 skips outside the fade: **0.00%** |
| B 60 s mkv | same | 2 holds + 2 skips (frames 283/284: source 529 shown twice, 530 never shown; same at 305/306): **4/1800 = 0.22%** |

All captures are 1280x720 at 30 fps. Both meet the target.

A's 10-minute file has exactly 870 unique hashes. Every loop over ~20.7 repeats is
bit-identical, so A has no drift or accumulated timing error.

Limits of the method:
- framemd5 counting only sees exact consecutive duplicates and gaps in pts. A sender that skips
  a source frame produces a new, valid-looking hash, so the tool cannot see it. B's two skips in
  the mkv are only visible through content matching, and `frame_check` would report B's mkv
  events as 0.11%, not 0.22%.
- pts come from ffmpeg's 1/30 time base, so DirectShow arrival jitter is not measured.
- A 20 s window allows only 6 bad frames before it fails, and it can easily miss rare
  events: B-20s showed none, while B's 60 s capture had two.
- Capture-side duplicates cannot be told apart from sender duplicates.
- The mkvs and framemd5 files are separate recordings.

## Supporting observations

- **Loop periods.** Nearest-later-frame matching gives A a period of 870 frames (29 s, distance
  about 0.00) and B a period of 900 frames (30 s).
- **Colour fidelity.** Reference: `scratchpad/media/bar-clip.mp4` (900 frames, 30 s, 720p). I
  assumed it is the test clip, because B maps onto it with a clean +1 step. Against it, A is
  essentially exact (luma PSNR 59.0 dB). B has a constant offset of Y +1.00 and V +2.01 (luma PSNR
  45.1 dB; 50% of chroma pixels off by more than 2 in V), which makes B slightly brighter and
  warmer. Viewers are unlikely to see it, but it is a real colour shift.
- **Sharpness and noise.** Flat regions are noise-free in both (A Y std 0.00, B 0.01). A
  high-pass view amplified 25x shows only the gradient edge in both. There is no meaningful
  sharpness difference: the content has almost no detail and is nearly isoluminant (Y 96–108),
  so this clip cannot judge sharpness. A detailed test clip is needed for that.
- **Blinding was compromised.** The session scratchpad `media/` folder holds
  `bar-capture.mkv`, which has the same size (18,493,487 bytes) and time as B.mkv, and
  `ours-t048-capture.mkv`, which matches A.mkv (24,075,209 bytes). I saw this while looking for
  the source clip. The pick rests on the 122x cut versus the 2.8x fade, which does not depend on
  identity, but future rounds should not leave identifiable copies next to the blinded files.

Analysis scripts and PNGs are in
`C:\Users\LOUREN~1\AppData\Local\Temp\claude\7aae79889799a1eb\fd795c51-ddf7-4e61-9b04-d8cc5058a0f2\scratchpad\critic-T048-r1\`.
