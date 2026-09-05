"""Shared CSV writer for the report renderers (members, links, ...).

Both ``jlab/members/report.py`` and ``jlab/links/report.py`` need to emit a
CSV sibling of their HTML report, and both hand the same class of
attacker-controlled text to a spreadsheet: a resolved Discord display name
(members) or a shared URL (links). Neither is HTML at that point, so
``html.escape`` (which ``jlab/members/report.py::_esc`` already applies for
the HTML context) does nothing for them here -- there was no CSV sanitiser
anywhere in this repo before this module, which is the gap this task closes.

**Formula / DDE injection.** Excel, LibreOffice Calc and Google Sheets all
treat a cell whose *first character* is ``=``, ``+``, ``-``, ``@``, a TAB, or
a CR as the start of a formula (or, historically, a DDE command) rather than
literal text, on open -- with zero further preprocessing on the reader's
part. A shared URL or a resolved display name is exactly attacker-controlled
text that can land in the first cell position, so every field written
through this module is checked for those six prefixes and, when one is
found, prefixed with a single leading apostrophe (the standard, widely
documented OWASP mitigation: it forces text interpretation in every
mainstream spreadsheet reader while leaving the rest of the value
byte-for-byte intact -- nothing is stripped or reordered).

**Atomic cells.** A CSV cell is a scalar, never a delimiter-joined list
serialized into one field (e.g. ``"general;random;off-topic"``) -- that
pattern silently breaks both a naive split and any per-value reasoning a
reader or pandas does on the column. :func:`escape_csv_field` therefore
raises :class:`TypeError` for ``list``/``tuple``/``dict``/``set`` values;
callers that have a multi-valued signal (e.g. "channels touched") pick a
single scalar representation for it (a count, or one cell per value across
several *columns*) before calling in here -- that decision belongs to the
renderer, not to this module.

Zero runtime dependencies: only the stdlib ``csv`` module is used.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

__all__ = ["DANGEROUS_CSV_PREFIXES", "escape_csv_field", "write_csv"]

# The classic CSV/DDE formula-injection prefixes recognised by Excel,
# LibreOffice Calc and Google Sheets. Order is the order OWASP lists them
# in; it has no effect on behaviour.
DANGEROUS_CSV_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")

# Non-scalar container types that must never be serialized into one cell.
_NON_SCALAR_TYPES = (list, tuple, dict, set, frozenset)


def escape_csv_field(value: Any) -> str:
    """Return *value* as an inert CSV cell string.

    ``None`` becomes the empty string; everything else is stringified with
    ``str()``. If the resulting text starts with one of
    :data:`DANGEROUS_CSV_PREFIXES`, a single leading apostrophe is
    prepended so a spreadsheet opens it as literal text instead of a
    formula -- the value itself is otherwise untouched.

    Raises :class:`TypeError` if *value* is a list/tuple/dict/set: cells in
    this schema are scalars, never a joined collection.
    """
    if isinstance(value, _NON_SCALAR_TYPES):
        raise TypeError(
            "csv cell values must be scalars (str/int/float/bool/None), "
            f"got {type(value).__name__}: {value!r}"
        )
    text = "" if value is None else str(value)
    if text[:1] in DANGEROUS_CSV_PREFIXES:
        return "'" + text
    return text


def write_csv(
    path: str | Path,
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> Path:
    """Write *header* and *rows* to *path* as a formula-injection-safe CSV.

    Every cell (header and data) is passed through :func:`escape_csv_field`.
    Rows are fully materialized and validated *before* anything is written
    to disk, so a row containing a non-scalar value raises ``TypeError``
    and leaves no partial file behind.

    The file is opened with ``newline=""`` per the ``csv`` module's own
    documented contract (otherwise embedded newlines inside a quoted field
    round-trip incorrectly on some platforms), encoded as UTF-8, and
    written with the stdlib ``csv.writer`` so commas/quotes/newlines inside
    a field are quoted correctly for both Excel and Google Sheets import
    and for ``pandas.read_csv`` with no extra arguments. Returns *path*.
    """
    out_path = Path(path)

    escaped_header = [escape_csv_field(cell) for cell in header]
    escaped_rows = [[escape_csv_field(cell) for cell in row] for row in rows]

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(escaped_header)
        writer.writerows(escaped_rows)

    return out_path
