# Teams Avatar Constitution

## Core Principles

### I. Security, Privacy & Least Privilege

- Recordings of the user are confidential personal data. They MUST be stored only on the user's
  own computer and MUST NOT be sent to any other service. The tool MUST NOT make network
  connections unless a feature spec names the destination, the data sent, and the reason.
- The tool MUST request only the device and system access a shipped feature needs (camera,
  storage, installing a camera component). Each is listed in the feature's plan with the reason
  it is required; elevated rights MUST be limited to the one-time step that needs them.
- The live camera MUST be accessed only while the user is recording or previewing, and MUST NOT
  reach a meeting through the tool.
- Disclosure: the tool MUST provide a visible indicator that the presented video is a recording.
  The indicator MAY be off by default. Whether it is on MUST be visible to the user at all times
  while presenting, and at first use the tool MUST state that, with the indicator off, the user is
  responsible for telling participants and for complying with their organisation's and meeting
  hosts' rules.
- The tool MUST be used only with the user's own likeness. Features that present another person's
  likeness, or generate a likeness, require that person's documented consent and a constitution
  amendment.
- Secrets, if any are ever introduced, MUST NOT be committed, logged, or embedded in test
  fixtures.
- Files and settings read from disk MUST be validated before use; an unreadable or unexpected
  file is rejected, not partly processed.

Rationale: the tool handles video of a person and changes what others see of them in a meeting;
keeping the data local and the disclosure choice explicit keeps both under the user's control.

### II. Test-First (NON-NEGOTIABLE)

- For every behaviour change, a test MUST be written and observed to fail before the
  implementation is written (red → green → refactor).
- Unit tests MUST run without a physical camera, an installed camera component, a meeting
  application, or network access; these are replaced by fakes at the module boundary.
- Each integration boundary (camera capture, recording storage, virtual camera output) MUST have
  contract tests covering the success path and at least one failure path.
- A change is not ready for review while any test fails or is skipped without a recorded reason.

Rationale: camera and meeting behaviour is hard to reproduce manually; tests are the only
repeatable evidence that the tool does what the spec says.

### III. Simplicity & Typed Python

- Build only what the current spec requires. Abstractions, configuration options and dependencies
  added for anticipated needs MUST be justified in the plan's complexity tracking or removed.
- All function signatures MUST carry type annotations, and the configured type checker and linter
  MUST pass with no new suppressions lacking a stated reason.
- Every non-trivial function MUST have a docstring (imperative first line; `Args`, `Returns`,
  `Raises` where not obvious). Comments explain why, never restate what the code does.
- New runtime dependencies MUST be pinned and named in the plan with the reason a standard-library
  or existing dependency does not suffice.

Rationale: a small, typed codebase stays reviewable and keeps real-time video code debuggable.

### IV. Observability & Safe Operations

- Logging MUST be structured and carry a correlation identifier per recording or presentation
  session. Logs MUST NOT contain picture content or secrets.
- Operations that change state outside the process (installing or removing the camera component,
  deleting recordings, starting presentation) MUST be idempotent on retry. Destructive ones MUST
  require confirmation or support a dry-run mode.
- Failures MUST be explicit: errors are raised or logged with context, never silently swallowed.
  On an unrecoverable error while presenting, the tool MUST stop output or show a static frame; it
  MUST NOT fall back to the live camera.
- Camera and device operations MUST have timeouts so the tool never hangs holding a device.

Rationale: the tool acts in front of people in real time; the user needs to know what it is
showing and be able to stop or re-run it without side effects.

## Technical Constraints

- Language: Python. The supported Python version, linter, type checker, and camera/video libraries
  are fixed by the first feature plan and recorded there; once chosen they apply repo-wide.
- Target platform: Windows desktop. Other platforms require a feature spec.
- Test runner: pytest.
- Configuration is stored per user on the local computer; a committed example lists the settings
  and their defaults.
- Dependencies are declared in `pyproject.toml` with a committed lock file.

## Development Workflow & Quality Gates

- No direct commits to `main`. Feature work uses `feature/<slug>` branches, fixes use
  `fix/<slug>`; changes reach `main` through a pull request.
- Work follows the Spec Kit flow: specify → plan → tasks → implement. Each plan MUST include a
  Constitution Check against the four principles; violations are either fixed or recorded with a
  justification in the plan's complexity tracking.
- Gates before a pull request is opened: tests pass, type check passes, lint passes, and a
  read-only code review of the diff has been run with blocker/high findings resolved or reported.
- Pull requests include a short summary and a test plan checklist.

## Governance

- This constitution supersedes other project practices where they conflict.
- Amendments are made by editing this file through a pull request that states the change, the
  reason, and any migration needed for existing specs, plans or code.
- Versioning follows semantic versioning: MAJOR for removing or redefining a principle or
  governance rule, MINOR for adding a principle or section or materially expanding guidance,
  PATCH for clarifications and wording fixes.
- Compliance is checked at plan time (Constitution Check) and at review time; reviewers MUST flag
  any unjustified deviation as a blocking finding.

**Version**: 2.0.0 | **Ratified**: 2026-11-11 | **Last Amended**: 2026-11-11
