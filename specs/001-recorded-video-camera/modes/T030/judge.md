# T030 judge verdict: frames.py

**Pick: candidate B**

B has the best indicator for a meeting participant. Its white text is the largest of the three, sits centred in the banner, has a red "rec" dot and the darkest banner, and stays clearly legible over the busy gradient at both 720p and 1080p. Its placeholder is a calm, compact, well-centred group.
It also has the cleanest code: the banner layout is cached and the ROI is dimmed with `convertScaleAbs`, so it is the fastest at 1.33 ms. Its size rules are correct (0 violations over a 570x363 size sweep; 2880x1620, 3840x2160 and portrait 2160x3840 all cap exactly).

## Why not A or C

- **A**: At 1080p the gap between the icon and "Camera paused" is very large, so the group looks like two unrelated items. The banner text sits low (about 4 px below it vs about 10 px above at 720p), and the banner is the lightest of the three over bright backgrounds. It is also the slowest (1.78 ms, because of the float64 blend and a `getTextSize` call on every frame).
- **C**: It has the most recognisable video-camera-off icon. But the banner caption is the smallest, the red dot sits above the text's centre line and crowds the "R", and the light-grey text (235) is a little weaker than pure white.

## Defects in B to fix before adoption

1. **Placeholder icon (visual)**: A rectangle with a circle inside reads as a slashed photo or picture rather than a video camera. Add the lens triangle on the right, as A and C do (keep the group centred). Consider the conventional top-left to bottom-right slash. B's slash runs bottom-left to top-right.
2. **Cache can still be corrupted (all three candidates)**: `_build_placeholder` returns the array that owns its data, so a caller can run `p.flags.writeable = True` and write into the cached frame. I checked this. Fix: set the owner read-only and return `img.view()`. Setting writeable on a view of a read-only base raises `ValueError` (also checked). Add a unit test for it.
3. **Minor**: `draw_indicator` calls `cv2.getTextSize` on every frame. Move the text height into `_indicator_layout`'s cached tuple. `_indicator_layout` has no minimum `banner_h` (A has `max(16, ...)`), so very small frames get an unreadable banner. Add a floor or document the minimum supported height.
