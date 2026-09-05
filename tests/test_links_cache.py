"""Tests for the extraction-payload cache (jlab.links.cache).

Covers t9's acceptance criteria:

1. Rendering/loading from the cache yields the same extraction payload as
   the run that wrote it, without opening a Discord session -- proven by
   making ``jlab.cli._discord.scan_window`` raise and loading anyway.
2. The cache carries author ids only, never resolved display names.
3. The cache stores the SCAN timestamp, not the render/load timestamp, and
   exposes it clearly to a caller.
4. Staleness (attachment-URL expiry, ~14-22h measured) is checkable via a
   dedicated helper, without this module doing any rendering itself.
5. The cache file lives under the run directory from jlab/links/paths.py
   and is gitignored -- proven with an actual ``git check-ignore`` call.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jlab.links import cache as links_cache
from jlab.links.paths import links_report_path

_REPO_ROOT = Path(__file__).resolve().parents[1]

_RESOLVED_NAME = "Some One Definitely Resolved"

_RECORDS = [
    {
        "url": "https://example.com/a",
        "channel": {"id": "c1", "name": "general"},
        "timestamp": "2026-06-01T00:00:00+00:00",
        "thread": {},
        "author_id": "111",
        "jump_url": "https://discord.com/channels/g/c1/1",
        "from_attachment": False,
    },
    {
        "url": "https://cdn.discordapp.com/attachments/1/2/file.png",
        "channel": {},
        "timestamp": "2026-06-02T00:00:00+00:00",
        "thread": {"id": "t1", "name": "sidebar"},
        "author_id": "222",
        "jump_url": None,
        "from_attachment": True,
    },
]


def _run_id(suffix: str) -> str:
    return f"20260905T000000Z-{suffix}"


def test_write_then_load_round_trips_the_extraction_payload() -> None:
    run_id = _run_id("t9a")
    scanned_at = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)

    links_cache.write_cache(run_id, _RECORDS, scanned_at=scanned_at)
    payload = links_cache.load_cache(run_id)

    assert payload["records"] == _RECORDS


def test_load_never_opens_a_discord_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading a cache must work even if scan_window() would blow up.

    Proves criterion 1: rendering from a cache is possible without a
    second Discord scan. We monkeypatch scan_window to raise, confirm it
    does raise if called, then show load_cache() never calls it.
    """
    run_id = _run_id("t9b")
    links_cache.write_cache(
        run_id,
        _RECORDS,
        scanned_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )

    from jlab.cli import _discord

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("scan_window must never be called when loading from cache")

    monkeypatch.setattr(_discord, "scan_window", _boom)

    with pytest.raises(AssertionError):
        _discord.scan_window(1326246312072581160)

    payload = links_cache.load_cache(run_id)
    assert payload["records"] == _RECORDS


def test_cache_contains_no_resolved_display_names() -> None:
    run_id = _run_id("t9c")
    links_cache.write_cache(
        run_id,
        _RECORDS,
        scanned_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )

    path = links_report_path(run_id, links_cache.CACHE_FILENAME)
    text = path.read_text(encoding="utf-8")

    assert _RESOLVED_NAME not in text
    assert "display_name" not in text
    for record in _RECORDS:
        assert record["author_id"] in text


def test_cache_stores_scan_time_not_load_time() -> None:
    run_id = _run_id("t9d")
    scanned_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

    links_cache.write_cache(run_id, _RECORDS, scanned_at=scanned_at)
    payload = links_cache.load_cache(run_id)

    loaded_scanned_at = links_cache.cache_scanned_at(payload)
    assert loaded_scanned_at == scanned_at

    now = datetime.now(timezone.utc)
    assert now - loaded_scanned_at > timedelta(days=365 * 4)

    # The raw payload must expose the scan time directly too, not bury it.
    assert payload["scanned_at"] == scanned_at.isoformat()


def test_write_cache_defaults_scanned_at_to_now() -> None:
    run_id = _run_id("t9e")
    before = datetime.now(timezone.utc)
    links_cache.write_cache(run_id, _RECORDS)
    after = datetime.now(timezone.utc)

    payload = links_cache.load_cache(run_id)
    scanned_at = links_cache.cache_scanned_at(payload)

    assert before - timedelta(seconds=5) <= scanned_at <= after + timedelta(seconds=5)


def test_attachments_expired_false_when_fresh() -> None:
    run_id = _run_id("t9f")
    now = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
    links_cache.write_cache(run_id, _RECORDS, scanned_at=now)

    payload = links_cache.load_cache(run_id)
    just_after = now + timedelta(hours=1)
    assert links_cache.attachments_expired(payload, now=just_after) is False


def test_attachments_expired_true_past_conservative_window() -> None:
    run_id = _run_id("t9g")
    scanned_at = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
    links_cache.write_cache(run_id, _RECORDS, scanned_at=scanned_at)

    payload = links_cache.load_cache(run_id)
    well_after = scanned_at + timedelta(hours=links_cache.ATTACHMENT_URL_EXPIRY_HOURS + 1)
    assert links_cache.attachments_expired(payload, now=well_after) is True


def test_attachment_expiry_threshold_is_conservative_lower_bound() -> None:
    # Measured window is 14-22 hours; the module must use the *earlier*
    # (more conservative) end so a renderer never presents a dead URL as
    # live.
    assert links_cache.ATTACHMENT_URL_EXPIRY_HOURS == 14


def test_cache_file_is_gitignored() -> None:
    run_id = _run_id("t9h")
    path = links_cache.write_cache(
        run_id,
        _RECORDS,
        scanned_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )

    assert path.name == links_cache.CACHE_FILENAME
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        cwd=_REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, f"{path} must be gitignored"


def test_write_cache_accepts_custom_filename() -> None:
    run_id = _run_id("t9i")
    path = links_cache.write_cache(
        run_id,
        _RECORDS,
        scanned_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
        filename="alt-cache.json",
    )
    assert path.name == "alt-cache.json"

    payload = links_cache.load_cache(run_id, filename="alt-cache.json")
    assert payload["records"] == _RECORDS


def test_round_trip_preserves_empty_refs_and_from_attachment_flags() -> None:
    run_id = _run_id("t9j")
    links_cache.write_cache(
        run_id,
        _RECORDS,
        scanned_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )
    payload = links_cache.load_cache(run_id)

    by_url = {record["url"]: record for record in payload["records"]}
    assert by_url["https://example.com/a"]["channel"] == {"id": "c1", "name": "general"}
    assert by_url["https://example.com/a"]["thread"] == {}
    assert by_url["https://example.com/a"]["from_attachment"] is False

    attachment_record = by_url["https://cdn.discordapp.com/attachments/1/2/file.png"]
    assert attachment_record["channel"] == {}
    assert attachment_record["thread"] == {"id": "t1", "name": "sidebar"}
    assert attachment_record["from_attachment"] is True


def test_cache_file_is_valid_json_matching_payload_shape() -> None:
    run_id = _run_id("t9k")
    scanned_at = datetime(2026, 6, 3, tzinfo=timezone.utc)
    path = links_cache.write_cache(run_id, _RECORDS, scanned_at=scanned_at)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert set(on_disk.keys()) == {"scanned_at", "records"}
    assert on_disk["scanned_at"] == scanned_at.isoformat()
    assert on_disk["records"] == _RECORDS
