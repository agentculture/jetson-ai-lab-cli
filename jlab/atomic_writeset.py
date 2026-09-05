"""All-or-nothing writer for a multi-file artifact set.

Motivating problem (see ``jlab/members/report.py``): a renderer that
produces an HTML report plus several CSV files today writes each file
independently. If the process dies partway through, the destination
directory can end up with a fresh HTML file next to stale (or missing)
CSVs — a mixed, misleading artifact set. This module gives such a
renderer one call that either produces the *complete* new set or leaves
the destination exactly as it was.

Guarantee this module actually provides
-----------------------------------------
``write_artifact_set`` writes every artifact into a private temporary
directory first (``tempfile.mkdtemp`` in the *same parent* as the
destination, so the later renames stay on one filesystem). Only once
**every** artifact has been fully written does it touch the destination
directory at all:

* If the destination does not yet exist, it is created with a single
  ``os.replace`` (a.k.a. ``rename(2)`` on POSIX) of the temp directory
  onto the destination path. A single ``rename`` of one path onto a
  non-existing path is atomic on POSIX and on Windows (``os.replace`` is
  documented as atomic on both), so this case is a true all-or-nothing
  swap.
* If the destination already exists, a directory cannot be renamed
  directly on top of a non-empty directory on any platform this module
  targets (POSIX raises ``ENOTEMPTY``; Windows refuses outright). So the
  swap is done as two renames: the existing destination is renamed aside
  to a hidden backup path, then the temp directory is renamed onto the
  now-vacated destination path, then the backup is removed. Each of
  those three steps individually is either an atomic rename or a
  best-effort cleanup — but the *pair* of renames together is not one
  atomic operation. **Platform caveat:** there is a brief window, no
  longer than the time between two ``rename`` syscalls, during which the
  destination path does not exist at all (the old contents sit under the
  backup name). A crash in exactly that window leaves the destination
  path absent rather than showing old-and-new-mixed content — the
  no-mixture guarantee still holds, but the destination is briefly
  missing rather than atomically swapped. This window never overlaps the
  artifact-writing phase itself: every artifact is fully written to the
  temp directory, and writing can be interrupted freely, before this
  swap step ever begins. If a crash happens *before* the swap (during
  writing, or before the swap starts), the destination directory is
  guaranteed completely untouched: still exactly its previous contents,
  with only an orphaned temp directory left behind (safe to delete; nothing
  under the destination path references it).

What this module deliberately does not try to provide: true one-syscall
atomicity for replacing a non-empty directory. That guarantee does not
exist in the POSIX or Windows filesystem APIs available from Python's
standard library without extra machinery (e.g. a symlink indirection
layer), which is out of scope here. Callers that need to survive a crash
in that narrow swap window too should keep that in mind; callers that
only need the writing phase to be crash-safe (the common case) get a
full guarantee.

Stdlib only: ``tempfile``, ``pathlib``, ``os``, ``shutil``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path


def write_artifact_set(dest_dir: Path | str, files: Mapping[str, str | bytes]) -> Path:
    """Write *files* into *dest_dir* as one all-or-nothing artifact set.

    ``files`` maps a relative filename (no ``..`` traversal, no absolute
    paths) to its full content, either ``str`` (written as UTF-8 text) or
    ``bytes`` (written as-is). Every artifact is written into a private
    temporary directory first; only after all of them succeed is the
    destination directory replaced. See the module docstring for the
    exact atomicity guarantee (and its one platform caveat).

    Returns *dest_dir* (as a :class:`~pathlib.Path`) on success. Raises
    on any failure, in which case the destination directory is left
    completely unchanged (see module docstring).
    """
    dest_dir = Path(dest_dir)
    parent = dest_dir.parent
    parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{dest_dir.name}.tmp-", dir=parent))
    try:
        for name, content in files.items():
            _validate_relative_name(name)
            target = tmp_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")

        _swap_into_place(tmp_dir, dest_dir)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return dest_dir


def _validate_relative_name(name: str) -> None:
    candidate = Path(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe artifact filename: {name!r}")


def _swap_into_place(tmp_dir: Path, dest_dir: Path) -> None:
    """Atomically-as-possible replace *dest_dir* with *tmp_dir*.

    See the module docstring for the exact guarantee this provides.
    """
    if not dest_dir.exists():
        os.replace(tmp_dir, dest_dir)
        return

    parent = dest_dir.parent
    backup_dir = Path(tempfile.mkdtemp(prefix=f".{dest_dir.name}.old-", dir=parent))
    # mkdtemp already created backup_dir; os.replace requires the
    # destination to not exist as a non-empty directory, so remove the
    # placeholder before using its name as the rename target.
    backup_dir.rmdir()
    try:
        os.replace(dest_dir, backup_dir)
        os.replace(tmp_dir, dest_dir)
    except BaseException:
        # Best-effort roll back: if dest_dir got moved aside but the
        # second rename failed, put the original contents back so the
        # destination is not left missing.
        if backup_dir.exists() and not dest_dir.exists():
            os.replace(backup_dir, dest_dir)
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)
