"""
Tests for the file tailer: rotation, truncation, partial lines, late files.

Rotation by rename needs POSIX semantics (Windows will not rename a file another
process holds open), so those cases are skipped there.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import agent  # noqa: E402
from agent import FileFollower  # noqa: E402

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="rename-while-open is POSIX only")


def append(path: Path, text: str) -> None:
    with open(path, "ab") as f:
        f.write(text.encode("utf-8"))


class TestBasics:
    def test_history_is_not_replayed_only_new_lines(self, tmp_path):
        log = tmp_path / "app.log"
        append(log, "old line 1\nold line 2\n")
        f = FileFollower(log)
        assert f.poll() == []
        append(log, "new line\n")
        assert f.poll() == ["new line"]
        assert f.poll() == []

    def test_file_that_appears_later_is_read_from_the_beginning(self, tmp_path):
        log = tmp_path / "late.log"
        f = FileFollower(log)
        assert f.poll() == []
        append(log, "first\nsecond\n")
        assert f.poll() == ["first", "second"]

    def test_blank_lines_and_windows_line_endings(self, tmp_path):
        log = tmp_path / "app.log"
        append(log, "")
        f = FileFollower(log)
        f.poll()
        append(log, "a\r\n\r\n   \nb\r\n")
        assert f.poll() == ["a", "b"]

    def test_partial_line_is_held_until_complete(self, tmp_path):
        log = tmp_path / "app.log"
        append(log, "")
        f = FileFollower(log)
        f.poll()
        append(log, "phone 98765")
        assert f.poll() == []
        append(log, "43210 leaked\n")
        assert f.poll() == ["phone 9876543210 leaked"], "a value must not be split across events"

    def test_multibyte_character_split_across_reads(self, tmp_path):
        log = tmp_path / "app.log"
        append(log, "")
        f = FileFollower(log)
        f.poll()
        data = "price ₹499 done\n".encode("utf-8")
        with open(log, "ab") as fh:
            fh.write(data[:8])  # cuts the 3-byte rupee sign in the middle
        assert f.poll() == []
        with open(log, "ab") as fh:
            fh.write(data[8:])
        assert f.poll() == ["price ₹499 done"]


@posix_only
class TestRotation:
    def test_rename_rotation_loses_nothing_and_repeats_nothing(self, tmp_path):
        log = tmp_path / "app.log"
        append(log, "")
        f = FileFollower(log)
        f.poll()
        append(log, "before 1\nbefore 2\n")
        got = f.poll()
        append(log, "tail written just before rotation\n")  # not read yet
        os.rename(log, tmp_path / "app.log.1")
        append(log, "after 1\nafter 2\n")  # new file with the same name
        got += f.poll()
        append(log, "after 3\n")
        got += f.poll()
        assert got == [
            "before 1", "before 2",
            "tail written just before rotation",
            "after 1", "after 2", "after 3",
        ]

    def test_gap_while_file_is_missing_then_new_file_appears(self, tmp_path):
        log = tmp_path / "app.log"
        append(log, "")
        f = FileFollower(log)
        f.poll()
        append(log, "one\n")
        os.rename(log, tmp_path / "app.log.1")
        got = f.poll()  # path is missing: old handle still drains
        assert got == ["one"]
        append(log, "two\n")
        got += f.poll()
        assert got == ["one", "two"]

    def test_last_line_without_newline_is_flushed_on_rotation(self, tmp_path):
        log = tmp_path / "app.log"
        append(log, "")
        f = FileFollower(log)
        f.poll()
        append(log, "unterminated")
        assert f.poll() == []
        os.rename(log, tmp_path / "app.log.1")
        append(log, "next\n")
        assert f.poll() == ["unterminated", "next"]

    def test_truncate_in_place_restarts_from_the_top(self, tmp_path):
        log = tmp_path / "app.log"
        append(log, "")
        f = FileFollower(log)
        f.poll()
        append(log, "a long first line that makes the file big\n")
        assert f.poll() == ["a long first line that makes the file big"]
        with open(log, "r+b") as fh:
            fh.truncate(0)
        append(log, "x\n")
        assert f.poll() == ["x"]
        append(log, "y\n")
        assert f.poll() == ["y"]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, non-root")
def test_unreadable_file_raises_permission_error(tmp_path):
    log = tmp_path / "secret.log"
    append(log, "x\n")
    log.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            FileFollower(log).poll()
    finally:
        log.chmod(0o600)


@posix_only
def test_tail_file_thread_survives_rotation_end_to_end(tmp_path):
    log = tmp_path / "app.log"
    append(log, "")
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    t = threading.Thread(
        target=agent.tail_file, args=(str(log), "order-service", q, 0.02, stop), daemon=True
    )
    t.start()
    try:
        time.sleep(0.2)
        append(log, "one\n")
        time.sleep(0.3)
        os.rename(log, tmp_path / "app.log.1")
        append(log, "two\nthree\n")
        time.sleep(0.4)
    finally:
        stop.set()
        t.join(timeout=3)
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items == [("one", "order-service"), ("two", "order-service"), ("three", "order-service")]
    assert not t.is_alive()
