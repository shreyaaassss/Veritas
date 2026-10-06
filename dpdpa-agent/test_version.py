"""Release version reporting and agent/server compatibility."""
from __future__ import annotations

import pytest

import veritas_version as vv


@pytest.fixture(autouse=True)
def fresh_version():
    vv.get_version.cache_clear()
    yield
    vv.get_version.cache_clear()


class TestServerVersion:
    def test_environment_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VERITAS_VERSION", "9.9.9")
        assert vv.get_version() == "9.9.9"

    def test_version_file_is_read(self, monkeypatch, tmp_path):
        monkeypatch.delenv("VERITAS_VERSION", raising=False)
        monkeypatch.setattr(vv.sys, "_MEIPASS", str(tmp_path), raising=False)
        (tmp_path / "VERSION").write_text("1.2.3\n")
        assert vv.get_version() == "1.2.3"

    def test_falls_back_to_dev(self, monkeypatch, tmp_path):
        monkeypatch.delenv("VERITAS_VERSION", raising=False)
        monkeypatch.setattr(vv.sys, "_MEIPASS", str(tmp_path), raising=False)   # no VERSION there
        monkeypatch.setattr(vv, "__file__", str(tmp_path / "veritas_version.py"))
        assert vv.get_version() == "dev"


class TestCompatibility:
    @pytest.mark.parametrize("version,outdated", [
        ("1.0.17", True), ("1.0.0", True), ("0.9.99", True), ("0.0.0-ci", True),
        ("1.0.18", False), ("v1.0.18", False), ("1.0.19", False), ("1.1.0", False), ("2.0.0-rc1", False),
        ("1.0.100", False),                 # numeric, not alphabetical, comparison
        ("", True), (None, True), ("   ", True),   # predates version reporting
        ("dev", False), ("main-abc123", False),    # a build from source is unknown, not outdated
    ])
    def test_outdated_flag(self, version, outdated):
        assert vv.is_agent_outdated(version) is outdated

    def test_minimum_is_a_release_number(self):
        assert vv.parse(vv.MIN_AGENT_VERSION) is not None
