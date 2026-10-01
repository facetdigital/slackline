"""Whole-message rendering (D9, PRD section 7).

The load-bearing test in this file is
``test_no_fixture_message_renders_as_nothing``. 18% of real messages carry no
``text`` field, so a renderer that loses them produces a transcript that looks
complete and is not. That is the failure this whole project exists to prevent.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from slackline.render import render_message

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "messages.json").read_text()
)
MESSAGES = {k: v for k, v in FIXTURES.items() if not k.startswith("_")}


class Names:
    def handle_for(self, user_id):
        return {"U001": "@sam", "U002": "@joan", "U004": "@kim"}.get(
            user_id, f"@{user_id}"
        )

    def channel_name_for(self, channel_id):
        return {"C100": "#announcements"}.get(channel_id)


@pytest.fixture
def names():
    return Names()


def render(key, names, **kwargs):
    return render_message(MESSAGES[key], names, **kwargs)


# --- the guarantee ---------------------------------------------------------


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_no_fixture_message_renders_as_nothing(key, names):
    """Not one message may render to an empty string.

    A blank line in a transcript is indistinguishable from a message that was
    never there. If content cannot be extracted, the renderer must say so
    in-band so a model knows something existed and was unreadable, which is
    recoverable. Silence is not.
    """
    rendered = render(key, names)
    assert rendered.strip(), f"{key} rendered as nothing"


def test_a_message_with_no_usable_content_says_so_explicitly(names):
    rendered = render("genuinely_empty", names)
    assert "no renderable content" in rendered
    assert "some_future_slack_subtype" in rendered


# --- text and markup -------------------------------------------------------


def test_plain_text_passes_through(names):
    assert "postpone the migration" in render("plain_text", names)


def test_markup_in_the_text_field_is_resolved(names):
    rendered = render("mrkdwn_markup", names)
    assert "@kim" in rendered
    assert "@here" in rendered
    assert "the doc (https://example.com/doc)" in rendered
    assert "#announcements" in rendered
    assert "<@U004>" not in rendered


# --- rich_text blocks, which are 99% of real blocks ------------------------


def test_rich_text_sections_are_flattened_in_order(names):
    rendered = render("rich_text_only", names)
    assert "Agreed. The remaining risk is the credential rollover." in rendered


def test_structured_user_elements_become_handles(names):
    """Inside blocks, a mention is {"type":"user","user_id":...}, not <@U...>.

    This is the trap: code that only handles the mrkdwn form renders block
    mentions as nothing at all.
    """
    assert "@joan" in render("rich_text_only", names)


def test_structured_link_elements_keep_label_and_url(names):
    assert "the runbook (https://example.com/runbook)" in render("rich_text_only", names)


def test_emoji_elements_render_as_their_shortcode(names):
    assert ":eyes:" in render("rich_text_only", names)


def test_broadcast_elements_render_as_at_mentions(names):
    assert "@here" in render("rich_text_only", names)


def test_lists_quotes_and_code_blocks_are_readable(names):
    rendered = render("rich_text_list_quote_and_code", names)
    assert "Blockers:" in rendered
    assert "cert renewal" in rendered and "staging parity" in rendered
    assert "we agreed to hold" in rendered
    assert "make deploy ENV=stg" in rendered


def test_section_and_header_blocks_render(names):
    rendered = render("section_block", names)
    assert "Incident 4412" in rendered
    assert "resolved" in rendered
    assert "@kim" in rendered, "markup inside a section block must resolve too"


def test_section_fields_are_included(names):
    rendered = render("section_block", names)
    assert "SEV-2" in rendered and "41m" in rendered


def test_image_blocks_are_summarized_by_their_alt_text(names):
    assert "latency graph" in render("image_block", names)


def test_context_blocks_render(names):
    assert "posted from the incident channel" in render("context_block", names)


# --- attachments, the largest source of empty-text messages ----------------


def test_attachments_render_their_parts(names):
    rendered = render("attachment_only", names)
    for expected in ("Deploy pipeline update", "CI Runner", "Build #4412 succeeded",
                     "All 240 checks passed"):
        assert expected in rendered


def test_an_attachment_title_link_is_kept(names):
    assert "https://ci.example.com/4412" in render("attachment_only", names)


def test_an_attachment_with_only_a_fallback_still_renders(names):
    assert "A link preview with nothing else usable" in render("attachment_fallback_only", names)


# --- files -----------------------------------------------------------------


def test_files_are_summarized_not_dropped(names):
    rendered = render("file_only", names)
    assert "rollback-checklist.pdf" in rendered
    assert "PDF" in rendered
    assert "180" in rendered or "KB" in rendered, "size should be human readable"


def test_blocks_and_files_both_appear(names):
    rendered = render("blocks_and_files", names)
    assert "Here is the diagram" in rendered
    assert "arch.png" in rendered


# --- subtypes --------------------------------------------------------------


def test_deleted_messages_render_as_an_explicit_marker(names):
    """Their absence would itself be information, so say they were here."""
    assert "deleted" in render("tombstone", names).lower()


def test_retention_removal_renders_as_an_explicit_marker(names):
    assert "retention" in render("retention_threshold", names).lower()


def test_join_and_leave_still_render_when_asked_for(names):
    assert "joined" in render("channel_join", names).lower()


# --- metadata --------------------------------------------------------------


def test_reactions_are_rendered_compactly(names):
    rendered = render("with_reactions_and_edit", names, include_reactions=True)
    assert ":+1:" in rendered and "3" in rendered
    assert ":tada:" in rendered


def test_reactions_can_be_omitted(names):
    assert ":tada:" not in render("with_reactions_and_edit", names, include_reactions=False)


def test_an_edited_message_is_marked_as_edited(names):
    assert "edited" in render("with_reactions_and_edit", names).lower()


def test_an_unknown_user_does_not_break_rendering(names):
    assert render("unknown_user", names).strip()


# --- robustness ------------------------------------------------------------


def test_rendering_never_raises_on_malformed_input(names):
    for broken in (
        {},
        {"blocks": None},
        {"blocks": [{"type": "rich_text"}]},
        {"blocks": [{"type": "rich_text", "elements": [{}]}]},
        {"attachments": [{}]},
        {"files": [{}]},
        {"text": None},
        {"blocks": [{"type": "totally_new_block_type", "mystery": 1}]},
    ):
        assert isinstance(render_message(broken, names), str)


def test_an_unknown_block_type_is_reported_rather_than_dropped(names):
    """Slack ships new block types. Losing one silently is the failure mode."""
    rendered = render_message(
        {"blocks": [{"type": "totally_new_block_type"}]}, names
    )
    assert rendered.strip()


# --- text and blocks are alternative representations, not additive ---------


def test_text_is_not_repeated_when_blocks_say_the_same_thing(names):
    """Found by reading a real channel: every message printed twice.

    Slack's `text` is the plaintext fallback FOR the blocks. Rendering both
    repeats the entire transcript and doubles the token cost.
    """
    rendered = render("text_and_equivalent_blocks", names)
    assert rendered.count("reports are filed") == 1


def test_the_block_rendering_is_the_one_kept(names):
    """It resolves mentions structurally, so it is the richer source."""
    assert "@channel" in render("text_and_equivalent_blocks", names)


def test_genuinely_different_text_and_blocks_are_both_kept(names):
    """Conservative on purpose: repeating a line is cosmetic, losing one is not."""
    rendered = render("text_and_additional_blocks", names)
    assert "Deploy finished." in rendered
    assert "completely different" in rendered


# --- text is never dropped in favor of a subset of itself (gate fix 10) -----


def test_text_that_says_more_than_the_blocks_is_kept_in_full(names):
    assert "rotate the staging credential" in render("text_superset_of_blocks", names)


def test_and_the_overlap_is_not_repeated(names):
    assert render("text_superset_of_blocks", names).count("Deploy finished.") == 1
