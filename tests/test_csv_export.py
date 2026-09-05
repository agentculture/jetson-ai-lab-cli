"""Tests for the shared CSV writer (formula-injection defence + atomic cells).

This module is used by both the members and links report renderers (see
``jlab/members/report.py`` and the not-yet-written ``jlab/links/report.py``).
It is deliberately generic: neither renderer's domain vocabulary appears
here, only the CSV-safety contract itself.

Written test-first per t4's acceptance criteria:

1. A field beginning with ``=``, ``+``, ``-``, ``@``, TAB or CR is
   prefix-escaped so it opens as inert text in Excel/Sheets.
2. A display name starting with ``=`` and a URL starting with ``@`` are
   both covered.
3. Every emitted cell is a scalar -- no delimiter-joined list appears in
   any cell -- asserted programmatically.
4. A generated CSV round-trips through ``csv.reader`` and, when pandas is
   importable as a dev dependency, ``pandas.read_csv`` -- with no
   preprocessing either way. pandas is NOT installed in this repo's dev
   group as of this task, so that half of the assertion is skipped rather
   carried by ``pandas.read_csv``, now that pandas is a dev dependency.
5. Opening a generated CSV in a REAL spreadsheet application executes
   nothing -- verified by driving LibreOffice Calc headless and inspecting
   the saved workbook, with a control proving the reader does evaluate
   formulas when they are not escaped.
"""

from __future__ import annotations

import csv
import importlib.util
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from jlab.csv_export import DANGEROUS_CSV_PREFIXES, escape_csv_field, write_csv

_HAS_PANDAS = importlib.util.find_spec("pandas") is not None


def test_dangerous_prefixes_cover_the_required_set():
    # Load-bearing: =, +, -, @, TAB, CR -- the classic CSV/DDE injection set.
    assert DANGEROUS_CSV_PREFIXES == ("=", "+", "-", "@", "\t", "\r")


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_escape_csv_field_neutralises_each_dangerous_prefix(prefix):
    value = f"{prefix}cmd|' /C calc'!A0"
    escaped = escape_csv_field(value)
    assert escaped.startswith("'")
    # The escape must not have removed or reordered the original text --
    # it must only be prefixed, so the payload is inert but recoverable.
    assert escaped[1:] == value


@pytest.mark.parametrize(
    "safe_value", ["Ori Nachum", "https://example.com/x", "123", "", "hello=world"]
)
def test_escape_csv_field_leaves_safe_values_untouched(safe_value):
    assert escape_csv_field(safe_value) == safe_value


def test_display_name_starting_with_equals_is_escaped():
    # Acceptance criterion 2: a resolved Discord display name.
    name = '=HYPERLINK("https://evil.example","click")'
    escaped = escape_csv_field(name)
    assert escaped.startswith("'=")


def test_url_starting_with_at_is_escaped():
    # Acceptance criterion 2: a shared URL landing in the first cell
    # position could itself begin with an @ (e.g. a mangled/relative form).
    url = "@SUM(1+1)*cmd"
    escaped = escape_csv_field(url)
    assert escaped.startswith("'@")


def test_escape_csv_field_rejects_non_scalar_values():
    with pytest.raises(TypeError):
        escape_csv_field(["a", "b"])
    with pytest.raises(TypeError):
        escape_csv_field({"a": 1})
    with pytest.raises(TypeError):
        escape_csv_field(("a", "b"))


def test_escape_csv_field_handles_none_and_numbers():
    assert escape_csv_field(None) == ""
    assert escape_csv_field(42) == "42"
    assert escape_csv_field(3.5) == "3.5"


def test_write_csv_rejects_a_list_valued_cell(tmp_path):
    path = tmp_path / "out.csv"
    with pytest.raises(TypeError):
        write_csv(
            path,
            ["name", "channels"],
            [["Ori", ["general", "random"]]],
        )
    # No partial file should be left behind for a rejected batch.
    assert not path.exists()


def test_write_csv_emits_only_scalar_cells_programmatically(tmp_path):
    path = tmp_path / "out.csv"
    rows = [
        ["Ori Nachum", "https://example.com/a", 3],
        ["=EVIL()", "@also-evil", 0],
    ]
    write_csv(path, ["name", "url", "count"], rows)

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        all_rows = list(reader)

    # Header + two data rows, three cells apiece -- every cell a scalar
    # (a str, never a list/tuple/dict serialized into the cell).
    assert len(all_rows) == 3
    for record in all_rows:
        assert len(record) == 3
        for cell in record:
            assert isinstance(cell, str)


def test_write_csv_escapes_dangerous_fields_end_to_end(tmp_path):
    path = tmp_path / "out.csv"
    write_csv(
        path,
        ["display_name", "url"],
        [
            ["=cmd|' /C calc'!A0", "@SUM(1+1)"],
            ["+1", "-2"],
        ],
    )

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header, row1, row2 = list(reader)

    assert header == ["display_name", "url"]
    assert row1[0].startswith("'=")
    assert row1[1].startswith("'@")
    assert row2[0].startswith("'+")
    assert row2[1].startswith("'-")


