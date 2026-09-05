"""Invariant guard tests (t11): read-only and public-only.

These encode the two load-bearing invariants from ``CLAUDE.md`` — read-only
and public-only — so a future change that breaks either fails CI rather than
shipping quietly. Guard tests only: no product code is touched or added here.

1. ``test_no_discord_write_calls_anywhere_in_members_code_paths`` greps the
   members/Discord code paths for any call shaped like a write operation
   (send / edit / delete / add_reaction / create_thread) and fails if one is
   found.
2. ``test_private_channel_contributes_nothing_to_member_statistics`` drives
   the REAL ``scan_window`` (fake guild/channel seam, no live Discord)
   including a private channel whose ``history()`` raises ``AssertionError``
   if ever called, then feeds the result through the real
   ``jlab.members.aggregate.aggregate`` and asserts the private channel's
   name and its lone author never appear anywhere in the aggregate. Because
   the private channel's ``history()`` is instrumented to blow up, a pass
   here proves the filtering happens BEFORE the fetch (h16) — a post-filter
   would never even reach the assertion, since the fetch itself would have
   raised.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from jlab.cli import _discord
from jlab.links.extract import extract_links
from jlab.members.aggregate import aggregate

# ---------------------------------------------------------------------------
# 1. Read-only: no write-shaped call anywhere in the members/Discord surfaces.
# ---------------------------------------------------------------------------

# The surfaces named in the task: the adapter, the members pipeline modules,
# the discord verb (command) module, and (t12) the links pipeline modules
# plus the shared csv/atomic-writeset primitives they build on.
_GUARDED_FILES = (
    "jlab/cli/_discord.py",
    "jlab/members/aggregate.py",
    "jlab/members/resolve.py",
    "jlab/members/report.py",
    "jlab/members/paths.py",
    "jlab/cli/_commands/discord.py",
    "jlab/links/extract.py",
    "jlab/links/report.py",
    "jlab/links/cache.py",
    "jlab/links/paths.py",
    "jlab/csv_export.py",
    "jlab/atomic_writeset.py",
)

# Call-shaped patterns only — a dotted-attribute call, or a `..._message(`
# call — deliberately NOT a bare substring match. This is what it catches
# and what it does not:
#
#   CATCHES:  channel.send(...)          message.edit(...)
#             msg.delete(...)            channel.create_thread(...)
#             message.add_reaction(...)  client.send_message(...)
#             foo.delete_message(...)
#
#   DOES NOT CATCH (by design — these are not write calls):
#             "delete this block when ..."   (docstring prose)
#             "a wheel install ... editable"  (substring "edit" in prose)
#             obj.deleted                     (attribute access, no call)
#             some_variable_send = ...        (identifier containing "send")
#             _sends_ok = True                (identifier, no call parens)
#
# This is a source-shape guard, not a semantic/type-aware one: it would in
# principle also flag a hypothetical *read-only* method that happened to be
# named e.g. `.delete_cache(...)` on some unrelated object. No such call
# exists in the guarded files today (see the baseline assertion below), and
# none should ever need to.
_WRITE_CALL_PATTERN = re.compile(
    r"""
    \.(send|edit|delete|add_reaction|create_thread)\s*\(     # obj.<verb>(...)
    |
    \b\w*_(?:send|edit|delete)_message\s*\(                  # ..._send_message(
    |
    \b(?:send|edit|delete)_message\s*\(                      # send_message(
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _repo_root() -> pathlib.Path:
    # jlab/cli/_discord.py -> jlab/cli -> jlab -> repo root
    return pathlib.Path(_discord.__file__).resolve().parents[2]


def test_no_discord_write_calls_anywhere_in_members_code_paths() -> None:
    """No send/edit/delete/add_reaction/create_thread call in guarded files.

    A future change that adds a write path (a post, a reaction, a thread
    creation, an edit, a delete) to any of these modules must fail this test
    rather than ship silently — read-only is load-bearing per CLAUDE.md.
    """
    root = _repo_root()
    offenders: list[str] = []
    for rel in _GUARDED_FILES:
        path = root / rel
        assert path.is_file(), f"expected guarded file to exist: {rel}"
        text = path.read_text(encoding="utf-8")
        for match in _WRITE_CALL_PATTERN.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{rel}:{line_no}: {match.group(0)!r}")
    assert offenders == [], "write-shaped call(s) found in read-only surfaces:\n" + "\n".join(
        offenders
    )


def test_write_call_pattern_does_not_false_positive_on_known_prose() -> None:
    """Guard the guard: known innocuous strings in these files must not match.

    Regression check for the false positives called out in the module
    docstring — docstring prose containing "delete"/"edit" as plain words,
    and attribute access (no call parens) like `.deleted`.
    """
    innocuous = [
        "Delete this block and consume upstream's serializer once #14 ships.",
        "a wheel install with no culture.yaml shipped alongside the package",
        "some_object.deleted",  # attribute access, not a call
        "the_send_variable = 1",  # identifier, not a call
    ]
    for text in innocuous:
        assert not _WRITE_CALL_PATTERN.search(text), f"false positive on: {text!r}"


def test_write_call_pattern_catches_plausible_write_shapes() -> None:
    """Guard the guard: plausible write-call shapes must be caught."""
    positives = [
        "await channel.send('hi')",
        "await message.edit(content='x')",
        "await msg.delete()",
        "await message.add_reaction('👍')",
        "await channel.create_thread(name='x')",
        "await client.send_message(chan, 'x')",
    ]
    for text in positives:
        assert _WRITE_CALL_PATTERN.search(text), f"missed write-call shape: {text!r}"


# ---------------------------------------------------------------------------
# 2. Public-only: a private channel contributes nothing to member statistics,
#    and its history() is never fetched (filtering happens before the fetch).
# ---------------------------------------------------------------------------

_PRIVATE_CHANNEL_NAME = "staff-only-private"
_PRIVATE_AUTHOR_ID = "priv-author-id-999"
_PRIVATE_AUTHOR_NAME = "priv-author-name-should-never-appear"

_PUBLIC_CHANNEL_NAME = "general"
_PUBLIC_AUTHOR_ID = "pub-author-id-1"


class _FakeType:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakePerms:
    def __init__(self, view: bool) -> None:
        self.view_channel = view


class _FakeAuthor:
    def __init__(self, author_id: str, name: str, *, bot: bool = False) -> None:
        self.id = author_id
        self.name = name
        self.bot = bot
        self.global_name = None
        self.nick = None


class _FakeMsg:
    def __init__(
        self, msg_id: str, author: _FakeAuthor, content: str, created_at: datetime
    ) -> None:
        self.id = msg_id
        self.author = author
        self.content = content
        self.created_at = created_at


class _PublicChannel:
    """A public text channel whose history() yields real messages."""

    def __init__(self, channel_id: str, name: str, messages: list[_FakeMsg]) -> None:
        self.id = channel_id
        self.name = name
        self.type = _FakeType("text")
        self._messages = messages  # oldest-first

    def permissions_for(self, _role: object) -> _FakePerms:
        return _FakePerms(True)

    def history(self, limit=None, after=None):
        selected = [m for m in self._messages if after is None or m.created_at > after]

        async def _gen():
            for m in selected:
                yield m

        return _gen()


class _PrivateChannelMustNotBeFetched:
    """A private (non-@everyone-viewable) channel.

    ``history()`` raises ``AssertionError`` if ever called, so a passing test
    proves the filtering happened BEFORE any fetch — a post-filter that
    dropped this channel's rows only after reading them would trip this
    assertion instead of merely producing an empty result.
    """

    def __init__(self, channel_id: str, name: str) -> None:
        self.id = channel_id
        self.name = name
        self.type = _FakeType("text")

    def permissions_for(self, _role: object) -> _FakePerms:
        return _FakePerms(False)  # @everyone cannot view this channel

    def history(self, limit=None, after=None):  # pragma: no cover - must not run
        raise AssertionError(
            "private channel history() must never be fetched (h16: filtering "
            "must happen before fetch, not as a post-filter)"
        )


class _FakeGuild:
    def __init__(self, channels: list) -> None:
        self.default_role = object()
        self._channels = channels

    async def fetch_channels(self) -> list:
        return self._channels


class _FakeClient:
    def __init__(self, guild: _FakeGuild) -> None:
        self._guild = guild

    async def fetch_guild(self, _gid: int) -> _FakeGuild:
        return self._guild


class _FakeSeam:
    """Stand-in for discord_bot_cli.discord_client — no live Discord."""

    def __init__(self, guild: _FakeGuild) -> None:
        self._guild = guild

    def run(self, action):
        import asyncio

        return asyncio.run(action(_FakeClient(self._guild)))


def test_private_channel_contributes_nothing_to_member_statistics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    public_messages = [
        _FakeMsg(
            "m1",
            _FakeAuthor(_PUBLIC_AUTHOR_ID, "pub-author-name"),
            "hello from public",
            now - timedelta(minutes=5),
        ),
    ]
    guild = _FakeGuild(
        [
            _PublicChannel("c1", _PUBLIC_CHANNEL_NAME, public_messages),
            _PrivateChannelMustNotBeFetched("c2", _PRIVATE_CHANNEL_NAME),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    # Drives the REAL scan_window. If the private channel's filtering were a
    # post-filter instead of a pre-fetch exclusion, this call would raise
    # AssertionError from _PrivateChannelMustNotBeFetched.history() before we
    # ever reach the assertions below.
    scan_result = _discord.scan_window(123, since_days=30)

    # The scan itself never touched the private channel.
    assert scan_result["scanned_text_channels"] == 1
    assert [c["name"] for c in scan_result["channels"]] == [_PUBLIC_CHANNEL_NAME]
    assert _PRIVATE_CHANNEL_NAME not in json.dumps(scan_result)

    agg = aggregate(scan_result)

    # The private channel's name never reaches the aggregate output.
    assert _PRIVATE_CHANNEL_NAME not in json.dumps(agg)
    # Its (hypothetical) author never appears among the aggregated members —
    # there is exactly one member, and it is the public-channel author.
    author_ids = [m["author_id"] for m in agg["members"]]
    assert author_ids == [_PUBLIC_AUTHOR_ID]
    assert _PRIVATE_AUTHOR_ID not in author_ids
    assert _PRIVATE_AUTHOR_NAME not in json.dumps(agg)


# ---------------------------------------------------------------------------
# 3. (t12) Public-only, links pipeline: a private channel contributes nothing
#    to a links run, and its URL never leaks into the extraction output.
# ---------------------------------------------------------------------------

_PRIVATE_LINKS_CHANNEL_NAME = "staff-only-private-links"
_PRIVATE_LINKS_AUTHOR_ID = "priv-links-author-id-999"
_PRIVATE_LINKS_URL = "https://private.example/secret-doc"

_PUBLIC_LINKS_CHANNEL_NAME = "links-general"
_PUBLIC_LINKS_AUTHOR_ID = "pub-links-author-id-1"
_PUBLIC_LINKS_URL = "https://public.example/shared-doc"


def test_private_channel_contributes_nothing_to_links_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private channel's messages (and their URLs) never reach link extraction.

    Mirrors ``test_private_channel_contributes_nothing_to_member_statistics``
    but drives the real :func:`jlab.links.extract.extract_links` instead of
    the members aggregate. The private channel's ``history()`` still raises
    if ever called, proving the filtering happens before any fetch; and the
    private channel carries its own distinctive URL so the assertions check
    an actual leak, not merely a name match.
    """
    now = datetime.now(timezone.utc)
    public_messages = [
        _FakeMsg(
            "lm1",
            _FakeAuthor(_PUBLIC_LINKS_AUTHOR_ID, "pub-links-author-name"),
            f"check this out {_PUBLIC_LINKS_URL}",
            now - timedelta(minutes=5),
        ),
    ]
    guild = _FakeGuild(
        [
            _PublicChannel("lc1", _PUBLIC_LINKS_CHANNEL_NAME, public_messages),
            _PrivateChannelMustNotBeFetched("lc2", _PRIVATE_LINKS_CHANNEL_NAME),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    # Drives the REAL scan_window. If the private channel's filtering were a
    # post-filter instead of a pre-fetch exclusion, this call would raise
    # AssertionError from _PrivateChannelMustNotBeFetched.history() before we
    # ever reach the assertions below.
    scan_result = _discord.scan_window(123, since_days=30)

    # The scan itself never touched the private channel.
    assert scan_result["scanned_text_channels"] == 1
    assert [c["name"] for c in scan_result["channels"]] == [_PUBLIC_LINKS_CHANNEL_NAME]
    scan_json = json.dumps(scan_result)
    assert _PRIVATE_LINKS_CHANNEL_NAME not in scan_json
    assert _PRIVATE_LINKS_URL not in scan_json

    records = extract_links(scan_result)
    records_json = json.dumps(records)

    # The private channel's name never reaches the extraction output.
    assert _PRIVATE_LINKS_CHANNEL_NAME not in records_json
    # Its (hypothetical) author never appears among the extracted records —
    # there is exactly one record, and it is the public-channel author/url.
    author_ids = [r["author_id"] for r in records]
    assert author_ids == [_PUBLIC_LINKS_AUTHOR_ID]
    assert _PRIVATE_LINKS_AUTHOR_ID not in author_ids
    # Positive leak check: the private channel's distinctive URL is absent
    # from the extraction output entirely, not just its channel name.
    urls = [r["url"] for r in records]
    assert urls == [_PUBLIC_LINKS_URL]
    assert _PRIVATE_LINKS_URL not in records_json
