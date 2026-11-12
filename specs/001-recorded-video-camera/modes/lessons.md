# Lessons (one line each, with reason)

- x264 inserts scene-cut keyframes; set `sc_threshold=0` (with `g=30`, `keyint_min=30`) wherever keyframe positions matter, or the 30-frame seek grid breaks.
- Builders keep helper scripts inside their own attempt folder, never the shared scratch root: a name collision overwrote a sibling's evidence (T030).
- Cached frames are returned as views of read-only arrays; callers copy before drawing (a writable flag can be flipped back on the base array).
- Seeks cost ~30 ms at 1080p: keep them on the decoder thread behind the read-ahead queue, never on the 33 ms pacer.
- Service-side code must bound every wait (handshakes, reads, joins) and cap per-connection threads: it runs for hours during meetings (T045 judge).
- Imported videos carry rotation metadata and variable frame rates: read display orientation and per-frame times from the adapter, never assume index/fps (T043 judge).
- Measure CPU per stage before optimising: decoder AUTO threads spun (7.7 CPU ms for 1 ms wall) and pyvirtualcam's BGR->NV12 cost 0.14-0.19 core; service logic itself was negligible (T048 r1).
- A wait budget is only real if every step inside it is capped by the time left (polls with their own 6 s timeouts broke a 10 s budget); keep process handles so a crashed child fails fast (T049 judge).
- Writing the activity record is best effort: its failure must never replace the operation's real outcome, and every outcome (incl. Ctrl+C) gets a record (FR-016; T059 judge).
- Real devices differ from the probe: measure on the actual hardware before trusting research numbers, and make error messages name the measured cause (fps, darkness) instead of a generic next step (T057 judge).
- Measurement harnesses must run in the foreground with hard timeouts and never as background loops: two hung ffmpeg loops held OBS Virtual Camera and needed the user to approve kills (T048).
- PyAV's bgr24 to_ndarray spins swscale threads (~0.2 core at 720p30); decode with thread_count=1 and convert yuv420p->BGR with OpenCV single-threaded (T048 r1).
- Keep blinded inputs blind: move or delete identifiably named source captures before a critic runs; the T048 critic found them in scratchpad/media.
