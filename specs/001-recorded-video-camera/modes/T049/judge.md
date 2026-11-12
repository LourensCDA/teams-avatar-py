# T049 judge verdict

**Pick: candidate A**

A's start loop keeps polling through any `ServiceError` until the deadline, so a service that is listening but slow to answer, or a stale-key race during start, still ends in a working `present`. B and C give up on the first such error after 5 s.
All three are otherwise equivalent on the contract (idempotent retry, refusal naming the running recording, probe before start, exit-4 mapping, round-trips). B also hides service errors, and C's better reply validation is a small change to port into A.

## Evidence (scratch copies, real `ControlClient` over a real named pipe)

| Probe | A | B | C |
|---|---|---|---|
| Service listens but stays mute | 11.0 s, "did not start within 10 seconds" | 5.4 s, "did not answer in time" | 5.4 s, same as B |
| Service listens, starts serving after 5.5 s | **OK at 5.5 s** | fails at 5.1 s | fails at 5.1 s |
| Launched process dies at once | 10.0 s, "did not start" | 10.1 s | 10.1 s |
| Two `present` race (lock-guarded fake service, stale key on disk) | both OK at 0.5 s, 1 winner and 1 loser | same | same |
| `present` of a different recording after that | `AlreadyPresentingError` naming "Office" | same | same |

The 18 target tests pass and mypy strict is clean for all three. The real launcher was not run, because it would open consoles on the desktop. The three launchers are nearly identical: `CREATE_NEW_CONSOLE` plus `SW_SHOWMINNOACTIVE`, with `TEAMS_AVATAR_HOME` passed through.

Round-trips are the same in all three: 1 request for `present` when the service is running, plus 1 `status` on refusal and 1 `status` for `set_quality`.

B's specific problems:
- `status()` reports `service_running=False` when the service sends an error reply.
- `set_indicator`/`shutdown` silently ignore error replies, which matters under Principle I for indicator visibility.
- It fakes a status when the reply has none.

## Defects in A to fix before adoption

1. **10 s budget is not a hard limit** (`_start_service`, lines 239-251). Each poll can take up to 1 s (connect) plus 5 s (exchange), and polls do not shrink as the deadline nears. Measured 11 s; the worst case is about 16 s, plus up to 6 s for the `present` request that follows. Fix: stop when the time left is shorter than one poll, or let `ControlChannel.request` take a timeout capped at the time left.
2. **No fast failure when the service process dies** (`launcher.py` throws away the `Popen`). A missing module, an argparse error, or a sink failure at startup costs the full 10 s, and the next step it shows ("Run teams-avatar shutdown") is wrong. Fix: `start()` returns the process handle. If the process has exited, poll once more, because the lock loser also exits on purpose. If nothing answers, raise `ServiceError` with the exit code and the log path. Cross-check with T048: if the service exits when OBS is missing, `VirtualCameraError` (exit 4) becomes a generic exit-1 error after 10 s. The service must stay up and answer `present` with `virtual_camera_*`.
3. **Unchecked `reply["status"]`** (lines 166 and 231). An `ok` reply with no `status` dict raises `KeyError`, which shows as a raw traceback instead of a `TeamsAvatarError`. Fix: port C's `_status_from` validation.
4. **Error message dropped** (`_present_error`, line 267). For unknown codes (`bad_request`, `internal`) the service's `message` is replaced with a generic text. Fix: pass `reply["message"]` through, as B and C do.
5. **Minor:**
   - `status()` and `set_quality()` can raise `ServiceError` on a hung service, while app-api.md lists no exceptions for them. A tray front end polling `status` needs to know this, so update the contract or catch it.
   - A busy pipe (`ServiceNotRunning` from `PIPE_BUSY`) makes `set_quality` report `applies_at_next_service_start=False` while the service is in fact running.
   - The "another recording" fallback is printed inside quotes (`"another recording" is already being presented`).
   - If the service dies between the last poll and `present`, the user sees "Run teams-avatar present to start it" from inside `present`.
6. **Cross-task (T048):** the service must take `service.lock` before writing `service.key`. If a losing instance overwrites the key, every client is rejected: A then spins for 10 s, and B/C fail at once.
