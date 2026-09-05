"""Tests for the URL extraction stage (jlab.links.extract).

Covers t6's acceptance criteria:

1. Four deduped sources: content regex, attachments[].url, embeds[].url,
   and regex over embed description/field values.
2. A URL in both content and its auto-preview embed yields exactly one
   record; a URL that exists only inside a rich embed's body is extracted.
3. No second pager: this module never touches Discord, only the
   already-serialized dicts scan_window() would produce.
4. Record shape: url, channel, timestamp, thread reference, author id,
   jump link, from_attachment flag; no surrounding message text anywhere.
5. Flat output: one record per share, all-scalar, no nested lists.
6. Bots excluded by default, included behind the opt-in, with no
   human-authored link appearing/disappearing between the two runs.
7. Threads: groupable by a non-empty thread reference; no-thread is {}.
"""

from __future__ import annotations

from jlab.links.extract import extract_links

_SECRET_CONTENT = "the launch codes are hidden under the couch cushions"


def _author(author_id: str | None, bot: bool = False) -> dict:
    return {
        "id": author_id,
        "name": "someone",
        "display_name": "Some One",
        "bot": bot,
    }


def _message(
    msg_id: str,
    author_id: str | None,
    content: str = "",
    *,
    created_at: str = "2026-06-01T00:00:00+00:00",
    channel: dict | None = None,
    thread: dict | None = None,
    attachments: list[dict] | None = None,
    embeds: list[dict] | None = None,
    jump_url: str | None = None,
    bot: bool = False,
) -> dict:
    return {
        "id": msg_id,
        "author": _author(author_id, bot=bot),
        "content": content,
        "created_at": created_at,
        "channel": channel if channel is not None else {"id": "c1", "name": "general"},
        "jump_url": (
            jump_url if jump_url is not None else f"https://discord.com/channels/g/c1/{msg_id}"
        ),
        "attachments": attachments or [],
        "embeds": embeds or [],
        "thread": thread if thread is not None else {},
    }


def _channel(chan_id: str, name: str, messages: list[dict], **overrides) -> dict:
    base = {
        "id": chan_id,
        "name": name,
        "messages": messages,
        "message_count": len(messages),
        "status": "ok",
        "reason": None,
        "complete": True,
    }
    base.update(overrides)
    return base


def _scan(channels: list[dict], **overrides) -> dict:
    base = {
        "guild_id": "1326246312072581160",
        "since_days": 90,
        "cutoff": "2026-06-06T00:00:00+00:00",
        "concurrency": 4,
        "max_messages_per_channel": 5000,
        "exclude_bots": True,
        "scanned_text_channels": len(channels),
        "channels_ok": len(channels),
        "channels_partial": 0,
        "channels_failed": 0,
        "message_count": sum(c["message_count"] for c in channels),
        "complete": True,
        "channels": channels,
    }
    base.update(overrides)
    return base


def _embed(
    *,
    url: str | None = None,
    embed_type: str | None = "rich",
    description: str | None = None,
    fields: list[dict] | None = None,
) -> dict:
    return {
        "type": embed_type,
        "url": url,
        "title": None,
        "description": description,
        "fields": fields or [],
    }


def test_url_extracted_from_content() -> None:
    message = _message("m1", "u1", content="check this out https://example.com/a")
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert len(records) == 1
    assert records[0]["url"] == "https://example.com/a"
    assert records[0]["from_attachment"] is False


def test_url_extracted_only_from_attachment() -> None:
    message = _message(
        "m1",
        "u1",
        content="see the attached file",
        attachments=[
            {
                "id": "a1",
                "filename": "diagram.png",
                "url": "https://cdn.discordapp.com/attachments/1/2/diagram.png",
                "content_type": "image/png",
                "size": 1234,
            }
        ],
    )
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert len(records) == 1
    assert records[0]["url"] == "https://cdn.discordapp.com/attachments/1/2/diagram.png"
    assert records[0]["from_attachment"] is True


def test_url_extracted_only_from_embed_url() -> None:
    message = _message(
        "m1",
        "u1",
        content="no plain-text link here",
        embeds=[_embed(url="https://example.com/embed-only", embed_type="link")],
    )
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert len(records) == 1
    assert records[0]["url"] == "https://example.com/embed-only"
    assert records[0]["from_attachment"] is False


def test_content_and_autopreview_embed_dedupe_to_one_record() -> None:
    """A live probe found link/article preview embeds duplicate content's URL."""
    message = _message(
        "m1",
        "u1",
        content="check this out https://example.com/a",
        embeds=[_embed(url="https://example.com/a", embed_type="link")],
    )
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert len(records) == 1
    assert records[0]["url"] == "https://example.com/a"


def test_rich_embed_body_only_url_is_extracted() -> None:
    """type=rich embeds carry url=None and hide links in description/fields."""
    message = _message(
        "m1",
        "u1",
        content="a bot posted a rich embed",
        embeds=[
            _embed(
                url=None,
                embed_type="rich",
                description="see https://example.com/in-description for details",
                fields=[{"name": "Link", "value": "https://example.com/in-field"}],
            )
        ],
    )
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)
    urls = {r["url"] for r in records}

    assert urls == {"https://example.com/in-description", "https://example.com/in-field"}


