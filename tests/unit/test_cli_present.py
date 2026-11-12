from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from teams_avatar import cli
from teams_avatar.app import App
from teams_avatar.paths import DataPaths
from tests.conftest import ClipFactory
from tests.fakes import FakeClock, FakeControlChannel, FakeLauncher
from tests.unit.test_app_present import FakeService


class Env:
    def __init__(self, data_home: Path) -> None:
        self.paths = DataPaths.resolve().ensure()
        self.service = FakeService()
        self.channel = FakeControlChannel(self.service)
        self.launcher = FakeLauncher(self.channel)
        self.clock = FakeClock()

    def app(self) -> App:
        return App(
            self.paths,
            control=self.channel,
            launcher=self.launcher,
            clock=self.clock,
            sleep=self.clock.advance,
        )

    def run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            [*argv, "--accept-notice"],
            stdin=io.StringIO(""),
            stdout=out,
            stderr=err,
            app_factory=self.app,
        )
        return code, out.getvalue(), err.getvalue()


@pytest.fixture
def env(data_home: Path, tmp_path: Path, make_clip: ClipFactory) -> Env:
    e = Env(data_home)
    make_clip(tmp_path / "clip.mp4", 180, 640, 360, distinct=False)
    return e


def test_import_prints_details(env: Env, tmp_path: Path) -> None:
    code, out, _ = env.run("import", str(tmp_path / "clip.mp4"), "--name", "Office")
    assert code == 0
    assert out.startswith('Imported "Office"')
    assert "640x360" in out and "0:06" in out


def test_import_rejected_exit_1(env: Env, tmp_path: Path) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_text("x", encoding="utf-8")
    code, _, err = env.run("import", str(bad))
    assert code == 1 and err.startswith('error: "bad.mp4" was not imported')


def test_import_name_clash_exit_3(env: Env, tmp_path: Path) -> None:
    env.run("import", str(tmp_path / "clip.mp4"), "--name", "Office")
    code, _, _ = env.run("import", str(tmp_path / "clip.mp4"), "--name", "office")
    assert code == 3


def test_present_output_matches_contract(env: Env, tmp_path: Path) -> None:
    env.run("import", str(tmp_path / "clip.mp4"), "--name", "Office blue shirt")
    code, out, _ = env.run("present", "Office blue shirt")
    assert code == 0
    assert out.splitlines() == [
        'Presenting "Office blue shirt" (recording 640x360) on OBS Virtual Camera, canvas'
        " 1280x720 at 30 fps.",
        "Recording indicator: OFF",
        'In Teams or Zoom, choose "OBS Virtual Camera" as your camera.',
        "If it is not listed, close and reopen Teams or Zoom, or reopen its camera settings.",
        'Keep the "Teams Avatar" window open during meetings; closing it stops the camera.',
    ]


def test_present_other_refused_exit_3(env: Env, tmp_path: Path) -> None:
    env.run("import", str(tmp_path / "clip.mp4"), "--name", "A")
    env.run("import", str(tmp_path / "clip.mp4"), "--name", "B")
    env.run("present", "A")
    code, _, err = env.run("present", "B")
    assert code == 3 and '"A" is already being presented' in err


def test_present_no_default_exit_1(env: Env) -> None:
    code, _, err = env.run("present")
    assert code == 1 and "no default" in err.lower()


def test_virtual_camera_missing_exit_4(env: Env, tmp_path: Path) -> None:
    env.run("import", str(tmp_path / "clip.mp4"), "--name", "A")
    env.service.error = "virtual_camera_not_installed"
    code, _, err = env.run("present", "A")
    assert code == 4 and "OBS" in err


def test_status_outputs(env: Env, tmp_path: Path) -> None:
    code, out, _ = env.run("status")
    assert code == 0
    assert "not running" in out and "Recording indicator: OFF" in out
    env.run("import", str(tmp_path / "clip.mp4"), "--name", "Office blue shirt")
    env.run("present", "Office blue shirt")
    env.service.status["elapsed_s"] = 761.4
    code, out, _ = env.run("status")
    assert out.strip() == (
        'Presenting "Office blue shirt" for 0:12:41. Canvas 1280x720 (quality 720p).'
        " Recording indicator: OFF"
    )
    code, out, _ = env.run("status", "--json")
    assert json.loads(out)["recording_name"] == "Office blue shirt"


def test_stop(env: Env, tmp_path: Path) -> None:
    code, out, _ = env.run("stop")
    assert code == 0 and "Not presenting" in out
    env.run("import", str(tmp_path / "clip.mp4"), "--name", "A")
    env.run("present", "A")
    code, out, _ = env.run("stop")
    assert code == 0 and "Camera paused" in out


def test_indicator(env: Env) -> None:
    code, out, _ = env.run("indicator", "on")
    assert code == 0 and out.strip() == "Recording indicator: ON"
    assert env.run("indicator", "maybe")[0] == 2


def test_quality(env: Env, tmp_path: Path) -> None:
    code, out, _ = env.run("quality", "1080p")
    assert code == 0 and "1080p" in out and "shutdown" not in out
    env.run("import", str(tmp_path / "clip.mp4"), "--name", "A")
    env.run("present", "A")
    code, out, _ = env.run("quality", "720p")
    assert 'Run "teams-avatar shutdown", then "teams-avatar present"' in out


def test_shutdown(env: Env) -> None:
    code, out, _ = env.run("shutdown")
    assert code == 0 and out.strip()


def test_service_command_hidden_and_exempt() -> None:
    assert cli.COMMANDS["service"].needs_notice is False
    assert "service" not in cli.build_parser().format_help()
