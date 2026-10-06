"""
Tests for the agent's version and health report (what the heartbeat carries).
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import agent  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("VERITAS_AGENT_VERSION", raising=False)
    agent.SOURCES._sources.clear()
    yield
    agent.SOURCES._sources.clear()


class TestVersion:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("VERITAS_AGENT_VERSION", "1.2.3")
        assert agent.get_agent_version() == "1.2.3"

    def test_version_file_next_to_the_script(self, tmp_path, monkeypatch):
        (tmp_path / "VERSION").write_text("1.0.18\n")
        monkeypatch.setattr(agent, "__file__", str(tmp_path / "agent.py"))
        assert agent.get_agent_version() == "1.0.18"

    def test_defaults_to_dev(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "__file__", str(tmp_path / "agent.py"))
        assert agent.get_agent_version() == "dev"

    def test_long_version_is_truncated(self, monkeypatch):
        monkeypatch.setenv("VERITAS_AGENT_VERSION", "v" * 100)
        assert len(agent.get_agent_version()) == 64

    def test_version_flag_prints_and_exits(self):
        env = {**os.environ, "VERITAS_AGENT_VERSION": "9.9.9"}
        out = subprocess.run([sys.executable, str(Path(agent.__file__)), "--version"],
                             capture_output=True, text=True, env=env, timeout=30)
        assert out.returncode == 0 and "Veritas Agent 9.9.9" in out.stdout


class TestBuildHealth:
    def test_shape_and_json_serialisable(self, monkeypatch):
        monkeypatch.setenv("VERITAS_AGENT_VERSION", "1.0.18")
        q: queue.Queue = queue.Queue(maxsize=agent.BUFFER_MAX)
        q.put(("line", "svc"))
        q.put(("line2", "svc"))
        agent.SOURCES.update("file", "/var/log/app.log", source_system="order-service", state="reading")
        health = agent.build_health(q)
        json.dumps(health)
        assert health["agent_version"] == "1.0.18"
        assert health["queue_depth"] == 2 and health["queue_capacity"] == agent.BUFFER_MAX
        assert health["uptime_seconds"] >= 0
        assert set(agent.AgentStats.NAMES) <= set(health["counters"])
        assert health["sources"][0]["state"] == "reading"

    def test_never_contains_event_text(self):
        q: queue.Queue = queue.Queue()
        q.put(("customer phone 9876543210 aadhaar 2345 6789 0124", "order-service"))
        text = json.dumps(agent.build_health(q))
        assert "9876543210" not in text and "2345 6789" not in text

    def test_works_without_a_queue(self):
        assert agent.build_health(None)["queue_depth"] == 0


class TestSourceBoard:
    def test_update_and_snapshot_are_copies(self):
        agent.SOURCES.update("file", "/a.log", source_system="x", state="reading")
        snap = agent.SOURCES.snapshot()
        snap[0]["state"] = "mutated"
        assert agent.SOURCES.snapshot()[0]["state"] == "reading"

    def test_one_entry_per_source(self):
        agent.SOURCES.update("file", "/a.log", state="waiting")
        agent.SOURCES.update("file", "/a.log", state="reading")
        agent.SOURCES.update("docker", "web", state="error", detail="gone")
        assert len(agent.SOURCES.snapshot()) == 2


def _run_tail(path, seconds, system="order-service"):
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    t = threading.Thread(target=agent.tail_file, args=(str(path), system, q, 0.02, stop), daemon=True)
    t.start()
    try:
        yield q
    finally:
        stop.set()
        t.join(timeout=3)


class TestTailReportsSourceState:
    def test_waiting_then_reading_with_last_line_time(self, tmp_path):
        log = tmp_path / "app.log"
        gen = _run_tail(log, 1)
        next(gen)
        time.sleep(0.2)
        state = {s["target"]: s for s in agent.SOURCES.snapshot()}[str(log)]
        assert state["state"] == "waiting" and state["source_system"] == "order-service"
        log.write_text("first\nsecond\n")
        time.sleep(0.3)
        state = {s["target"]: s for s in agent.SOURCES.snapshot()}[str(log)]
        assert state["state"] == "reading" and state["last_line_at"] is not None
        gen.close()

    @pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, non-root")
    def test_unreadable_file_is_reported_as_error(self, tmp_path):
        log = tmp_path / "secret.log"
        log.write_text("x\n")
        log.chmod(0o000)
        try:
            gen = _run_tail(log, 1)
            next(gen)
            time.sleep(0.3)
            state = {s["target"]: s for s in agent.SOURCES.snapshot()}[str(log)]
            assert state["state"] == "error" and state["detail"] == "Permission denied"
            gen.close()
        finally:
            log.chmod(0o600)


class TestHeartbeat:
    class _Stop(Exception):
        pass

    def test_first_heartbeat_is_sent_immediately_with_health(self, monkeypatch):
        sent = []

        class Resp:
            ok = True
            status_code = 200

        monkeypatch.setattr(agent.requests, "post", lambda url, **kw: sent.append((url, kw)) or Resp())
        monkeypatch.setattr(agent.time, "sleep", lambda s: (_ for _ in ()).throw(self._Stop()))
        monkeypatch.setenv("VERITAS_AGENT_VERSION", "1.0.18")
        q: queue.Queue = queue.Queue()
        with pytest.raises(self._Stop):
            agent.heartbeat_loop(
                {"veritas_address": "https://v:8000"},
                {"agent_id": "A1", "auth_token": "t"}, 30, q,
            )
        assert len(sent) == 1, "reports once before the first sleep"
        url, kw = sent[0]
        assert url == "https://v:8000/agent/heartbeat"
        assert kw["json"]["agent_id"] == "A1"
        assert kw["json"]["health"]["agent_version"] == "1.0.18"
        assert kw["headers"]["Authorization"] == "Bearer t"
