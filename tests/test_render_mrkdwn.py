"""Slack markup to readable text (D10, PRD P8).

`<@U0ABC1234>` costs several tokens and conveys nothing to a model. `@sam`
costs one and conveys the person. Across a transcript that is both a
comprehension problem and a cost problem.
"""
from __future__ import annotations

import pytest

from slackline.render import resolve_mrkdwn


class Names:
    """Minimal directory stand-in."""

    def handle_for(self, user_id):
        return {"U001": "@sam", "U002": "@joan"}.get(user_id, f"@{user_id}")

    def channel_name_for(self, channel_id):
        return {"C100": "#announcements"}.get(channel_id)


@pytest.fixture
def names():
    return Names()


# --- mentions --------------------------------------------------------------


def test_user_mentions_become_handles(names):
    assert resolve_mrkdwn("hi <@U002> please look", names) == "hi @joan please look"


def test_user_mentions_with_an_inline_label_prefer_the_label(names):
    assert resolve_mrkdwn("<@U002|joan> here", names) == "@joan here"


def test_an_unknown_user_id_renders_as_itself_rather_than_failing(names):
    """A deleted colleague must not break a transcript."""
    assert resolve_mrkdwn("ask <@U999>", names) == "ask @U999"


def test_channel_mentions_become_names(names):
    assert resolve_mrkdwn("see <#C100|announcements>", names) == "see #announcements"


def test_channel_mentions_without_a_label_use_the_directory(names):
    assert resolve_mrkdwn("see <#C100>", names) == "see #announcements"


def test_an_unresolvable_channel_renders_as_its_id(names):
    assert resolve_mrkdwn("see <#C999>", names) == "see #C999"


def test_special_mentions(names):
    assert resolve_mrkdwn("<!here> ping", names) == "@here ping"
    assert resolve_mrkdwn("<!channel> ping", names) == "@channel ping"
    assert resolve_mrkdwn("<!everyone> ping", names) == "@everyone ping"


def test_user_group_mentions_use_the_handle(names):
    assert resolve_mrkdwn("<!subteam^S123|@eng> ship it", names) == "@eng ship it"
    assert resolve_mrkdwn("<!subteam^S123> ship it", names) == "@S123 ship it"


def test_date_tokens_fall_back_to_their_readable_text(names):
    raw = "<!date^1392734382^{date_short} at {time}|Feb 18, 2014 at 6:39 AM PST>"
    assert resolve_mrkdwn(raw, names) == "Feb 18, 2014 at 6:39 AM PST"


# --- links -----------------------------------------------------------------


def test_a_labelled_link_keeps_both_label_and_url(names):
    assert (
        resolve_mrkdwn("read <https://example.com|the doc>", names)
        == "read the doc (https://example.com)"
    )


def test_a_bare_link_renders_as_the_url(names):
    assert resolve_mrkdwn("see <https://example.com>", names) == "see https://example.com"


def test_a_link_whose_label_equals_its_url_is_not_duplicated(names):
    assert (
        resolve_mrkdwn("<https://example.com|https://example.com>", names)
        == "https://example.com"
    )


def test_mailto_links_render_as_the_address(names):
    assert resolve_mrkdwn("<mailto:a@b.com|a@b.com>", names) == "a@b.com"
    assert resolve_mrkdwn("<mailto:a@b.com>", names) == "a@b.com"


def test_telephone_links_render_as_the_number(names):
    assert resolve_mrkdwn("<tel:+15551234567>", names) == "+15551234567"


# --- entities --------------------------------------------------------------


def test_slack_entities_are_unescaped(names):
    assert resolve_mrkdwn("a &amp; b &lt; c &gt; d", names) == "a & b < c > d"


def test_entities_are_unescaped_after_markup_parsing_not_before(names):
    """The ordering bug: unescape first and a literal mention gets misparsed.

    Slack escapes exactly three entities. A user who typed the characters
    '<@U001>' has them on the wire as '&lt;@U001&gt;' and means them literally.
    Unescaping before parsing would turn that into a real mention and rewrite
    what they said.
    """
    assert resolve_mrkdwn("literally &lt;@U001&gt; ok", names) == "literally <@U001> ok"


def test_only_the_three_slack_entities_are_touched(names):
    """Slack escapes & < > and nothing else, so &quot; is literal text."""
    assert resolve_mrkdwn("say &quot;hi&quot;", names) == 'say &quot;hi&quot;'


# --- robustness ------------------------------------------------------------


def test_malformed_markup_does_not_raise(names):
    for raw in ("<@", "<>", "<@U001", "a < b > c", "<|>", "<!>", "<#>"):
        assert isinstance(resolve_mrkdwn(raw, names), str)


def test_empty_and_none_input(names):
    assert resolve_mrkdwn("", names) == ""
    assert resolve_mrkdwn(None, names) == ""


def test_several_entities_of_mixed_kinds_in_one_message(names):
    raw = "<@U002> see <#C100|announcements> and <https://x.co|docs> cc <!here>"
    assert (
        resolve_mrkdwn(raw, names)
        == "@joan see #announcements and docs (https://x.co) cc @here"
    )


def test_text_with_no_markup_is_returned_unchanged(names):
    assert resolve_mrkdwn("just plain words", names) == "just plain words"
