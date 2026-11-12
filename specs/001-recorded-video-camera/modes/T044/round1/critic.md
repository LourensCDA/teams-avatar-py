# T044 round 1 — critic

**Pick: B.**

**Biggest gap in A:** A does a hard cut where the clip repeats. There is no crossfade, so the last frame of the clip jumps straight to the first one. The gradient band rotates 61.5° in one frame (frames 653→654 and again at 1553→1554, every 900 frames / 30.0 s). Its mean absolute frame difference there is 26.9, against a median of 0.23 (about 118x normal motion). Fix: apply the R5 1 s crossfade. Blend the clip's last 30 frames with its first 30 frames, linearly over 30 frames, so the pacer never shows a jump from the last frame to the first.

## Measured target (720p, 30 pictures/s, ≤ 1 % dropped or repeated)

Method: I parsed `A-20s.framemd5` / `B-20s.framemd5`. Received frames = data rows. I checked that pts steps are all 1, counted runs of consecutive identical MD5s, and counted hashes that recur after a gap.

| | A | B |
|---|---|---|
| Frames received / expected | 601 / 600 (pts 0..600, every step = 1, 1280x720 yuv420p) | 601 / 600 (same) |
| Repeated pictures (consecutive identical hashes) | 1 pair (frames 0–1 only, a capture-startup duplicate) = 1/600 = **0.17 %** | 1 pair (frames 0–1 only) = **0.17 %** |
| Dropped (missing pts) | 0 | 0 |
| Verdict | passes ≤ 1 % | passes ≤ 1 % |

Caveat: framemd5 here has CFR pts, so a source frame the producer skipped without sending a duplicate leaves no trace in the hashes. As a cross-check I ran a frame-difference scan on the 60 s .mkv captures. A frame difference below 0.15x the local median counts as a repeat, and above 1.6x counts as a skip; the repeat window was excluded.
- **A**: 2 stalls, each a repeated picture followed by a double-size step (a skipped picture). They are at frames 283/284 and 305/306 (t ≈ 9.4 s and 10.2 s). That is 2 repeated + 2 skipped in 1800 frames = **0.22 %**. Neither recurs 900 frames later, so they come from delivery, not from the clip.
- **B**: only the frame-0 startup duplicate, so **0 %** mid-stream.

## Supporting observations

1. **Repeat points.** I found them by matching frames to later frames: every frame best matches the one exactly 900 frames later in A (err ≤ 0.006) and 870 frames later in B (err ≤ 0.024). B's period is 29 s, consistent with a 30 s clip that overlaps itself by 1 s of crossfade. A's period is the full 30 s with no overlap.
2. **A's cut is a pop.** Frames 651–653 show a shallow diagonal band. Frame 654 shows a steep near-vertical band (PNGs: `fA_0653.png`, `fA_0654.png`). Nothing in between, and anyone watching would see it.
3. **B is not invisible either.** The fade runs frames 750–780 (31 frames). Per-frame difference rises to 0.54–0.79, against B's median of 0.29 (a peak of 2.7x). The band-orientation estimate jitters by 3–7° per frame because two bands are on screen at once. At mid-fade (frame 766) the frame visibly shows a double exposure, a desaturated blue-purple wedge where the outgoing and incoming bands overlap. With a real face this would be a 1 s ghost every 29 s. It is far less noticeable than A's single-frame jump, but it is still a tell.
4. **The test clip does not match the brief.** I found no translucent sliding box in either capture. Amplified frame-difference images show only the smooth rotating gradient band, and edge detection finds no sharp box edges. So I could not measure a "box-position jump", and I used band-orientation jump (structure-tensor angle) as the motion metric instead. Minor and unverified without the source: B decodes slightly noisier (high-pass std 0.218 vs 0.176) and its .mkv is 2.2x larger, and its flat blue reads R=0 vs A's R=9. Either one may be off on colour or dither, and I cannot tell which is faithful.

Scripts and frames: `C:\Users\LOUREN~1\AppData\Local\Temp\claude\7aae79889799a1eb\fd795c51-ddf7-4e61-9b04-d8cc5058a0f2\scratchpad\critic-T044-r1\` (`md5stats.py`, `diffs.py`, `series.py`, `angle.py`, `glitch.py`, PNGs `fA_*`, `fB_*`, `hB_0766.png`, `dA.png`, `dB.png`).
