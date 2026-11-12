# T045 judge: control.py

**Pick: candidate C**

C is the only one whose service sheds connections that never authenticate (50 half-open clients: 102 threads at +1 s, back to 2 at +7.5 s; A and B hold 52 threads for as long as the clients stay connected). It also caps request size at 1 MiB and uses no `_winapi`/`PipeConnection` internals.
Its weak side is the client, which can hang on a mute server and waits about 20 s on a busy pipe. Those faults sit in the short-lived CLI, not in the service that has to run through a meeting, and they are cheap to fix.

## Measurements

Python 3.12.1, scratch copies, `probe.py`/`acl.py`/`busy.py` in the scratchpad `judge-T045` folder.

| Check | A | B | C |
|---|---|---|---|
| 500 sequential requests: threads / handles | 2→2 / 163→164 | 2→2 / 162→163 | 2→2 / 163→163 |
| 200 wrong-key requests, 16x30 concurrent requests | no leak, 0 errors | no leak, 0 errors | no leak, 0 errors |
| Handler raises, then next request | `internal`, loop survives | same | same |
| 50 half-open RW clients, +7.5 s | **52 threads pinned** | **52 threads pinned** | dropped (2 threads) |
| 20 read-only clients (what Everyone gets), +7.5 s | **22 threads pinned** | **22 threads pinned** | dropped |
| `stop()` (idle, before `serve`, from inside handler, with 10 half-open) | serve returns <20 ms | same | same |
| Mute server (pipe exists, no handshake) | **blocked >15 s** | `ServiceError` at 6.0 s | **blocked >15 s** |
| Server authenticates, then never replies | `ServiceError` at 5.0 s | 5.0 s | 5.0 s |
| Busy pipe (only instance taken) | `ServiceNotRunning` at 1.0 s | 1.0 s | **`ServiceError` at 20.2 s** |
| 64 MiB authenticated request | read in full | read in full | rejected (1 MiB cap) |
| Unpickling on any path | none | none | none |
| Key file write | atomic (fixed `.tmp`) | **not atomic** (`write_bytes` in place) | atomic (`mkstemp`, hex, malformed file detected) |
| Private APIs | `_winapi`, `PipeConnection(handle)`, challenge funcs | same | challenge funcs only |

All three get the same pipe DACL because they all use `Listener(family="AF_PIPE")`, which passes NULL security attributes and does not set `PIPE_REJECT_REMOTE_CLIENTS`. Measured SDDL: `D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;<user>)(A;;FR;;;WD)(A;;FR;;;AN)`. Everyone and Anonymous get read access. They cannot finish the handshake, so they cannot send commands, but they can hold a server worker thread. In A and B that thread is held forever, so any other local user or low-integrity process can exhaust the service. All three are mutually authenticated, so a squatter that takes the pipe name first never sees a request. `FILE_FLAG_FIRST_PIPE_INSTANCE` makes the service fail to start rather than share the name.

## Defects in C to fix before adoption

1. **high: the client hangs forever on a mute server** (`ControlClient._connect`, line 267: `Client(..., authkey=key)` runs `answer_challenge` with no timeout). Measured: still blocked after 15 s, both when the server never accepts and when it accepts and stays silent. This breaks "mute server → `ServiceError` after a reply timeout". It also lets a pipe squatter, or a service stuck before `serve()`, hang `status` and the T049 launcher's 10 s poll. Fix: give the handshake a deadline, either with `conn.poll(REPLY_TIMEOUT_S)` before each handshake read or with a guarded close timer. Add a contract test that uses a `Listener` which never accepts.
2. **high: the connect budget is about 20 s, not 1 s, when the pipe is busy** (lines 262-277). CPython's `PipeClient` loops on `WaitNamedPipe(1000)` for up to 20 s, so C's own `_ERROR_PIPE_BUSY` retry never runs. When the timeout does come, it is `ERROR_SEM_TIMEOUT` (121) and gets reported as a generic `ServiceError`. Measured at 20.2 s. `stop()` (line 181) has the same 20 s exposure. Fix: open the pipe with an explicit deadline as A and B do, or treat 121 as busy and never call `Client` without a time limit.
3. **medium: the watchdog closes the connection from another thread** (lines 188-209). Two problems:
   - It can race the worker's own `conn.close()`. `PipeConnection.close` reads `_handle`, calls `CloseHandle`, and only then clears it, so a double `CloseHandle` is possible. If Windows has reused the handle number by then, the close hits an unrelated handle in a service that also holds camera and file handles. This is inferred from the code, not reproduced.
   - The 5 s also covers handler time, so a handler slower than 5 s has its effect applied and its reply dropped.

   Fix: cancel the watchdog before calling the handler, and close at most once behind a lock or flag.
4. **medium: nothing limits concurrent connections, and the default DACL lets any user or low-integrity process open the pipe read-only** (same for all candidates). Each open costs C two threads (worker plus `Timer`) for 5 s, so a tight loop of read-only opens can still pile up thousands of threads. Fix: put a semaphore cap on in-flight connections, closing extras at once. Better still, create the pipe with an owner-only DACL plus `PIPE_REJECT_REMOTE_CLIENTS`, which needs `CreateNamedPipeW` through ctypes with explicit security attributes. Record that trade-off against "no private APIs" in research R4.
5. **low:** the key file gets no explicit ACL and relies on what `%LOCALAPPDATA%` passes down. A `TEAMS_AVATAR_HOME` that points at a shared folder would expose the key. Handler exceptions echo `str(exc)` to the client (line 225), which could include paths. That is acceptable for a same-user client, but keep keys out of exception text.

## Why not A or B

- **A**: same client hang as C. Server threads stay pinned by half-open or read-only clients until `stop()`, the only point where A closes tracked connections. It depends on `_winapi` and `PipeConnection(handle)`.
- **B**: it has the best client, the only one that times out on a mute server, at 6 s. But its service pins a thread per half-open or read-only client indefinitely. It writes the key file non-atomically, so a racing client can read a partial key and get a wrong-key error. It reads requests of any size. Its thread-wrapped client exchange is hard to follow, and it uses the same private APIs as A. B's client timeout pattern is worth borrowing for fix 1.
