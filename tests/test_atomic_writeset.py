"""Tests for jlab.atomic_writeset (t5).

Covers the two acceptance criteria from the task:

1. Killing the write partway leaves either a COMPLETE artifact set or NO
   new artifacts at all — never a complete HTML beside missing or stale
   CSVs.
2. The failure path is tested by injecting an exception between file
   writes and asserting the destination directory is unchanged
   (including that a PREVIOUS run's artifacts are either fully replaced
   or fully intact — no mixture of old and new).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jlab.atomic_writeset import write_artifact_set


def _read_all(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): p.read_text(encoding="utf-8")
        for p in directory.rglob("*")
        if p.is_file()
    }


def test_writes_all_files_on_success(tmp_path: Path) -> None:
    dest = tmp_path / "report"
    files = {"index.html": "<html>v1</html>", "a.csv": "a,b\n1,2\n", "b.csv": "x\n1\n"}

    result = write_artifact_set(dest, files)

    assert result == dest
    assert dest.is_dir()
    assert _read_all(dest) == files


def test_no_partial_writes_visible_from_outside(tmp_path: Path) -> None:
    """The destination never contains a subset of the artifact set.

    This does not literally kill the process (that is not testable from
    inside pytest) — it injects a failure mid-write, which is the
    documented equivalent: any interruption before the final swap must
    leave the destination exactly as before.
    """
    dest = tmp_path / "report"

    class Boom(Exception):
        pass

    files = {"index.html": "<html>v1</html>", "a.csv": "a\n1\n"}

    call_count = {"n": 0}
    real_write_text = Path.write_text

    def flaky_write_text(self, data, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise Boom("simulated crash between file writes")
        return real_write_text(self, data, *args, **kwargs)

    monkey_target = Path.write_text
    Path.write_text = flaky_write_text
    try:
        with pytest.raises(Boom):
            write_artifact_set(dest, files)
    finally:
        Path.write_text = monkey_target

    assert not dest.exists()
    # No stray temp directories left in the parent either.
    leftovers = list(tmp_path.iterdir())
    assert leftovers == []


def test_failure_leaves_previous_run_fully_intact_not_mixed(tmp_path: Path) -> None:
    dest = tmp_path / "report"
    old_files = {"index.html": "<html>OLD</html>", "a.csv": "old\n1\n", "b.csv": "old\n2\n"}
    write_artifact_set(dest, old_files)
    assert _read_all(dest) == old_files

    new_files = {
        "index.html": "<html>NEW</html>",
        "a.csv": "new\n1\n",
        "b.csv": "new\n2\n",
    }

    class Boom(Exception):
        pass

    call_count = {"n": 0}
    real_write_text = Path.write_text

    def flaky_write_text(self, data, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise Boom("simulated crash between file writes")
        return real_write_text(self, data, *args, **kwargs)

    Path.write_text = flaky_write_text
    try:
        with pytest.raises(Boom):
            write_artifact_set(dest, new_files)
    finally:
        Path.write_text = real_write_text

    # The previous, complete run must be fully intact -- never a mixture
    # of old and new content.
    assert _read_all(dest) == old_files


def test_replaces_previous_run_completely_on_success(tmp_path: Path) -> None:
    dest = tmp_path / "report"
    old_files = {"index.html": "<html>OLD</html>", "a.csv": "old\n", "stale.csv": "stale\n"}
    write_artifact_set(dest, old_files)

    new_files = {"index.html": "<html>NEW</html>", "a.csv": "new\n"}
    write_artifact_set(dest, new_files)

    # Fully replaced: the new set is exactly present, and the old file
    # that isn't part of the new set (stale.csv) is gone -- no mixture.
    assert _read_all(dest) == new_files


def test_rejects_unsafe_filenames(tmp_path: Path) -> None:
    dest = tmp_path / "report"
    with pytest.raises(ValueError):
        write_artifact_set(dest, {"../escape.txt": "no"})
    assert not dest.exists()


def test_bytes_content_supported(tmp_path: Path) -> None:
    dest = tmp_path / "report"
    write_artifact_set(dest, {"data.bin": b"\x00\x01\x02"})
    assert (dest / "data.bin").read_bytes() == b"\x00\x01\x02"


def test_no_leftover_temp_directories_on_success(tmp_path: Path) -> None:
    dest = tmp_path / "report"
    write_artifact_set(dest, {"index.html": "<html>1</html>"})
    write_artifact_set(dest, {"index.html": "<html>2</html>"})

    assert list(tmp_path.iterdir()) == [dest]
