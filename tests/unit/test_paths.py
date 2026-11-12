from pathlib import Path

import pytest

from teams_avatar.paths import DataPaths


def test_default_is_localappdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEAMS_AVATAR_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert DataPaths.resolve().root == tmp_path / "TeamsAvatar"


def test_env_override(data_home: Path) -> None:
    assert DataPaths.resolve().root == data_home


def test_sub_paths(data_home: Path) -> None:
    p = DataPaths.resolve()
    assert p.library == data_home / "library.json"
    assert p.settings == data_home / "settings.json"
    assert p.activity == data_home / "activity.jsonl"
    assert p.recordings == data_home / "recordings"
    assert p.log_file == data_home / "logs" / "teams-avatar.log"
    assert p.service_key == data_home / "service.key"
    assert p.service_lock == data_home / "service.lock"
    assert p.data_lock == data_home / "data.lock"
    assert p.recording_file("abc") == data_home / "recordings" / "abc.mp4"
    assert p.partial_file("abc") == data_home / "recordings" / "abc.partial.mp4"


def test_folders_created_on_first_use(data_home: Path) -> None:
    assert not data_home.exists()
    p = DataPaths.resolve()
    p.ensure()
    assert p.recordings.is_dir()
    assert p.log_file.parent.is_dir()
