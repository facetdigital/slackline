"""Human-readable rendering. The default format for every caller (D12).

Text is the default rather than JSONL even for agents, because the transcript is
both what a person wants and measurably the cheaper representation for a language
model to read: no repeated keys, no quoting, no structural punctuation.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

_RENDERERS: Dict[str, Any] = {}

# C0 controls other than tab and newline, DEL, and the C1 range. Slack content is
# untrusted, and a human's terminal interprets escape sequences. Carriage return
# is included: it lets a display name overwrite the row from column zero. The
# JSON formatters escape the same range rather than stripping it (P8, P11).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x0d\x0e-\x1f\x7f-\x9f]")


def _clean(text: str) -> str:
    return _CONTROL_RE.sub("", text)


def renderer(record_type: str):
    def register(fn):
        _RENDERERS[record_type] = fn
        return fn

    return register


@renderer("identity")
def _identity(record: Dict[str, Any]) -> str:
    user = record["user"]
    team = record["team"]
    scopes = record.get("scopes") or []
    lines = [
        f"User:      {user['name']} ({user['id']})",
        f"Workspace: {team['name']} ({team['id']})",
    ]
    if record.get("url"):
        lines.append(f"URL:       {record['url']}")
    if scopes:
        lines.append("")
        lines.append("Granted scopes:")
        lines.extend(f"  {scope}" for scope in sorted(scopes))
    write_scopes = [s for s in scopes if ":write" in s or s.startswith("chat:")]
    lines.append("")
    if write_scopes:
        # Worth surfacing loudly. slackline-read still cannot write, because the
        # method allowlist stops it, but a token holding write scope is a wider
        # credential than this tool needs and that is worth knowing.
        lines.append(
            "Note: this token holds write scopes (" + ", ".join(write_scopes) + ")."
        )
        lines.append(
            "      slackline-read still cannot mutate Slack: the method allowlist"
        )
        lines.append("      refuses every write method regardless of token scope.")
    else:
        lines.append("This token holds no write scopes. slackline-read is read-only.")
    return "\n".join(lines) + "\n"


@renderer("conversation")
def _conversation(record):
    # A conversation record from `conversation` carries a time range and heads a
    # transcript; one from `channels` is a row in a list. Same type, two uses.
    if "range" in record:
        header = f"Conversation: {record.get('label') or record['id']}  ({record['id']})"
        window = record.get("range") or {}
        if window.get("from") or window.get("to"):
            header += "\nRange:        " + " .. ".join(
                [window.get("from") or "(beginning)", window.get("to") or "(now)"]
            )
        return header + "\n\n"

    parts = [f"{record['id']:<12} {record['kind']:<8} {record['label']}"]
    if record["kind"] == "mpim" and record.get("name"):
        # Surfacing the raw name matters: two rows can share one, and the reader
        # needs to see that they are nonetheless different conversations.
        parts.append(f"  ({record['name']})")
    if record.get("num_members"):
        parts.append(f"  {record['num_members']} members")
    return "".join(parts) + "\n"


@renderer("user")
def _user(record):
    flags = []
    if record.get("is_bot"):
        flags.append("bot")
    if record.get("deleted"):
        flags.append("deactivated")
    suffix = f"  [{', '.join(flags)}]" if flags else ""
    name = record.get("real_name") or record.get("display") or ""
    return f"{record['id']:<12} @{record['handle']:<20} {name}{suffix}\n"


@renderer("resolution")
def _resolution(record: Dict[str, Any]) -> str:
    lines = []
    for match in record.get("matches") or []:
        line = f"{match['id']:<12} {match['kind']:<8} {match.get('label') or ''}"
        lines.append(line.rstrip())
        if match.get("members"):
            lines.append("             members: " + ", ".join(match["members"]))
        if match.get("name") and match["kind"] == "mpim":
            lines.append(f"             name:    {match['name']}")
    return "\n".join(lines) + "\n" if lines else "no matches\n"


@renderer("message")
def _message(record: Dict[str, Any]) -> str:
    """One message as a transcript entry.

    The shape is chosen for a language model reading a long conversation: a
    bracketed local time, a speaker, then the content quoted so message
    boundaries stay unambiguous even when a message contains blank lines. Replies
    are indented under their root.
    """
    indent = "  " if record.get("is_reply") else ""
    user = record.get("user") or {}
    # The handle, not the display name. Display names are not unique: two
    # colleagues can both show as "Joan", and a transcript where different
    # people carry the same label is worse than one that is slightly less
    # friendly. The handle is unique, stable, compact, and is the same
    # vocabulary the --with selector uses, so a reader can act on it.
    # The exception is someone from another workspace: their handle is not one
    # anyone here can select on, so they are shown by name and marked.
    name = user.get("name")
    if user.get("external"):
        speaker = f"@{user.get('display') or name or user.get('id')} (ext)"
    else:
        speaker = f"@{name}" if name else (user.get("display") or "unknown")
    if user.get("is_bot"):
        speaker += " (bot)"

    # Trim to minutes: seconds are noise when reading a conversation, and the
    # exact ts is still in the JSONL output for anything that needs it.
    stamp = (record.get("time") or "")[:16].replace("T", " ")

    lines = [f"{indent}[{stamp}] {speaker}:"]
    # Cleaned before splitting as well as at the seam: NEL and the other C1
    # separators count as line breaks to splitlines() and would otherwise
    # become spurious lines rather than being removed.
    for line in _clean(record.get("text") or "").splitlines() or [""]:
        lines.append(f"{indent}> {line}")

    trailer = []
    if record.get("edited"):
        trailer.append("edited")
    if record.get("reactions"):
        trailer.append(
            " ".join(f":{r['name']}:{r['count']}" for r in record["reactions"])
        )
    replies = record.get("reply_count") or 0
    if replies and not record.get("is_reply"):
        trailer.append(f"{replies} repl{'y' if replies == 1 else 'ies'}")
    if trailer:
        lines.append(f"{indent}  ({'; '.join(trailer)})")
    return "\n".join(lines) + "\n\n"


@renderer("page")
def _page(record: Dict[str, Any]) -> str:
    """The trailing summary. Always printed, because a reader must be able to
    tell that a transcript is a subset without inspecting the JSON."""
    count = record.get("returned", 0)
    parts = [f"{count} message" + ("" if count == 1 else "s")]
    if record.get("filtered"):
        parts.append(f"{record['filtered']} filtered out")
    if record.get("incomplete_threads"):
        n = record["incomplete_threads"]
        parts.append(f"{n} thread{'' if n == 1 else 's'} not fully expanded, pass --all-pages")
    if record.get("truncated_by") == "max-messages":
        parts.append("TRUNCATED by --max-messages, there is more")
    elif record.get("truncated_by"):
        parts.append(f"stopped by the {record['truncated_by']} ceiling, there is more")
    elif record.get("has_more"):
        parts.append("more available, pass --all-pages or use the cursor")
    if record.get("next_cursor"):
        parts.append(f"next cursor: {record['next_cursor']}")
    return "-- " + "; ".join(parts) + "\n"


def _generic(record: Dict[str, Any]) -> str:
    """Fallback for a record type with no registered renderer.

    Silently skipping unrenderable records would make "this record type has no
    renderer yet" indistinguishable from "there were no results", which is the
    worst possible failure for an agent consuming the output: empty, exit 0, and
    confidently wrong. The same reasoning as D9 applied one layer up.
    """
    kind = record.get("type", "record")
    fields = ", ".join(f"{k}={v!r}" for k, v in record.items() if k != "type")
    return f"[{kind}] {fields}\n"


def render_records(records: List[Dict[str, Any]]) -> str:
    chunks = []
    for record in records:
        render = _RENDERERS.get(record.get("type", ""), _generic)
        # Cleaned at the seam every renderer passes through, so no field of
        # any record type can carry an escape sequence to the terminal.
        chunks.append(_clean(render(record)))
    return "".join(chunks)
