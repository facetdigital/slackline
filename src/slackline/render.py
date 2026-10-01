"""Slack's wire format to text a language model can actually read.

This module is the core of the product (PRD section 7). Reconnaissance against
the live workspace found that **18% of real messages carry no ``text`` at all**
and 72% carry ``blocks``. A tool that reads ``message["text"]`` and stops drops
nearly one message in five, silently, with no error. For an agent asked to
summarize a decision, a silently missing message is the worst possible failure:
invisible, confident, and wrong.

Two responsibilities:

1. :func:`resolve_mrkdwn` turns Slack markup into readable text (D10).
2. :func:`render_message` assembles content from every source a message has,
   and says so explicitly when there is genuinely nothing to render (D9).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Protocol

# Slack wraps every entity in angle brackets. One pass over this pattern handles
# all of them, which matters: parsing them separately invites an ordering bug
# where one pass rewrites text the next pass then misreads.
_ENTITY_RE = re.compile(r"<([^<>]*)>")

# Slack escapes exactly three characters, and only these three.
_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))


class NameSource(Protocol):
    """The directory operations the renderer needs. Kept narrow for testing."""

    def handle_for(self, user_id: str) -> str: ...

    def channel_name_for(self, channel_id: str) -> Optional[str]: ...


def _unescape(text: str) -> str:
    """Undo Slack's escaping.

    Ampersand goes last. Doing it first would turn '&amp;lt;' into '&lt;' and
    then into '<', corrupting text that was correctly double-escaped.
    """
    for entity, character in _ENTITIES:
        text = text.replace(entity, character)
    return text


def _render_entity(body: str, names: NameSource) -> str:
    """Render one ``<...>`` entity."""
    if not body:
        return "<>"

    kind, _, label = body.partition("|")

    # User mention: <@U123> or <@U123|handle>
    if kind.startswith("@"):
        user_id = kind[1:]
        if not user_id:
            return f"<{body}>"
        return f"@{label}" if label else names.handle_for(user_id)

    # Channel mention: <#C123|name> or <#C123>
    if kind.startswith("#"):
        channel_id = kind[1:]
        if not channel_id:
            return f"<{body}>"
        if label:
            return f"#{label}"
        return names.channel_name_for(channel_id) or f"#{channel_id}"

    # Special mentions: <!here>, <!channel>, <!subteam^S123|@eng>, <!date^...|text>
    if kind.startswith("!"):
        command = kind[1:]
        if command.startswith("date^"):
            # The fallback text after the pipe is Slack's own rendering, which
            # is already localized and readable. Reimplementing its date format
            # tokens would be a lot of work to arrive somewhere worse.
            return label or command
        if command.startswith("subteam^"):
            return label if label else "@" + command.split("^", 1)[1]
        if not command:
            return f"<{body}>"
        return f"@{command}"

    # Links. Slack passes the scheme through, so anything with one lands here.
    if ":" in kind:
        scheme = kind.split(":", 1)[0].lower()
        if scheme in ("mailto", "tel"):
            return label or kind.split(":", 1)[1]
        if label and label != kind:
            # Both halves are kept: the label carries the intent, the URL is
            # what a reader would need to follow it.
            return f"{label} ({kind})"
        return kind

    return f"<{body}>" if not label else label


def resolve_mrkdwn(text: Optional[str], names: NameSource) -> str:
    """Turn Slack mrkdwn into readable text.

    Markup is parsed before entities are unescaped, never after. Slack sends a
    literally-typed ``<@U001>`` as ``&lt;@U001&gt;``; unescaping first would turn
    that into a real mention and rewrite what the person actually said.
    """
    if not text:
        return ""

    rendered = _ENTITY_RE.sub(lambda match: _render_entity(match.group(1), names), text)
    return _unescape(rendered)


# ---------------------------------------------------------------------------
# Whole-message rendering (D9)
# ---------------------------------------------------------------------------

#: Subtypes that carry no content but whose presence is itself information.
#: Rendering them as an explicit marker is deliberate: a reader needs to know a
#: message was removed, rather than seeing a gap and assuming nothing happened.
_MARKER_SUBTYPES = {
    "tombstone": "[message deleted]",
    "retention_threshold": "[earlier messages removed by the retention policy]",
}


def _human_size(num_bytes: Any) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return ""


def _rich_elements(elements: Any, names: NameSource) -> str:
    """Flatten the element list inside a rich_text sub-block.

    Mentions, links, emoji and broadcasts arrive here as *structured elements*,
    not as mrkdwn. Code that only knows the ``<@U123>`` form renders these as
    nothing at all, which is the specific trap this function exists to avoid.
    rich_text is 99% of the blocks in the surveyed workspace.
    """
    parts: List[str] = []
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        kind = element.get("type")

        if kind == "text":
            parts.append(element.get("text") or "")
        elif kind == "user":
            parts.append(names.handle_for(element.get("user_id") or ""))
        elif kind == "usergroup":
            parts.append("@" + str(element.get("usergroup_id") or "group"))
        elif kind == "channel":
            channel_id = element.get("channel_id") or ""
            parts.append(names.channel_name_for(channel_id) or f"#{channel_id}")
        elif kind == "link":
            url = element.get("url") or ""
            label = element.get("text") or ""
            parts.append(f"{label} ({url})" if label and label != url else url)
        elif kind == "emoji":
            parts.append(f":{element.get('name', '')}:")
        elif kind == "broadcast":
            parts.append("@" + str(element.get("range") or "channel"))
        elif kind == "date":
            parts.append(element.get("fallback") or "")
        elif element.get("elements"):
            # Nested sections appear inside list items.
            parts.append(_rich_elements(element["elements"], names))
        elif element.get("text"):
            parts.append(str(element["text"]))
    return "".join(parts)


def _rich_text_block(block: Dict[str, Any], names: NameSource) -> List[str]:
    lines: List[str] = []
    for sub in block.get("elements") or []:
        if not isinstance(sub, dict):
            continue
        kind = sub.get("type")
        content = _rich_elements(sub.get("elements"), names)
        if not content.strip():
            continue
        if kind == "rich_text_list":
            style = "1." if sub.get("style") == "ordered" else "-"
            for item in sub.get("elements") or []:
                item_text = _rich_elements(
                    item.get("elements") if isinstance(item, dict) else None, names
                )
                if item_text.strip():
                    lines.append(f"  {style} {item_text.strip()}")
        elif kind == "rich_text_quote":
            lines.extend(f"  > {line}" for line in content.strip().splitlines())
        elif kind == "rich_text_preformatted":
            lines.append("  ```")
            lines.extend(f"  {line}" for line in content.strip().splitlines())
            lines.append("  ```")
        else:
            lines.append(content)
    return lines


def _plain(field: Any, names: NameSource) -> str:
    """Render a Slack text object, which may be plain_text or mrkdwn."""
    if isinstance(field, dict):
        return resolve_mrkdwn(field.get("text"), names)
    if isinstance(field, str):
        return resolve_mrkdwn(field, names)
    return ""


def _render_blocks(blocks: Any, names: NameSource) -> List[str]:
    lines: List[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")

        if kind == "rich_text":
            lines.extend(_rich_text_block(block, names))
        elif kind in ("section", "header"):
            text = _plain(block.get("text"), names)
            if text.strip():
                lines.append(text)
            for field in block.get("fields") or []:
                field_text = _plain(field, names)
                if field_text.strip():
                    lines.append("  " + field_text.replace("\n", " "))
        elif kind == "context":
            parts = [_plain(element, names) for element in block.get("elements") or []]
            joined = " ".join(part for part in parts if part.strip())
            if joined.strip():
                lines.append(joined)
        elif kind == "image":
            alt = block.get("alt_text") or "image"
            lines.append(f"[image: {alt}]")
        elif kind == "divider":
            continue
        elif kind == "actions":
            continue
        else:
            # Slack ships new block types. Reporting an unrecognized one is the
            # point: losing it silently is the failure mode D9 exists to stop.
            text = _plain(block.get("text"), names)
            lines.append(text if text.strip() else f"[unrendered block: {kind}]")
    return lines


def _render_attachments(attachments: Any, names: NameSource) -> List[str]:
    """Attachments are the single largest source of empty-text messages.

    56 of the 71 text-less messages in the survey carried their content here:
    link unfurls, CI notifications, and app posts.
    """
    lines: List[str] = []
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        parts: List[str] = []
        for key in ("pretext", "author_name", "title", "text"):
            value = resolve_mrkdwn(attachment.get(key), names)
            if value.strip():
                parts.append(value.strip())
        link = attachment.get("title_link") or attachment.get("from_url")
        if link:
            parts.append(f"({link})")
        if not parts:
            fallback = resolve_mrkdwn(attachment.get("fallback"), names)
            if fallback.strip():
                parts.append(fallback.strip())
        if parts:
            lines.append("  | " + " | ".join(parts))
        if attachment.get("footer"):
            lines.append("  | " + resolve_mrkdwn(attachment["footer"], names))
    return lines


def _render_files(files: Any) -> List[str]:
    lines: List[str] = []
    for item in files or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("title") or item.get("id") or "file"
        descriptors = [d for d in (item.get("pretty_type"), _human_size(item.get("size"))) if d]
        suffix = f" ({', '.join(descriptors)})" if descriptors else ""
        lines.append(f"  [file: {name}{suffix}]")
    return lines


def _render_reactions(reactions: Any) -> str:
    parts = []
    for reaction in reactions or []:
        if isinstance(reaction, dict) and reaction.get("name"):
            parts.append(f":{reaction['name']}: {reaction.get('count', 0)}")
    return "  [" + ", ".join(parts) + "]" if parts else ""


def _side_to_keep(text: str, blocks: str) -> Optional[str]:
    """Which rendering to keep alone, ``"text"``, ``"blocks"``, or neither.

    Slack's ``text`` field is the plaintext fallback *for* the blocks, not extra
    content, so rendering both repeats every message. They are rarely identical
    strings: the block form drops formatting characters that the text form
    keeps (backticks, bold markers), so an exact comparison misses. Comparing
    only the alphanumeric content is the deliberately loose test.

    The side kept is the one that contains everything the other says. When
    neither does, ``None``, and the caller keeps both: repeating a line is
    cosmetic, and losing one is the failure this module exists to prevent (D9).
    The direction matters: preferring the block rendering when the text said
    *more* would drop the extra text.
    """
    def essence(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    text_essence, block_essence = essence(text), essence(blocks)
    if not text_essence or not block_essence:
        return None
    if text_essence in block_essence:
        return "blocks"
    if block_essence in text_essence:
        return "text"
    return None


def render_message(
    message: Dict[str, Any],
    names: NameSource,
    include_reactions: bool = True,
) -> str:
    """Render a message from every content source it has.

    Priority order per D9: ``text``, then ``blocks``, then ``attachments``, then
    ``files``. All of them are used, not the first one found: a message can
    legitimately carry a block *and* a file, and dropping either loses content.

    If nothing renders, an explicit placeholder is returned rather than an empty
    string. That is the contract: a reader may never be unable to tell the
    difference between a message that said nothing and a message that was lost.
    """
    if not isinstance(message, dict):
        return "[unrenderable message]"

    subtype = message.get("subtype")
    if subtype in _MARKER_SUBTYPES:
        return _MARKER_SUBTYPES[subtype]

    lines: List[str] = []

    text = resolve_mrkdwn(message.get("text"), names).strip()
    block_lines = [line for line in _render_blocks(message.get("blocks"), names)
                   if line.strip()]
    block_text = "\n".join(block_lines)

    # Prefer the block rendering when it says at least what the text says: it
    # resolves mentions structurally and is the richer source. When the text
    # says more, keep the text alone; its markup was already resolved. Keep
    # both only when neither contains the other.
    keep_side = _side_to_keep(text, block_text) if (text and block_text) else None
    if keep_side == "blocks":
        lines.extend(block_lines)
    elif keep_side == "text":
        lines.append(text)
    else:
        if text:
            lines.append(text)
        lines.extend(block_lines)
    lines.extend(_render_attachments(message.get("attachments"), names))
    lines.extend(_render_files(message.get("files")))

    if not lines:
        # In-band, so a model knows a message existed and could not be read.
        return f"[no renderable content: subtype={subtype or 'none'}]"

    body = "\n".join(lines)

    if message.get("edited"):
        body += "  (edited)"
    if include_reactions:
        body += _render_reactions(message.get("reactions"))
    return body
