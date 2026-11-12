import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from teams_avatar.errors import DataFileError, InvalidArgumentError, NameClashError, NotFoundError
from teams_avatar.library import Library, default_name, validate_name
from teams_avatar.models import Recording
from teams_avatar.paths import DataPaths


def rec(name: str, rid: str = "", **kw: object) -> Recording:
    fields: dict[str, object] = {
        "id": rid or os.urandom(16).hex(),
        "name": name,
        "origin": "imported",
        "created_at": datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
        "duration_s": 10.0,
        "frame_count": 300,
        "width": 1280,
        "height": 720,
        "size_bytes": 1000,
    }
    fields.update(kw)
    return Recording(**fields)  # type: ignore[arg-type]


@pytest.fixture
def lib(data_home: Path) -> Library:
    paths = DataPaths.resolve()
    paths.ensure()
    return Library(paths)


@pytest.mark.parametrize("name", ["a", "Office blue shirt", "x" * 64, "v1.2_final-cut"])
def test_valid_names(name: str) -> None:
    assert validate_name(name) == name


def test_name_is_trimmed() -> None:
    assert validate_name("  Office  ") == "Office"


@pytest.mark.parametrize("name", ["", "   ", "x" * 65, "bad/name", "emoji😀", "a:b"])
def test_invalid_names(name: str) -> None:
    with pytest.raises(InvalidArgumentError):
        validate_name(name)


def test_default_name_uses_local_time() -> None:
    assert default_name(datetime(2026, 3, 4, 5, 6, 7)) == "Recording 2026-03-04 05-06-07"


def test_empty_library(lib: Library) -> None:
    assert lib.load() == []


def test_add_and_reload(lib: Library) -> None:
    r = rec("Office", warnings=["too_short"], source_name="clip.mp4")
    lib.add(r)
    loaded = lib.load()
    assert loaded == [r]


def test_names_unique_case_insensitive(lib: Library) -> None:
    lib.add(rec("Office"))
    with pytest.raises(NameClashError):
        lib.add(rec("OFFICE"))
    assert len(lib.load()) == 1


def test_lookup_by_name_or_prefix(lib: Library) -> None:
    a = rec("Office", rid="abcdef0123456789abcdef0123456789")
    b = rec("Garden", rid="abcdff0123456789abcdef0123456789")
    lib.add(a)
    lib.add(b)
    assert lib.find("office").id == a.id
    assert lib.find("abcdef").id == a.id
    with pytest.raises(NotFoundError):
        lib.find("abcd")  # prefix shorter than 6 characters
    with pytest.raises(NotFoundError):
        lib.find("nothing")


def test_ambiguous_prefix_is_not_found(lib: Library) -> None:
    lib.add(rec("A", rid="abcdef0000000000000000000000000a"))
    lib.add(rec("B", rid="abcdef0000000000000000000000000b"))
    with pytest.raises(NotFoundError):
        lib.find("abcdef")


def test_at_most_one_default(lib: Library) -> None:
    a, b = rec("A"), rec("B")
    lib.add(a)
    lib.add(b)
    lib.set_default("A")
    lib.set_default("B")
    defaults = [r.name for r in lib.load() if r.is_default]
    assert defaults == ["B"]


def test_rename_checks_clash(lib: Library) -> None:
    lib.add(rec("A"))
    lib.add(rec("B"))
    with pytest.raises(NameClashError):
        lib.rename("A", "b")
    assert lib.rename("A", "C").name == "C"
    assert lib.rename("C", "c").name == "c"  # case change of its own name is fine


def test_remove(lib: Library) -> None:
    a = rec("A")
    lib.add(a)
    removed = lib.remove(a.id)
    assert removed.name == "A"
    assert lib.load() == []


def test_schema_version_written(lib: Library) -> None:
    lib.add(rec("A"))
    data = json.loads(lib.paths.library.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["recordings"][0]["name"] == "A"


def test_atomic_save_uses_replace(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: str, dst: str) -> None:
        calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    lib.add(rec("A"))
    assert calls and calls[-1][1] == str(lib.paths.library)
    assert calls[-1][0] != calls[-1][1]


def test_unknown_schema_version_refused(lib: Library) -> None:
    lib.paths.library.write_text(json.dumps({"schema_version": 2, "recordings": []}), "utf-8")
    with pytest.raises(DataFileError, match="version"):
        lib.load()


def test_corrupt_json_refused(lib: Library) -> None:
    lib.paths.library.write_text("{not json", encoding="utf-8")
    with pytest.raises(DataFileError):
        lib.load()


def test_invalid_entry_refused_not_partly_read(lib: Library) -> None:
    good = rec("A").to_dict()
    bad = dict(good, id="b" * 32, name="B", origin="stolen")
    lib.paths.library.write_text(
        json.dumps({"schema_version": 1, "recordings": [good, bad]}), encoding="utf-8"
    )
    with pytest.raises(DataFileError):
        lib.load()


def test_add_refuses_duplicate_id(lib: Library) -> None:
    from teams_avatar.errors import DuplicateIdError

    first = rec("One", rid="f" * 32)
    lib.add(first)
    with pytest.raises(DuplicateIdError):
        lib.add(rec("Two", rid="f" * 32))
    assert [r.name for r in lib.load()] == ["One"]


def test_add_renaming_picks_next_free_name(lib: Library) -> None:
    lib.add(rec("Office"))
    lib.add(rec("Office 2"))
    saved = lib.add_renaming(rec("office"))
    assert saved.name == "office 3"
    assert [r.name for r in lib.load()][-1] == "office 3"
    assert lib.add_renaming(rec("Fresh")).name == "Fresh"


def test_add_renaming_keeps_name_within_64_chars(lib: Library) -> None:
    lib.add(rec("x" * 64))
    saved = lib.add_renaming(rec("x" * 64))
    assert saved.name == "x" * 62 + " 2"
    assert len(saved.name) == 64


def test_register_adopts_entry_recovered_under_same_id(lib: Library) -> None:
    rid = "a" * 32
    lib.add(rec("Recovered 2026-11-11 09-30-15", rid=rid))
    stored = lib.register(rec("Office", rid=rid, origin="recorded"))
    assert (stored.id, stored.name) == (rid, "Office")
    assert [(r.id, r.name) for r in lib.load()] == [(rid, "Office")]


def test_register_keeps_recovered_name_when_wanted_name_is_taken(lib: Library) -> None:
    rid = "a" * 32
    lib.add(rec("Office"))
    lib.add(rec("Recovered 1", rid=rid))
    assert lib.register(rec("office", rid=rid)).name == "Recovered 1"


def test_register_new_id_behaves_like_add_renaming(lib: Library) -> None:
    lib.add(rec("Office"))
    assert lib.register(rec("Office")).name == "Office 2"
