import json
from pathlib import Path

import pytest

from teams_avatar.errors import DataFileError
from teams_avatar.models import Settings
from teams_avatar.paths import DataPaths
from teams_avatar.settings import SettingsStore


@pytest.fixture
def store(data_home: Path) -> SettingsStore:
    paths = DataPaths.resolve()
    paths.ensure()
    return SettingsStore(paths)


def test_defaults(store: SettingsStore) -> None:
    s = store.load()
    assert s == Settings(
        indicator_on=False, presentation_quality="720p", first_use_acknowledged=False
    )


def test_persisted_with_schema_version(store: SettingsStore) -> None:
    store.update(indicator_on=True, presentation_quality="1080p")
    data = json.loads(store.paths.settings.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["indicator_on"] is True
    assert store.load().presentation_quality == "1080p"


@pytest.mark.parametrize(
    "content",
    [
        {"schema_version": 1, "indicator_on": "yes"},
        {"schema_version": 1, "presentation_quality": "4k"},
        {"schema_version": 3},
        "not json",
    ],
)
def test_invalid_values_refused(store: SettingsStore, content: object) -> None:
    text = content if isinstance(content, str) else json.dumps(content)
    store.paths.settings.write_text(text, encoding="utf-8")
    with pytest.raises(DataFileError):
        store.load()


def test_update_rejects_bad_quality(store: SettingsStore) -> None:
    with pytest.raises(ValueError):
        store.update(presentation_quality="4k")
