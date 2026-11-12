import argparse
import io
import json
from pathlib import Path
from typing import Any

import pytest

from teams_avatar import cli
from teams_avatar.app import App
from teams_avatar.errors import NameClashError
from teams_avatar.paths import DataPaths


class TtyIn(io.StringIO):
    def isatty(self) -> bool:
        return True


def run(argv: list[str], stdin: io.StringIO | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, stdin=stdin or io.StringIO(""), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def acknowledged() -> None:
    App(DataPaths.resolve()).acknowledge_first_use()


def test_history_text(data_home: Path) -> None:
    acknowledged()
    code, out, _ = run(["history"])
    assert code == 0
    assert "No activity" in out


def test_json_prints_one_object(data_home: Path) -> None:
    acknowledged()
    code, out, _ = run(["history", "--json", "--limit", "5"])
    assert code == 0
    assert json.loads(out) == {"records": []}


def test_unknown_command_exit_2(data_home: Path) -> None:
    code, _, _ = run(["bogus"])
    assert code == 2


def test_error_line_and_exit_code(data_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    acknowledged()

    def boom(app: App, args: argparse.Namespace) -> cli.Output:
        raise NameClashError("Office")

    monkeypatch.setitem(cli.COMMANDS, "boom", cli.Command("boom", "test", lambda p: None, boom))
    code, out, err = run(["boom"])
    assert code == 3
    assert err.startswith('error: A recording named "Office" already exists. ')
    assert err.count("\n") == 1
    code, out, err = run(["boom", "--json"])
    assert code == 3
    assert json.loads(out)["error"]["exit_code"] == 3


def test_first_use_requires_y_interactively(data_home: Path) -> None:
    code, out, _ = run(["history"], stdin=TtyIn("n\n"))
    assert code == 3
    assert "indicator" in out.lower()
    assert App(DataPaths.resolve()).first_use_notice() is not None
    code, out, _ = run(["history"], stdin=TtyIn("y\n"))
    assert code == 0
    assert App(DataPaths.resolve()).first_use_notice() is None


@pytest.mark.parametrize("argv", [["history", "--json"], ["history"]])
def test_first_use_non_interactive_fails(data_home: Path, argv: list[str]) -> None:
    code, out, err = run(argv, stdin=io.StringIO(""))
    assert code == 3
    assert "indicator" in (out + err).lower()
    assert App(DataPaths.resolve()).first_use_notice() is not None


def test_accept_notice_flag(data_home: Path) -> None:
    code, _, _ = run(["history", "--json", "--accept-notice"])
    assert code == 0
    assert App(DataPaths.resolve()).first_use_notice() is None


def test_service_command_exempt(data_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def svc(app: App, args: argparse.Namespace) -> cli.Output:
        seen.append(True)
        return cli.Output("", {})

    monkeypatch.setitem(
        cli.COMMANDS,
        "service",
        cli.Command("service", None, lambda p: None, svc, needs_notice=False),
    )
    code, _, _ = run(["service"], stdin=io.StringIO(""))
    assert code == 0 and seen
    assert App(DataPaths.resolve()).first_use_notice() is not None
