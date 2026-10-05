"""
Tests for the machine fingerprint and the license fingerprint check.

The fingerprint for Linux (version 2) must:
  - be anchored on /etc/machine-id (fallback: disk serial),
  - not change with hostname or MAC address,
  - be identical in dpdpa-agent/license.py (used by the product) and
    tools/fingerprint.py (used to collect a fingerprint for licensing).
"""
from __future__ import annotations

import hashlib
import importlib.util
import platform
import socket
import sys
import uuid
from pathlib import Path

import pytest

import license as lic

MACHINE_ID = "0123456789abcdef0123456789abcdef"
_TOOLS = Path(__file__).resolve().parent.parent / "tools" / "fingerprint.py"


def _expected_v2(kind: str, value: str) -> str:
    raw = f"veritas-fp-v2|linux|{kind}|{value}"
    return "v2:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@pytest.fixture
def linux(monkeypatch, tmp_path):
    """Pretend to be Linux with a controllable machine-id file."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    mid = tmp_path / "machine-id"
    mid.write_text(MACHINE_ID + "\n")
    monkeypatch.setattr(lic, "_MACHINE_ID_PATHS", (str(mid),))
    return mid


def _load_tools_fingerprint(monkeypatch, machine_id_path: Path):
    spec = importlib.util.spec_from_file_location("tools_fingerprint", _TOOLS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "MACHINE_ID_PATHS", (str(machine_id_path),))
    return mod


class TestLinuxFingerprintV2:
    def test_format_and_value(self, linux):
        fp = lic.current_fingerprint()
        assert fp == _expected_v2("machine-id", MACHINE_ID)
        assert fp.startswith("v2:") and len(fp) == 3 + 64

    def test_stable_across_calls(self, linux):
        assert lic.current_fingerprint() == lic.current_fingerprint()

    def test_independent_of_hostname_and_mac(self, linux, monkeypatch):
        before = lic.current_fingerprint()
        monkeypatch.setattr(socket, "gethostname", lambda: "a-completely-different-host")
        monkeypatch.setattr(uuid, "getnode", lambda: 0xAABBCCDDEEFF)
        assert lic.current_fingerprint() == before

    def test_different_machine_id_gives_different_fingerprint(self, linux):
        before = lic.current_fingerprint()
        linux.write_text("f" * 32 + "\n")
        assert lic.current_fingerprint() != before

    def test_falls_back_to_disk_serial(self, linux, monkeypatch):
        linux.write_text("")  # unusable machine-id
        monkeypatch.setattr(lic, "_disk_serial", lambda: "SERIAL123")
        assert lic.current_fingerprint() == _expected_v2("disk-serial", "SERIAL123")

    def test_invalid_machine_id_is_ignored(self, linux, monkeypatch):
        linux.write_text("not-hex-content\n")
        monkeypatch.setattr(lic, "_disk_serial", lambda: "SERIAL123")
        assert lic.current_fingerprint() == _expected_v2("disk-serial", "SERIAL123")

    def test_no_anchor_raises(self, linux, monkeypatch):
        linux.write_text("")
        monkeypatch.setattr(lic, "_disk_serial", lambda: "NO_SERIAL")
        with pytest.raises(lic.FingerprintError):
            lic.current_fingerprint()


class TestToolsMatchProduct:
    def test_tools_fingerprint_equals_license_fingerprint(self, linux, monkeypatch):
        tools = _load_tools_fingerprint(monkeypatch, linux)
        assert tools.machine_fingerprint() == lic.current_fingerprint()

    def test_tools_disk_serial_fallback_matches(self, linux, monkeypatch):
        linux.write_text("")
        tools = _load_tools_fingerprint(monkeypatch, linux)
        monkeypatch.setattr(lic, "_disk_serial", lambda: "SERIAL123")
        monkeypatch.setattr(tools, "get_disk_serial_linux", lambda: "SERIAL123")
        assert tools.machine_fingerprint() == lic.current_fingerprint()


class TestCheckLicenseFingerprint:
    def test_matching_fingerprint_passes(self, linux):
        lic.check_license_fingerprint(lic.current_fingerprint())

    def test_other_machine_is_rejected_with_own_fingerprint_shown(self, linux):
        with pytest.raises(lic.LicenseError) as e:
            lic.check_license_fingerprint("v2:" + "0" * 64)
        msg = str(e.value)
        assert "not valid for this machine" in msg
        assert lic.current_fingerprint() in msg

    def test_old_format_license_on_linux_asks_for_reissue(self, linux):
        with pytest.raises(lic.LicenseError) as e:
            lic.check_license_fingerprint("a" * 64)
        msg = str(e.value)
        assert "old machine fingerprint format" in msg
        assert "reissued" in msg
        assert lic.current_fingerprint() in msg

    def test_unidentifiable_machine_gives_clear_error(self, linux, monkeypatch):
        linux.write_text("")
        monkeypatch.setattr(lic, "_disk_serial", lambda: "NO_SERIAL")
        with pytest.raises(lic.LicenseError) as e:
            lic.check_license_fingerprint("v2:" + "0" * 64)
        assert "cannot verify" in str(e.value).lower()
