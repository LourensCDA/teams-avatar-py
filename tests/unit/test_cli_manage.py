"""US3 CLI: list, preview, rename, delete, default (contracts/cli.md)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from teams_avatar import cli
from teams_avatar.app import App
from teams_avatar.paths import DataPaths
from tests.conftest import ClipFactory
from tests.fakes import FakeClock, FakeControlChannel, FakeLauncher, FakePlaybackUI
from tests.unit.test_app_present import FakeService


class TtyIn(io.StringIO):
    def isatty(self) -> bool:
        return True


class Env:
    def __init__(self, data_home: Path, tmp_path: Path, make_clip: ClipFactory) -> None:
        self.paths = DataPaths.resolve().ensure()
        self.service = FakeService()
        self.channel = FakeControlChannel(self.service)
        self.clock = FakeClock()
        self.clip = make_clip(tmp_path / "clip.mp4", 180, 640, 360, distinct=False)
        self.short = make_clip(tmp_path / "short.mp4", 60, 320, 180)

    def app(self) -> App:
        return App(
            self.paths,
            control=self.channel,
            launcher=FakeLauncher(self.channel),
            clock=self.clock,
            sleep=self.clock.advance,
        )

    def run(self, *argv: str, stdin: io.StringIO | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            [*argv, "--accept-notice"],
            stdin=stdin or io.StringIO(""),
            stdout=out,
            stderr=err,
            app_factory=self.app,
        )
        return code, out.getvalue(), err.getvalue()


@pytest.fixture
def env(data_home: Path, tmp_path: Path, make_clip: ClipFactory) -> Env:
    return Env(data_home, tmp_path, make_clip)


def test_list_empty_hint(env: Env) -> None:
    code, out, _ = env.run("list")
    assert code == 0
    assert "record" in out and "import" in out


def test_list_table(env: Env) -> None:
    env.run("import", str(env.clip), "--name", "Office")
    env.run("import", str(env.short), "--name", "Quick")
    env.run("default", "Office")
    env.run("present", "Quick")
    code, out, _ = env.run("list")
    assert code == 0
    header, *rows = out.strip().splitlines()
    for column in ("Name", "Duration", "Added", "Size", "Picture", "Origin", "Notes"):
        assert column in header
    office = next(r for r in rows if "Office" in r)
    quick = next(r for r in rows if "Quick" in r)
    assert "0:00:06" in office and "640x360" in office and "imported" in office
    assert "default" in office
    assert "presenting" in quick and "too short" in quick
    data = json.loads(env.run("list", "--json")[1])
    names = {r["recording"]["name"]: r for r in data["recordings"]}
    assert names["Quick"]["presenting"] is True and names["Office"]["missing"] is False


def test_list_marks_missing(env: Env) -> None:
    env.run("import", str(env.clip), "--name", "Office")
    for f in env.paths.recordings.glob("*.mp4"):
        f.unlink()
    _, out, _ = env.run("list")
    assert "missing" in out


def test_rename_and_default(env: Env) -> None:
    env.run("import", str(env.clip), "--name", "Office")
    code, out, _ = env.run("rename", "Office", "Office blue shirt")
    assert code == 0 and "Office blue shirt" in out
    code, out, _ = env.run("default", "office blue shirt")
    assert code == 0 and "default" in out.lower()


def test_delete_confirms(env: Env) -> None:
    env.run("import", str(env.clip), "--name", "Office")
    code, out, _ = env.run("delete", "Office", stdin=TtyIn("n\n"))
    assert code == 3 and "[y/N]" in out
    assert env.run("list", "--json")[1].count("Office") == 1
    code, out, _ = env.run("delete", "Office", stdin=TtyIn("y\n"))
    assert code == 0 and "Deleted" in out
    env.run("import", str(env.clip), "--name", "Again")
    code, _, _ = env.run("delete", "Again", "--yes")
    assert code == 0


def test_delete_non_interactive_needs_yes(env: Env) -> None:
    env.run("import", str(env.clip), "--name", "Office")
    code, _, err = env.run("delete", "Office")
    assert code == 3 and "--yes" in err


def test_delete_refused_while_presenting_matches_contract(env: Env) -> None:
    env.run("import", str(env.clip), "--name", "Office blue shirt")
    env.run("present", "Office blue shirt")
    code, _, err = env.run("delete", "Office blue shirt", "--yes")
    assert code == 3
    assert err == (
        'error: "Office blue shirt" is being presented. Run "teams-avatar stop" first,'
        " then delete it.\n"
    )
    code, _, _ = env.run("rename", "Office blue shirt", "Other")
    assert code == 3


def test_preview(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.run("import", str(env.clip), "--name", "Office")
    ui = FakePlaybackUI(esc_after=10)
    monkeypatch.setattr(cli, "make_playback_ui", lambda: ui)
    code, _, _ = env.run("preview", "Office")
    assert code == 0 and len(ui.frames) == 10 and ui.closed_called
    assert env.channel.requests == []


def test_not_found_exit_1(env: Env) -> None:
    code, _, err = env.run("rename", "nope", "x")
    assert code == 1 and err.startswith('error: No recording matches "nope".')