def test_write_csv_round_trips_through_csv_reader(tmp_path):
    path = tmp_path / "out.csv"
    header = ["display_name", "url", "message_count"]
    rows = [
        ["Normal Name", "https://example.com", 5],
        ["=HYPERLINK(1)", "@evil", 0],
        ["Comma, Name", 'Quote"Name', 2],
    ]
    write_csv(path, header, rows)

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        got = list(reader)

    assert got[0] == header
    assert got[1] == ["Normal Name", "https://example.com", "5"]
    assert got[2][0].startswith("'=")
    assert got[2][1].startswith("'@")
    assert got[3][0] == "Comma, Name"
    assert got[3][1] == 'Quote"Name'


@pytest.mark.skipif(not _HAS_PANDAS, reason="pandas is not a dev dependency here")
def test_write_csv_round_trips_through_pandas_read_csv(tmp_path):
    import pandas as pd

    path = tmp_path / "out.csv"
    header = ["display_name", "url", "message_count"]
    rows = [
        ["Normal Name", "https://example.com", 5],
        ["=HYPERLINK(1)", "@evil", 0],
    ]
    write_csv(path, header, rows)

    frame = pd.read_csv(path)
    assert list(frame.columns) == header
    assert frame.iloc[0]["display_name"] == "Normal Name"
    # pandas sees the escaped, inert form -- the leading apostrophe is part
    # of the string content, exactly as Excel/Sheets would treat it as text.
    assert frame.iloc[1]["display_name"] == "'=HYPERLINK(1)"


def test_write_csv_uses_universal_newline_safe_mode(tmp_path):
    """The file must be opened with newline="" per the csv module's own
    contract, or embedded CRs in a quoted field get mangled on Windows-style
    readers. Verify by round-tripping a value containing an embedded
    newline-like character once it has been through our escaping (a raw
    embedded real newline is still a bug source many CSV writers get wrong).
    """
    path = tmp_path / "out.csv"
    write_csv(path, ["a"], [["line1\nline2"]])
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    assert rows[1] == ["line1\nline2"]


# ---------------------------------------------------------------------------
# Spreadsheet-application verification (honesty condition h29).
#
# Every other test in this file asserts on the BYTES we emit. That is a proxy
# for the behaviour we actually care about -- whether a spreadsheet executes a
# hostile cell -- not a measurement of it. These two tests close that gap by
# driving a real spreadsheet application (LibreOffice Calc, headless), opening
# a generated CSV, and inspecting the saved workbook.
#
# The control test is the load-bearing half. Without it, "no formulas found"
# would also pass against a reader that never evaluates formulas at all --
# which is exactly what a first attempt at this check turned out to be doing.
# ---------------------------------------------------------------------------

_SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")
_HOSTILE_ROWS = [
    ['=HYPERLINK("http://evil","click")', "@SUM(1+1)", 1],
    ["+1234567890", "-2+3", 2],
]


def _to_xlsx(csv_path: Path) -> Path:
    """Open *csv_path* in LibreOffice Calc and save it as .xlsx."""
    subprocess.run(  # noqa: S603
        [
            _SOFFICE,
            "--headless",
            "--convert-to",
            "xlsx",
            "--outdir",
            str(csv_path.parent),
            str(csv_path),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    return csv_path.with_suffix(".xlsx")


def _sheet_xml(xlsx: Path) -> str:
    with zipfile.ZipFile(xlsx) as zf:
        return zf.read("xl/worksheets/sheet1.xml").decode("utf-8")


@pytest.mark.skipif(_SOFFICE is None, reason="LibreOffice is not installed here")
def test_escaped_csv_opens_as_inert_text_in_a_real_spreadsheet(tmp_path):
    """h29: opening a generated CSV in a spreadsheet executes nothing."""
    path = tmp_path / "escaped.csv"
    write_csv(path, ["display_name", "url", "n"], _HOSTILE_ROWS)

    sheet = _sheet_xml(_to_xlsx(path))

    assert "<f" not in sheet, "a cell became a live formula despite escaping"
    # Every hostile value survives as text, with the escape visible.
    with zipfile.ZipFile(path.with_suffix(".xlsx")) as zf:
        strings = zf.read("xl/sharedStrings.xml").decode("utf-8")
    assert "&apos;=HYPERLINK" in strings
    assert "&apos;@SUM(1+1)" in strings
    assert "&apos;+1234567890" in strings
    assert "&apos;-2+3" in strings


@pytest.mark.skipif(_SOFFICE is None, reason="LibreOffice is not installed here")
def test_control_unescaped_csv_really_does_execute(tmp_path):
    """The control that makes the test above mean something.

    Written with the SAME hostile values but WITHOUT our escaping, the
    spreadsheet must actually execute them. If this test ever stops failing
    to execute -- i.e. if it finds no formula -- then the reader is not
    evaluating formulas at all and its sibling above proves nothing.
    """
    path = tmp_path / "control.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["display_name", "url", "n"])
        writer.writerows(_HOSTILE_ROWS)

    sheet = _sheet_xml(_to_xlsx(path))

    assert "<f" in sheet, (
        "the control did not execute -- this reader does not evaluate "
        "formulas, so the escaping test above is vacuous"
    )
    assert "HYPERLINK(" in sheet