def test_four_sources_all_deduped_together() -> None:
    shared = "https://example.com/shared"
    message = _message(
        "m1",
        "u1",
        content=f"link one {shared}",
        attachments=[
            {
                "id": "a1",
                "filename": "f.png",
                "url": shared,
                "content_type": "image/png",
                "size": 1,
            }
        ],
        embeds=[
            _embed(url=shared, embed_type="link"),
            _embed(
                url=None,
                embed_type="rich",
                description=f"also mentions {shared}",
                fields=[{"name": "x", "value": shared}],
            ),
        ],
    )
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert len(records) == 1
    assert records[0]["url"] == shared
    # It DID come from an attachment among its sources, so still flagged.
    assert records[0]["from_attachment"] is True


def test_no_second_pager_only_consumes_scan_output() -> None:
    """extract_links takes a plain dict; it cannot call history() or the API."""
    import ast
    import inspect

    from jlab.links import extract as extract_module

    source = inspect.getsource(extract_module)
    assert "discord_bot_cli" not in source
    assert "import discord" not in source

    tree = ast.parse(source)
    call_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "history" not in call_names


def test_no_message_text_leaks_into_output() -> None:
    message = _message(
        "m1",
        "u1",
        content=f"{_SECRET_CONTENT} https://example.com/a",
    )
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)
    dumped = str(records)

    assert _SECRET_CONTENT not in dumped


def test_record_shape_is_flat_and_scalar() -> None:
    message = _message(
        "m1",
        "u1",
        content="https://example.com/a",
        thread={"id": "t1", "name": "my-thread"},
        jump_url="https://discord.com/channels/g/c1/m1",
    )
    scan = _scan([_channel("c1", "general", [message])])

    record = extract_links(scan)[0]

    assert record["url"] == "https://example.com/a"
    assert record["channel"] == {"id": "c1", "name": "general"}
    assert record["timestamp"] == "2026-06-01T00:00:00+00:00"
    assert record["thread"] == {"id": "t1", "name": "my-thread"}
    assert record["author_id"] == "u1"
    assert record["jump_url"] == "https://discord.com/channels/g/c1/m1"
    assert record["from_attachment"] is False
    for value in record.values():
        assert not isinstance(value, list)


def test_one_record_per_share_not_per_url() -> None:
    same_url = "https://example.com/popular"
    messages = [_message(f"m{i}", "u1", content=same_url) for i in range(20)]
    scan = _scan([_channel("c1", "general", messages)])

    records = extract_links(scan)

    assert len(records) == 20
    assert all(r["url"] == same_url for r in records)


def test_bots_excluded_by_default_and_included_on_opt_in() -> None:
    human = _message("m1", "u1", content="https://example.com/human", bot=False)
    bot = _message("m2", "u2", content="https://example.com/bot", bot=True)
    scan = _scan([_channel("c1", "general", [human, bot])])

    default_records = extract_links(scan)
    included_records = extract_links(scan, include_bots=True)

    default_urls = {r["url"] for r in default_records}
    included_urls = {r["url"] for r in included_records}

    assert default_urls == {"https://example.com/human"}
    assert included_urls == {"https://example.com/human", "https://example.com/bot"}
    # The only difference is the bot-authored link; the human one is stable.
    assert "https://example.com/human" in included_urls
    human_default = next(r for r in default_records if r["url"] == "https://example.com/human")
    human_included = next(r for r in included_records if r["url"] == "https://example.com/human")
    assert human_default == human_included


def test_thread_reference_groups_and_no_thread_is_empty() -> None:
    thread_ref = {"id": "t1", "name": "my-thread"}
    in_thread_a = _message("m1", "u1", content="https://example.com/a", thread=thread_ref)
    in_thread_b = _message("m2", "u1", content="https://example.com/b", thread=thread_ref)
    no_thread = _message("m3", "u1", content="https://example.com/c")
    scan = _scan([_channel("c1", "general", [in_thread_a, in_thread_b, no_thread])])

    records = extract_links(scan)
    by_url = {r["url"]: r for r in records}

    assert by_url["https://example.com/a"]["thread"] == thread_ref
    assert by_url["https://example.com/b"]["thread"] == thread_ref
    assert by_url["https://example.com/a"]["thread"] == by_url["https://example.com/b"]["thread"]
    assert by_url["https://example.com/c"]["thread"] == {}


def test_trailing_sentence_punctuation_is_stripped() -> None:
    message = _message("m1", "u1", content="see https://x.com/a.")
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert records[0]["url"] == "https://x.com/a"


def test_angle_bracket_wrapped_url_is_unwrapped() -> None:
    message = _message("m1", "u1", content="link: <https://x.com/a>")
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert records[0]["url"] == "https://x.com/a"


def test_markdown_link_syntax_strips_closing_paren() -> None:
    message = _message("m1", "u1", content="[click here](https://x.com/a)")
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert records[0]["url"] == "https://x.com/a"


def test_url_with_legitimately_balanced_parens_is_preserved() -> None:
    message = _message("m1", "u1", content="see https://en.wikipedia.org/wiki/Foo_(disambiguation)")
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert records[0]["url"] == "https://en.wikipedia.org/wiki/Foo_(disambiguation)"


def test_url_with_query_string_and_fragment_not_truncated() -> None:
    message = _message("m1", "u1", content="https://x.com/a?b=1&c=2#section")
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert records[0]["url"] == "https://x.com/a?b=1&c=2#section"


def test_message_with_no_author_id_is_skipped() -> None:
    message = _message("m1", None, content="https://x.com/a")
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert records == []


def test_message_with_no_url_produces_no_records() -> None:
    message = _message("m1", "u1", content="no links here at all")
    scan = _scan([_channel("c1", "general", [message])])

    records = extract_links(scan)

    assert records == []


def test_empty_scan_produces_empty_list() -> None:
    scan = _scan([])

    assert extract_links(scan) == []
