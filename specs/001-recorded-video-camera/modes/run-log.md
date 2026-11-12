# Run log

| Task | Mode | Outcome | Rounds/N | Detail |
|---|---|---|---|---|
| T001-T006 | direct | done | - | - |
| T007-T019 | direct | tests written, red confirmed | - | - |
| T030 | bestof:3 | adopted B (attempt 3) + 2 judge fixes | N=3 | modes/T030.md |
| T028 | bestof:3 | adopted B (attempt 1) + 2 high fixes | N=3 | modes/T028.md |
| T020-T027, T029, T031-T033 | direct | done; Phase 2 checkpoint 128 passed, mypy src + ruff clean | - | - |
| T034-T042 | direct | US1 tests written, red confirmed (7 collection errors + app/cli failures) | - | - |
| T046, T047 | direct | done; T039 hardware tests 4/4 PASS on real OBS | - | - |
| T044 | gauntlet | blind win round 1 vs OBS VC (ours B); 0.17 % vs 0.17 % | 1 round | modes/T044.md |
| T045 | bestof:3 | adopted C (attempt 2) + 5 judge fixes | N=3 | modes/T045.md |
| T043 | bestof:3 | adopted C (attempt 1) + 7 judge fixes (incl. adapter rotation + frame_time) | N=3 | modes/T043.md |
| T049 | bestof:3 | adopted A (attempt 1) + 5 judge fixes | N=3 | modes/T049.md |
| T051-T055 | direct | US2 tests written, red confirmed | - | - |
| T056, T058 | direct | done (T058 gained 6 unit tests for overlay/key mapping); lister lists a Kyocera scanner (Image class) on this machine -> note for testing guide | - | - |
| T059 | bestof:3 | adopted A (attempt 3) + 7 judge fixes | N=3 | modes/T059.md |
| T061-T063 | direct | US3 tests written, red confirmed (22 failing) | - | - |
| T064-T066 | direct | done; 44 US3+CLI tests pass; added ConfirmationRequiredError (exit 3) and OpenCvPlaybackUI | - | - |
| T067, T069, T072 | direct | frame_check tool + 5 tests; settings.example.json verified against defaults; no-network import scan (2 tests) + existing pipe key/non-JSON tests. Open follow-up: pipe DACL is Everyone-readable (HMAC-protected) and service.key has no explicit ACL | - | - |
| T057 | bestof:3 | adopted C (attempt 3) + 9 judge fixes; hardware 0/3 here (dark room/shutter, 7-9 fps) | N=3 | modes/T057.md |
| T060 | direct | done; cameras/record CLI + App.list_cameras/record (camera code imported lazily so presenting never loads it); 43 CLI tests pass | - | - |
| T068 | direct | README updated to the built tool (commands, first-use notice, exit codes, data folder, hardware tests, frame_check) | - | - |
| T042, T050 | direct | done: full suite 332 passed, 6 hardware skips; integration test passes with ported service | - | - |
| T070 | direct | hardware contracts 33 passed / 3 failed (camera tests: dark room, 7-10 fps, refused as designed); recorded in testing-guide section 2; re-run in normal light before sign-off | - | - |
| T048 | gauntlet | blind win round 1 vs OBS VC (ours A) after 2 gate reworks; 10 min 0.00%, CPU 0.14 core, stop max 569 ms | 1 round (+2 gate reworks) | modes/T048.md |
| T074 | direct (delegated) | testing guide finalized with real output, fixtures recipes, T048/T070 results, TS-015 ghost-tuning steps; TS-001/TS-002 marked NOT YET RUN | - | - |
| T075 | direct (review delegated) | cycle 1: 6 high, 2 medium, 2 low; fixing high+medium test-first | cycle 1 of max 2 | modes/T075.md |
| T075 | direct (review delegated) | cycle 1: 8 high/medium fixed (356 passed); cycle 2: 3 high + 7 medium follow-ons REPORTED UNRESOLVED (hard stop after two cycles) | 2 cycles | modes/T075.md |
| T075 | follow-up (user request) | all 10 cycle-2 findings fixed test-first; 376 passed, HW 35 passed, live OBS smoke OK; not re-reviewed (two-cycle limit) | - | modes/T075.md |
