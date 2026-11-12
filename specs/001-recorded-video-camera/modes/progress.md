# Gauntlet progress

| Task | Round | Latest pick | Latest gap | Measured |
|---|---|---|---|---|
| T044 loop_player | 1 (done) | ours | bar hard-cuts at repeat; ours shows 1 s double exposure | 0.17 % both |

Bar: OBS Studio 32.0.4 Virtual Camera, Media Source with Loop on, same 30 s clip (gradient drift + sliding box), 720p30 profile; captured with ffmpeg dshow 60 s + 20 s framemd5. OBS config backed up and restored byte-identical afterwards.

| T048 service | 1 (done) | ours | bar hard-cuts at repeat; ours shows 1 s double exposure mid-fade | 10 min 0.00%; CPU 0.14 core; stop max 569 ms |
