# Data Model: Recorded Video Camera

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Research**: [research.md](research.md)

All data is stored per user under the data folder (`%LOCALAPPDATA%\TeamsAvatar\`, or
`TEAMS_AVATAR_HOME` when set). Layout, locking, `schema_version` and startup reconciliation
are in [research.md](research.md) R8.

## Recording

A stored video of the user. Persisted as one entry in `library.json` plus one video file.

| Field | Type | Rules |
|-------|------|-------|
| `id` | string (UUID4 hex) | Generated; never reused. Names the file `recordings/<id>.mp4`. |
| `name` | string | 1–64 characters; letters, digits, spaces, `-`, `_`, `.`; trimmed. Unique case-insensitively across the library. |
| `origin` | enum `recorded` \| `imported` | Set at creation; never changes. |
| `created_at` | datetime (UTC, ISO 8601) | Time recording finished or import completed. |
| `duration_s` | float | From the stored file; > 0. |
| `frame_count` | int | From the stored file; ≥ 1. |
| `width`, `height` | int | Stored picture size, even numbers. Longer side ≤ 1920 and shorter side ≤ 1080 (FR-022), so portrait video keeps its orientation. |
| `size_bytes` | int | File size on disk. |
| `is_default` | bool | At most one recording has `true`. |
| `warnings` | list of enum `too_short` \| `loop_ghosting` | Computed at save/import (FR-015); shown in `list` and when presenting. |
| `source_name` | string or null | Imported only: original file name (not full path) for the user's reference. |

**Validation**:
- Default name when none given: `Recording YYYY-MM-DD HH-MM-SS` (local time). Orphaned video
  files found at startup are added as `Recovered YYYY-MM-DD HH-MM-SS` with `origin=imported`.
- Name clash on record or import: the operation stops and asks for another name; nothing is
  replaced (spec edge case).
- `too_short` when `duration_s < 5.0`; `loop_ghosting` when first/last frame difference exceeds
  the threshold in research R5.
- Library entries whose file is missing or unreadable are reported by `list` as `missing` and
  cannot be presented (spec edge case); they can still be deleted.

**State**: a recording is either `available` or `presenting` (derived from the camera service).
Delete and rename are refused while `presenting` (FR-013; rename refused for the same reason, so
`status` stays truthful).

## Settings

Persisted in `settings.json`; one per user.

| Field | Type | Default | Rules |
|-------|------|---------|-------|
| `indicator_on` | bool | `false` | FR-017. Applies immediately to a running presentation. |
| `presentation_quality` | enum `720p` \| `1080p` | `720p` | FR-023. Sets the canvas size; takes effect when the camera service next starts. |
| `first_use_acknowledged` | bool | `false` | Set after the first-use notice (Principle I disclosure responsibility) is shown. |

The default recording is held on the Recording (`is_default`), not in settings, so deleting it
clears the default in the same write.

## Recording Session (transient)

One attempt to capture from a real camera. Not persisted beyond its activity record.

| Field | Type | Rules |
|-------|------|-------|
| `session_id` | string (UUID4 hex) | Correlation id for logs (Principle IV). |
| `camera_name` | string | Must be a physical camera from the device list; "OBS Virtual Camera" is rejected. |
| `started_at` | datetime (UTC) | |
| `outcome` | enum `saved` \| `cancelled` \| `failed` | |
| `failure_reason` | string or null | Plain-language cause (FR-014). |

**Transitions**: `starting → recording → (saved | cancelled | failed)`; `starting → failed` when
the camera is unavailable or in use (no file is created).

## Presentation Session (held by the camera service)

| Field | Type | Rules |
|-------|------|-------|
| `session_id` | string (UUID4 hex) | Correlation id for logs. |
| `recording_id` | string | Recording being presented. |
| `started_at` | datetime (UTC) | Used for "presenting for how long" (FR-009). |
| `canvas_width`, `canvas_height` | int | Fixed for the service run, from the quality setting (research R6). |
| `ended_at` | datetime or null | |
| `end_reason` | enum `stopped` \| `failed` \| `service_shutdown` or null | |

**Camera service states**:

```text
             present(rec)               stop / failure
 [not running] ──start──▶ [idle: placeholder] ──present──▶ [presenting] ──▶ [idle: placeholder]
       ▲                        │                               │
       └──── shutdown ──────────┴──────── shutdown ─────────────┘
```

- At most one Presentation Session exists (FR-010). `present` for the recording already
  presenting returns the current status; for a different recording it is refused with the name
  of the running one.
- On a failure while presenting (file unreadable mid-play, decode error, or no decoded frame for
  more than 200 ms) the service records `end_reason=failed` and returns to `idle`, showing the
  placeholder (FR-020, Principle IV).
- The service never opens a real camera (FR-011).

## Activity Record

Appended to `activity.jsonl`, one JSON object per line (FR-016). Contains no picture content.

| Field | Type | Rules |
|-------|------|-------|
| `at` | datetime (UTC) | |
| `event` | enum `recorded` \| `imported` \| `presented` \| `presentation_ended` \| `renamed` \| `deleted` \| `record_failed` \| `import_rejected` | |
| `recording_id` | string or null | |
| `recording_name` | string or null | Name at the time of the event. |
| `session_id` | string or null | Recording or presentation session. |
| `outcome` | string | e.g. `saved`, `cancelled`, `stopped`, `failed`. |
| `detail` | string or null | Plain-language reason for failures and rejections. |

## Relationships

- Recording 1 — 0..* Presentation Session (over time; at most one active).
- Recording Session 0..1 — 1 Recording (a saved session creates one recording).
- Activity Record references Recording and session ids by value; entries survive deletion of the
  recording they describe.
