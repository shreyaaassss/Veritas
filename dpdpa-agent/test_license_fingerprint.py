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


# ── macOS (version 2) ──────────────────────────────────────────────────────
HW_UUID = "D2B8E210-9EA8-59D6-ADEF-3DA4CB0A7130"
HW_SERIAL = "JW7LFQ0JLX"


def _ioreg_output(uuid_value=HW_UUID, serial=HW_SERIAL) -> bytes:
    lines = ["+-o Root  <class IORegistryEntry>", "  {"]
    if serial:
        lines.append(f'    "IOPlatformSerialNumber" = "{serial}"')
    if uuid_value:
        lines.append(f'    "IOPlatformUUID" = "{uuid_value}"')
    lines += ['    "manufacturer" = <"Apple Inc.">', "  }"]
    return "\n".join(lines).encode()


def _expected_macos(kind: str, value: str) -> str:
    return "v2:" + hashlib.sha256(f"veritas-fp-v2|macos|{kind}|{value}".encode()).hexdigest()


@pytest.fixture
def mac(monkeypatch):
    """Pretend to be a Mac whose ioreg output we control."""
    state = {"out": _ioreg_output()}
    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    def fake_check_output(cmd, *a, **kw):
        if state["out"] is None:
            raise FileNotFoundError("ioreg")
        assert cmd[0].endswith("ioreg")
        return state["out"]

    monkeypatch.setattr(lic.subprocess, "check_output", fake_check_output)
    return state


def _load_tools_for_mac(monkeypatch, state):
    spec = importlib.util.spec_from_file_location("tools_fingerprint_mac", _TOOLS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.subprocess, "check_output", lambda cmd, *a, **kw: state["out"])
    return mod


class TestMacosFingerprintV2:
    def test_format_and_value(self, mac):
        fp = lic.current_fingerprint()
        assert fp == _expected_macos("platform-uuid", HW_UUID.lower())
        assert fp.startswith("v2:") and len(fp) == 3 + 64

    def test_independent_of_hostname_and_mac_address(self, mac, monkeypatch):
        before = lic.current_fingerprint()
        monkeypatch.setattr(socket, "gethostname", lambda: "a-different-name.local")
        monkeypatch.setattr(uuid, "getnode", lambda: 0xAABBCCDDEEFF)
        assert lic.current_fingerprint() == before

    def test_uuid_case_does_not_matter_and_machines_differ(self, mac):
        a = lic.current_fingerprint()
        mac["out"] = _ioreg_output(uuid_value=HW_UUID.lower())
        assert lic.current_fingerprint() == a
        mac["out"] = _ioreg_output(uuid_value="11111111-2222-3333-4444-555555555555")
        assert lic.current_fingerprint() != a

    def test_falls_back_to_the_hardware_serial(self, mac):
        mac["out"] = _ioreg_output(uuid_value=None)
        assert lic.current_fingerprint() == _expected_macos("hardware-serial", HW_SERIAL)

    def test_kinds_never_collide(self, mac):
        by_uuid = lic.current_fingerprint()
        mac["out"] = _ioreg_output(uuid_value=None)
        assert lic.current_fingerprint() != by_uuid

    def test_no_identity_raises_a_clear_error(self, mac):
        mac["out"] = _ioreg_output(uuid_value=None, serial=None)
        with pytest.raises(lic.FingerprintError, match="stable machine identity"):
            lic.current_fingerprint()
        mac["out"] = None            # ioreg missing altogether
        with pytest.raises(lic.FingerprintError):
            lic.current_fingerprint()

    def test_is_different_from_the_linux_value_for_the_same_text(self, mac, monkeypatch):
        # the platform name is part of the hash
        assert _expected_macos("platform-uuid", HW_UUID.lower()) != _expected_v2("platform-uuid", HW_UUID.lower())

    def test_tools_fingerprint_equals_the_product_fingerprint(self, mac, monkeypatch):
        tools = _load_tools_for_mac(monkeypatch, mac)
        assert tools.machine_fingerprint() == lic.current_fingerprint()
        mac["out"] = _ioreg_output(uuid_value=None)
        assert tools.machine_fingerprint() == lic.current_fingerprint()
        mac["out"] = _ioreg_output(uuid_value=None, serial=None)
        with pytest.raises(RuntimeError):
            tools.machine_fingerprint()

    def test_license_for_this_mac_passes_and_old_format_asks_for_reissue(self, mac):
        lic.check_license_fingerprint(lic.current_fingerprint())
        old_style = hashlib.sha256(b"SERIAL:0x1:host:Darwin").hexdigest()
        with pytest.raises(lic.LicenseError, match="old machine fingerprint format.*macOS"):
            lic.check_license_fingerprint(old_style)

    def test_a_license_for_another_mac_shows_this_macs_fingerprint(self, mac):
        other = _expected_macos("platform-uuid", "11111111-2222-3333-4444-555555555555")
        with pytest.raises(lic.LicenseError) as e:
            lic.check_license_fingerprint(other)
        assert lic.current_fingerprint() in str(e.value)
