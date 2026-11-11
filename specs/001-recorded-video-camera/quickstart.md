# Quickstart: Recorded Video Camera

**Feature**: [spec.md](spec.md) | **Full test steps**: [testing-guide.md](testing-guide.md)

A short run guide proving the feature works end to end. Commands follow
[contracts/cli.md](contracts/cli.md).

## Prerequisites

- Windows 11, Python 3.12, `uv`.
- OBS Studio installed (provides "OBS Virtual Camera"); OBS itself not running its virtual camera.
- A physical webcam, and Teams or Zoom desktop.

## Setup

```powershell
uv sync                      # install locked dependencies
uv run teams-avatar cameras  # expect your webcam listed, not "OBS Virtual Camera"
```

## Validate

1. **Record** (US2): `uv run teams-avatar record --name "Test 1"` → preview window opens with
   "REC"; sit still ~20 s; press `Space`. Expect: details printed, no warnings.
2. **Import** (FR-021): `uv run teams-avatar import C:\path\to\clip.mp4 --name "Phone clip"` →
   details printed; original file unchanged.
3. **List** (US3): `uv run teams-avatar list` → both recordings with duration, date, size.
4. **Present** (US1): `uv run teams-avatar present "Test 1"` → returns within 30 s and a
   minimised "Teams Avatar" window appears on the taskbar showing `PRESENTING … indicator OFF`. In Teams
   (Settings → Devices → Camera) choose "OBS Virtual Camera": you see the recording; it repeats
   without a jump. The webcam light is off.
5. **Status**: `uv run teams-avatar status` → recording name, elapsed time, `Recording indicator: OFF`.
6. **Indicator**: `uv run teams-avatar indicator on` → "RECORDED VIDEO" banner appears in Teams'
   preview; `indicator off` removes it.
7. **Stop**: `uv run teams-avatar stop` → within 2 s Teams' preview shows "Camera paused".
8. **Shutdown**: `uv run teams-avatar shutdown` → service ends; `status` reports not running.

## Automated checks

```powershell
uv run pytest
uv run mypy src tests
uv run ruff check . ; uv run ruff format --check .
```
