"""
The one-time setup code that protects the first-administrator setup.

On a fresh server POST /api/auth/setup must be open, so without a secret only the operator
knows, whoever reaches /setup first on the network would become SUPER_ADMIN.
"""
from __future__ import annotations

import logging
import os
import re
import stat
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent_store.store as agent_store_module
import evidence_store.store as store_module
import setup_code
import user_store.store as user_store_module
from agent_store.store import reset_agent_store
from dashboard.server import app
from rate_limit import _limiter
from user_store.store import get_user_store, reset_user_store

client = TestClient(app, raise_server_exceptions=False)

GOOD = {"username": "firstadmin", "email": "admin@example.com", "password": "StrongPass#123"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_DEFAULT_DB_PATH", tmp_path / "evidence.db")
    monkeypatch.setattr(agent_store_module, "_DEFAULT_DB_PATH", tmp_path / "agents.db")
    monkeypatch.setattr(user_store_module, "_DEFAULT_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(setup_code, "data_root", lambda: tmp_path)
    monkeypatch.delenv(setup_code.ENV_VAR, raising=False)
    monkeypatch.setenv("VERITAS_SECURE_COOKIES", "false")
    monkeypatch.setenv("VERITAS_JWT_SECRET", "test-jwt-secret-for-unit-tests-only")
    store_module.reset_store()
    reset_agent_store()
    reset_user_store()
    _limiter._windows.clear()
    yield
    store_module.reset_store()
    reset_agent_store()
    reset_user_store()
    _limiter._windows.clear()


def setup(code, **overrides):
    body = {**GOOD, **overrides}
    if code is not None:
        body["setup_code"] = code
    return client.post("/api/auth/setup", json=body)


class TestTheCode:
    def test_format_uses_an_alphabet_without_lookalike_characters(self):
        for _ in range(200):
            code = setup_code.generate()
            assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", code), code

    def test_codes_differ(self):
        assert len({setup_code.generate() for _ in range(100)}) == 100

    @pytest.mark.parametrize("typed", ["abcd-efgh-jklm", " ABCD EFGH JKLM ", "abcdefghjklm", "AbCd-eFgH-JkLm"])
    def test_case_spaces_and_dashes_do_not_matter(self, typed):
        assert setup_code.normalize(typed) == "ABCDEFGHJKLM"

    def test_it_is_created_once_and_kept(self):
        first = setup_code.get_or_create()
        assert setup_code.get_or_create() == first
        assert setup_code.read_existing() == first

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_the_file_is_owner_only(self, tmp_path):
        setup_code.get_or_create()
        path = tmp_path / "secrets" / "setup_code"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_read_existing_never_creates_one(self, tmp_path):
        assert setup_code.read_existing() is None
        assert not (tmp_path / "secrets" / "setup_code").exists()

    def test_racing_requests_agree_on_one_code(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(setup_code.get_or_create())) for _ in range(12)]
        [t.start() for t in threads]; [t.join() for t in threads]
        assert len(set(results)) == 1

    def test_clear_removes_it(self, tmp_path):
        setup_code.get_or_create()
        setup_code.clear()
        assert setup_code.read_existing() is None
        setup_code.clear()  # clearing twice is fine

    def test_a_preset_code_is_used_and_nothing_is_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv(setup_code.ENV_VAR, "my-preset-code")
        assert setup_code.verify("MY PRESET CODE")
        assert not setup_code.verify("something else")
        assert not (tmp_path / "secrets" / "setup_code").exists()

    def test_empty_input_never_verifies(self):
        setup_code.get_or_create()
        assert not setup_code.verify("") and not setup_code.verify(None) and not setup_code.verify(" - ")


class TestSetupEndpoint:
    def test_no_code_is_refused_with_a_helpful_message_and_creates_nobody(self):
        r = setup(None)
        assert r.status_code == 403
        assert "setup code" in r.json()["detail"].lower() and "veritas setup-code" in r.json()["detail"]
        assert get_user_store().count_users() == 0

    def test_a_wrong_code_is_refused_and_creates_nobody(self):
        setup_code.get_or_create()
        r = setup("AAAA-BBBB-CCCC")
        assert r.status_code == 403
        assert get_user_store().count_users() == 0

    def test_the_right_code_creates_the_first_administrator(self):
        code = setup_code.get_or_create()
        r = setup(code)
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "SUPER_ADMIN"
        assert get_user_store().count_users() == 1

    def test_the_code_works_however_it_is_typed(self):
        code = setup_code.get_or_create()
        assert setup(f"  {code.lower().replace('-', ' ')}  ").status_code == 200

    def test_the_code_is_deleted_after_use_and_setup_cannot_be_repeated(self, tmp_path):
        code = setup_code.get_or_create()
        assert setup(code).status_code == 200
        assert not (tmp_path / "secrets" / "setup_code").exists()
        again = setup(code, username="secondadmin", email="two@example.com")
        assert again.status_code == 403 and "already" in again.json()["detail"].lower()
        assert get_user_store().count_users() == 1

    def test_once_set_up_a_wrong_code_gets_no_hint_about_codes(self):
        code = setup_code.get_or_create()
        setup(code)
        r = setup("WRONG-WRONG-WRONG")
        assert "already" in r.json()["detail"].lower() and "setup code" not in r.json()["detail"].lower()

    def test_the_code_survives_a_server_restart_until_it_is_used(self):
        first = setup_code.get_or_create()
        reset_user_store()      # as if the server restarted
        assert setup_code.get_or_create() == first
        assert setup(first).status_code == 200

    def test_the_first_attempt_creates_a_code_even_if_the_startup_banner_never_ran(self, tmp_path):
        assert setup("ANY-GUESS-HERE").status_code == 403
        assert (tmp_path / "secrets" / "setup_code").exists()

    def test_a_preset_code_works_for_automated_installs(self, monkeypatch):
        monkeypatch.setenv(setup_code.ENV_VAR, "ci-setup-code")
        assert setup("ci-setup-code").status_code == 200

    def test_guessing_is_rate_limited(self):
        setup_code.get_or_create()
        codes = [setup(f"WRONG-{i:04d}-CODE").status_code for i in range(12)]
        assert codes[:10] == [403] * 10
        assert 429 in codes[10:]

    def test_neither_the_real_nor_the_guessed_code_is_written_to_the_log(self, caplog):
        code = setup_code.get_or_create()
        with caplog.at_level(logging.DEBUG):
            setup("GUESS-GUESS-GUESS")
            setup(code)
        assert "GUESS-GUESS-GUESS" not in caplog.text and code not in caplog.text

    def test_input_validation_still_applies_with_the_right_code(self):
        code = setup_code.get_or_create()
        assert setup(code, password="short").status_code == 422
        assert setup(code, email="notanemail").status_code == 422
        assert get_user_store().count_users() == 0


class TestSetupPage:
    def test_the_page_asks_for_the_code_and_sends_it(self):
        html = client.get("/setup").text
        assert 'id="setupCode"' in html and "setup_code" in html
        assert "sudo veritas setup-code" in html


class TestCommandLineFlag:
    def _run(self, monkeypatch, capsys):
        import run_pipeline
        monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--setup-code"])
        with pytest.raises(SystemExit) as e:
            run_pipeline.main()
        out = capsys.readouterr()
        return e.value.code, out.out.strip(), out.err

    def test_prints_the_code(self, monkeypatch, capsys):
        code = setup_code.get_or_create()
        import run_pipeline
        monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--setup-code"])
        run_pipeline.main()          # returns normally
        assert capsys.readouterr().out.strip() == code

    def test_says_so_when_no_code_exists_yet(self, monkeypatch, capsys):
        status, out, err = self._run(monkeypatch, capsys)
        assert status == 1 and out == "" and "Start the Veritas service" in err

    def test_says_so_when_setup_is_complete(self, monkeypatch, capsys):
        setup(setup_code.get_or_create())
        status, out, err = self._run(monkeypatch, capsys)
        assert status == 1 and "already complete" in err
