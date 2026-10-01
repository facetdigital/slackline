# Plan: Read-Only MVP Gate Fixes

## Execution Instructions

When executing this plan:

1. **Work step-by-step.** Complete each step fully before moving to the next.
2. **Test-first within each step.** Write the failing test before the implementation, run
   it, watch it fail for the reason the step describes, then implement until it passes.
   Never edit the test afterwards to make it pass. If a step's test passes before the
   implementation, stop: either the test is wrong or the defect is not what the step says.
3. **Test after each step.** Run the commands listed under **Test:** and confirm green.
4. **Commit after each step**, using the given commit message. The message body should say
   why, not what; the diff already shows what.
5. **Update documentation continuously.** `README.md`, `CLAUDE.md`, the architecture doc if
   a decision changes, this plan's checkboxes, and `docs/plans/development-roadmap.md`
   move in the same PR as the code they describe.
6. **Group steps into PRs as marked.** One branch per PR, branched from `main`, merged with
   `--no-ff`. Never squash, never rebase.
7. **Mark completion.** When every step is done, move this item from "Next Immediate
   Step" to "Completed" in the roadmap and re-run the phase gate so the Read-Only MVP gate
   item can close.

Conventions the executor must follow:

- Run the suite as `.venv/bin/python -m pytest -q`. Bare `python3` has no `pytest`.
- Cite decision numbers in code comments (`(D16)`), the way the existing code does.
- Never use an emdash in any text you write, in code, docs, or commit messages.
- Fixtures stay synthetic: no real message content, no real colleague names.
- `FakeClient` in `tests/conftest.py` enforces the read-only allowlist. Do not bypass it.
- Do not add a runtime dependency (D1).

**Legend:** `Dn` cites [architecture.md](../architecture/architecture.md). `Pn` and `Rn`
cite [product-requirements.md](../requirements/product-requirements.md). `Gate-N` cites
item N of the fix list in
[phase-gate-read-only-mvp.md](../assessments/phase-gate-read-only-mvp.md).

---

## Summary

Close every item in the Phase 1.0 gate's fix list. The one high finding is that
continuation after a `--max-messages` truncation skips messages or has no cursor; the
fix is adaptive page sizing, so the ceiling always lands on a page boundary and Slack's
own cursor is exact. The medium findings are unpaginated thread replies, a duplicated
`thread_broadcast`, a dead `--include-archived` flag, and documentation that did not move
with the code. The low findings are hardening and doc corrections. Multi-conversation
reads, which already work, become a supported feature.

## Requirements

- After any truncation, `has_more: true` always comes with a usable `next_cursor`, and
  continuing with it never skips and never repeats a message (D16, P5, Gate-1)
- `thread` accepts `--cursor` (Gate-1)
- `--threads full` follows reply cursors under `--all-pages`, and reports threads it did
  not finish (invariants 4 and 5, Gate-2)
- A `thread_broadcast` reply is rendered once (Gate-3)
- `channels --include-archived` shows archived conversations; archived rows never win a
  name resolution over a live one (Gate-4)
- Documentation matches the credential precedence the code implements (D4a, Gate-5)
- README reflects the live verification and documents multi-conversation reads (Gate-6)
- `resolve` reports verified member lists for group DMs (D6, Gate-7)
- Audit log is owner-only; cached fields are documented (D7, D20, Gate-8)
- `Retry-After` is capped (D17, Gate-9)
- The renderer never drops `text` in favor of a subset of itself (D9, Gate-10)
- Architecture layout, D3 sample, and stale comments corrected (Gate-11)
- Terminal control characters stripped from text output; `team_id` validated before it
  becomes a path (Gate-12)
- `thread --ts` given a reply's ts reads the whole thread from its root (Gate-13)
- Real identifiers in tests and D6 are recorded as a Phase 3.1 blocker (Gate-14)
- `KeyboardInterrupt` exits 130 (gate nit)

---

## PR 1: Correct continuation, threads, and multi-conversation reads

Branch: `fix/continuation`

### Step 1: Teach `FakeClient` to honor `limit` and `cursor`

The shipped code will stop calling `client.paginate()` for transcripts and drive
`client.call()` itself with a per-request `limit` (Step 2). The fake must therefore serve
pages the way Slack does: at most `limit` items per call, and a cursor that resumes
exactly where the previous call stopped, including mid-page. Without this, no offline
test can observe the defect or its fix.

- [x] Write the failing test first: `tests/test_fake_client.py`
  - `test_call_serves_at_most_limit_items`: responses `{"conversations.history": [page_a, page_b]}`
    where `page_a` has `messages` `[m1, m2, m3]` and `response_metadata.next_cursor` `"real2"`,
    `page_b` has `[m4, m5]` and cursor `""`. `call("conversations.history", limit=2)` returns
    `[m1, m2]` and a non-empty `next_cursor` that is not `"real2"`.
  - `test_a_mid_page_cursor_resumes_without_skipping_or_repeating`: continue with that cursor
    and `limit=2`; get `[m3]` and `next_cursor == "real2"`. Continue with `"real2"`; get
    `[m4, m5]` and `next_cursor == ""`.
  - `test_a_single_dict_page_is_also_sliced`: a dict payload (not a list) with 3 messages and
    `limit=1` returns 1 message and a non-empty cursor.
  - `test_an_unknown_cursor_is_an_assertion_error`: `call(..., cursor="nope")` raises
    `AssertionError`.
  - `test_exceptions_and_callables_still_work`: an `Exception` payload is raised; a callable
    payload is called with the params.
  - `test_paginate_is_unchanged_for_directory_calls`: `paginate("users.list", "members")`
    over a two-page list still yields two `api.Page`s.
  Fails: `call()` returns the whole list object for a list payload and ignores `limit`.
- [x] Implement in `tests/conftest.py`, `FakeClient.call()`:
  - Keep the allowlist check and the `calls.append((method, params))` record first.
  - If the payload is an `Exception`, raise it. If callable, call it with `**params` and
    treat the result as the payload.
  - Normalize: `pages = payload if isinstance(payload, list) else [payload]`.
  - Locate the page: cursor `None` or `""` is `(0, 0)`. A cursor of the form
    `fake:<page>:<offset>` is parsed to those ints. Any other cursor is matched against
    `pages[i - 1]["response_metadata"]["next_cursor"]` to find `i`, with offset 0; no match
    raises `AssertionError(f"FakeClient has no page for cursor {cursor!r}")`.
  - `collection` is the first top-level key of the page whose value is a `list`. If there is
    none (for example `auth.test`), return the page unchanged.
  - `items = page[collection][offset:]`; if `limit` is given, `items = items[:limit]`.
  - If `offset + len(items) < len(page[collection])`, `next_cursor = f"fake:{page_index}:{offset + len(items)}"`;
    otherwise `next_cursor = (page.get("response_metadata") or {}).get("next_cursor") or ""`.
  - Return `dict(page, **{collection: items}, response_metadata={"next_cursor": next_cursor})`.
  - Leave `paginate()` as it is; the directory and resolver still use it.
- [x] Verify green: the new file and the whole suite. Nothing else may change behavior:
  every existing test that uses `FakeClient` must still pass unmodified.

**Satisfies:** D21 (the fake must reproduce the wire contract the code depends on); prerequisite for Gate-1

**File(s):** `tests/conftest.py`, `tests/test_fake_client.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_fake_client.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Make the fake Slack client honor limit and cursor like the real one`

---

### Step 2: A shared bounded collector with adaptive page size

One function, in `transcript.py` because both transcript commands need it, that fetches
pages until a budget of kept messages is spent, asking Slack for exactly the remaining
budget on each request. The ceiling then always coincides with a page boundary, so the
cursor Slack returns resumes at exactly the first undelivered message (D16).

- [x] Write the failing test first: `tests/test_transcript_collect.py`, using `FakeClient`
  from `conftest` and a helper that builds pages of synthetic messages
  `{"type": "message", "user": "U002", "text": f"m{i}", "ts": f"{1000 - i}.000000"}`
  (newest first within a page, like Slack). All messages are kept unless a test says
  otherwise; `keep` is `lambda m: m.get("subtype") != "channel_join"`.
  - `test_each_request_asks_for_the_remaining_budget`: three pages of 4, `limit=4`,
    `max_messages=6`, `all_pages=True`. The `limit` params sent are `[4, 2]`; 6 messages
    kept; `truncated_by == "max-messages"`; `next_cursor` is non-empty.
  - `test_continuing_from_a_truncation_skips_nothing_and_repeats_nothing`: continue from the
    cursor above with `max_messages=100`, `all_pages=True`. The union of `ts` across both
    calls equals every fixture `ts`; the intersection is empty.
  - `test_a_ceiling_reached_on_the_last_page_is_not_a_truncation`: ten messages over three
    pages, `max_messages=10`, `all_pages=True`: `truncated_by is None`, `next_cursor == ""`.
  - `test_filtered_messages_use_page_slots_but_not_budget`: page one is
    `[join, join, m1, m2]`, page two `[m3, m4]`; `limit=4`, `max_messages=2`, `all_pages=True`.
    First request `limit=2` returns `[join, join]`, both filtered, budget still 2; the
    second request is sent with `limit=2`; result has 2 kept, `filtered == 2`.
  - `test_single_page_mode_stops_after_one_request_with_the_cursor`: `all_pages=False`,
    budget not reached, cursor present: one request, `truncated_by is None`,
    `next_cursor` non-empty.
  - `test_single_page_mode_reports_truncation_when_the_budget_is_spent`: `all_pages=False`,
    `max_messages=2`, page of 4: `truncated_by == "max-messages"`, cursor non-empty.
  - `test_seen_counts_kept_and_filtered`: `seen == kept + filtered`.
  - `test_no_budget_means_page_size_is_the_limit`: `max_messages=None`, `all_pages=True`,
    three pages: every request sends `limit=limit`, everything is returned,
    `truncated_by is None`.
  Fails: `slackline.transcript` has no `collect`.
- [x] Implement in `src/slackline/transcript.py`:
  ```python
  class Collected(NamedTuple):
      messages: List[Dict[str, Any]]   # kept, in the order Slack returned them
      filtered: int
      seen: int                        # kept + filtered
      next_cursor: str                 # "" when Slack has nothing further
      truncated_by: Optional[str]      # "max-messages" or None
      pages: int

  def collect(client, method, collection, *, limit, max_messages, all_pages, cursor,
              keep, **params) -> Collected:
  ```
  - `remaining = max_messages` (may be `None`, meaning no budget).
  - Loop: `page_limit = limit if remaining is None else min(limit, remaining)`;
    `body = client.call(method, cursor=cursor or None, limit=page_limit, **params)`;
    `items = body.get(collection) or []`; for each item, `keep(item)` decides between
    `messages.append` (and `remaining -= 1` when budgeted) and `filtered += 1`;
    `next_cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""`;
    `pages += 1`.
  - Stop when `not next_cursor` (complete). Else if `remaining == 0`:
    `truncated_by = "max-messages"`, stop. Else if `not all_pages`: stop with
    `truncated_by = None`. Else `cursor = next_cursor` and continue.
  - Trust Slack's `limit`: do not add a second cut inside the item loop. A defensive cut
    there is exactly the bug being fixed.
  - Module docstring paragraph: why the budget drives the page size (D15, D16).
- [x] Verify green.

**Satisfies:** D15, D16, P5, PRD 9.5; Gate-1

**File(s):** `src/slackline/transcript.py`, `tests/test_transcript_collect.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_transcript_collect.py -q
```

**Commit message:** `Add a bounded collector that sizes each page to the remaining budget`

---

### Step 3: `conversation` uses the collector

- [x] Write the failing test first: add to `tests/test_cmd_conversation.py`
  - `test_continuing_from_a_truncated_read_delivers_every_remaining_message`: history of
    three pages of four synthetic messages (build them in the test, not the fixture);
    first run `--max-messages 2 --format jsonl`; second run with `--cursor <page.next_cursor>`
    `--all-pages --max-messages 100`. Union of message `ts` across both runs equals all
    twelve; intersection empty.
  - `test_a_truncation_never_reports_has_more_without_a_cursor`: over those twelve,
    `--all-pages --max-messages 12` gives `truncated_by is None`, `has_more is False`,
    `next_cursor == ""`; `--all-pages --max-messages 10` gives
    `truncated_by == "max-messages"`, `has_more is True`, `next_cursor` non-empty.
  - `test_the_request_limit_never_exceeds_the_remaining_budget`: `--limit 4 --max-messages 6 --all-pages`;
    the `limit` values sent to `conversations.history` are `[4, 2]`.
  - `test_end_to_end_through_main_with_cursor_flag`: in `tests/test_continuation_e2e.py`,
    monkeypatch `slackline.api._urllib_transport` with a canned transport (mirror the
    shape in the gate report: `auth.test`, `users.list`, `users.conversations`,
    `conversations.history` keyed on the `cursor` query parameter, honoring `limit`), set
    `SLACK_USER_TOKEN=xoxp-test`, `SLACKLINE_AUDIT_LOG=none`, `SLACKLINE_CACHE_DIR=tmp_path`,
    and run `cli_read.main([...])` twice as above. Assert exit 0 both times and no gaps.
  Fails: the first run reports the cursor of the following page, so the second run misses
  the rest of the cut page.
- [x] Implement in `src/slackline/commands/conversation.py`:
  - Replace the `ctx.client.paginate(...)` loop and the in-loop ceiling check with one
    call: `collected = collect(ctx.client, "conversations.history", "messages",
    limit=options["limit"], max_messages=options["max_messages"],
    all_pages=options["all_pages"], cursor=options["cursor"],
    keep=lambda m: _kept(m, options), channel=conversation["id"],
    oldest=..., latest=...)`.
  - `collected.messages` replaces the old `collected` list; `filtered` starts from
    `collected.filtered`; `next_cursor = collected.next_cursor`;
    `truncated_by = collected.truncated_by`.
  - `has_more = bool(next_cursor)`. Remove `bool(truncated_by) or`.
  - Keep the ascending sort and everything after it.
  - Update the module docstring: the ceiling coincides with a page boundary by
    construction, which is what makes the cursor exact (D16).
- [x] Verify green, including every pre-existing test in `test_cmd_conversation.py`
  unmodified.

**Satisfies:** D15, D16, P5, PRD 9.5; Gate-1

**File(s):** `src/slackline/commands/conversation.py`, `tests/test_cmd_conversation.py`, `tests/test_continuation_e2e.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_conversation.py tests/test_continuation_e2e.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Size history pages to the remaining ceiling so a cursor never skips messages`

---

### Step 4: `thread` uses the collector and accepts `--cursor`

- [x] Write the failing test first: add to `tests/test_cmd_thread.py`
  - `test_cursor_is_accepted_and_sent`: `ARGV + ["--cursor", "fake:0:1"]` against a
    three-message reply page: the `conversations.replies` call carries `cursor="fake:0:1"`,
    and the messages returned are the ones after the first.
  - `test_a_truncated_thread_reports_an_exact_cursor`: three messages, `--max-messages 2`:
    `truncated_by == "max-messages"`, `has_more is True`, `next_cursor` non-empty; continuing
    with it returns exactly the third message.
  - `test_a_thread_read_to_the_end_is_not_truncated`: three messages, `--max-messages 3`:
    `truncated_by is None`, `has_more is False`.
  - `test_continuation_pages_have_no_root_and_still_mark_replies`: with `--cursor`, no
    message has `ts == thread_ts`; every emitted message has `is_reply is True`.
  Fails: `--cursor` is an unknown option (exit 2).
- [x] Implement in `src/slackline/commands/thread.py`:
  - `_parse`: accept `--cursor VALUE`; add it to the `HELP` options block, next to
    `--all-pages`.
  - `run`: replace the paginate loop, the `seen` counter, and the post-sort ceiling with
    `collect(ctx.client, "conversations.replies", "messages", limit=..., max_messages=...,
    all_pages=..., cursor=options["cursor"], keep=..., channel=conversation["id"],
    ts=options["ts"])`. `NotFound` when `collected.seen == 0`, with or without a cursor;
    an empty page is never reported as an empty thread.
  - Keep the ascending sort of `collected.messages`. Remove the `if len(collected) >
    max_messages` block: the budget already enforced the ceiling and the cursor is exact.
    Update the module docstring paragraph about truncation: the root is on the first page
    because Slack returns the parent first, and the ceiling now stops the fetch rather
    than cutting after the fact.
  - `has_more = bool(next_cursor)`.
- [x] Verify green, pre-existing thread tests unmodified.

**Satisfies:** D13, D16, P4; Gate-1

**File(s):** `src/slackline/commands/thread.py`, `tests/test_cmd_thread.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_thread.py -q
```

**Commit message:** `Let thread continue from a cursor and stop at an exact page boundary`

---

### Step 5: Paginate thread replies under `--threads full` and report unfinished threads

- [x] Write the failing test first: add to `tests/test_cmd_conversation.py`
  - `test_replies_follow_cursors_under_all_pages`: `replies` is a list of two pages
    (`[parent, reply]` with cursor `"r2"`, then `[broadcast]`); `--threads full --all-pages`
    yields both replies; two `conversations.replies` calls, the second with `cursor="r2"`.
  - `test_replies_stop_after_one_page_without_all_pages_and_say_so`: same pages,
    `--threads full` only: one reply, `page["incomplete_threads"] == 1`, and a warning
    containing `"not fully expanded"` (capture `ctx.warn` by replacing it with a list
    append, as `test_the_fallback_scan_announces_itself_on_stderr` does).
  - `test_a_fully_expanded_thread_reports_zero_incomplete`: single reply page:
    `page["incomplete_threads"] == 0`.
  - `test_the_text_page_line_mentions_unfinished_threads`: `fmt="text"`, the trailing line
    contains `"1 thread not fully expanded"`.
  Fails: `conversations.replies` is called once with no cursor handling and the page
  record has no `incomplete_threads` key.
- [x] Implement:
  - `conversation.py`: for each expandable root, `replies = collect(ctx.client,
    "conversations.replies", "messages", limit=options["limit"], max_messages=None,
    all_pages=options["all_pages"], cursor=None, keep=lambda m: _kept(m, options),
    channel=conversation["id"], ts=message["ts"])`. Skip the root's own `ts` when
    emitting. `filtered += replies.filtered`. If `replies.next_cursor`, increment
    `incomplete_threads`.
  - Emit `"incomplete_threads": incomplete_threads` in the `page` record. When it is
    non-zero, `ctx.warn(f"{conversation['id']}: {n} thread(s) not fully expanded; pass
    --all-pages to follow reply cursors")`.
  - `formatters/text.py` `_page`: after the filtered count, append
    `f"{n} thread{'' if n == 1 else 's'} not fully expanded, pass --all-pages"` when
    `record.get("incomplete_threads")`.
  - `HELP` for `conversation`: under `--all-pages`, say it also follows reply cursors
    inside expanded threads; under `--max-messages`, say the ceiling counts top-level
    messages and that inlined replies are bounded per thread by `--limit` and
    `--all-pages` (gate low finding on the ceiling).
- [x] Verify green.

**Satisfies:** D9 (nothing dropped silently), D11 (filtering is visible), P4, CLAUDE.md invariants 4 and 5; Gate-2

**File(s):** `src/slackline/commands/conversation.py`, `src/slackline/formatters/text.py`, `tests/test_cmd_conversation.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_conversation.py -q
```

**Commit message:** `Follow reply cursors when expanding threads and report the ones left unfinished`

---

### Step 6: Render a `thread_broadcast` reply once

Slack surfaces a broadcast reply in channel history and again in the thread's replies.
Under `--threads full` both copies were emitted.

- [x] Write the failing test first: add to `tests/test_cmd_conversation.py`
  - `test_a_broadcast_reply_is_rendered_once_under_its_root`: history
    `[thread_parent, thread_broadcast]`, replies `[thread_parent, thread_reply, thread_broadcast]`,
    `--threads full`: the broadcast `ts` appears exactly once, with `is_reply is True`,
    and appears after `thread_reply`.
  - `test_a_broadcast_whose_root_is_outside_the_window_still_appears`: history
    `[thread_broadcast]` only, `--threads full`: it appears once, from history, and no
    `conversations.replies` call is made (its `thread_ts != ts`).
  - `test_a_broadcast_appears_once_without_thread_expansion`: history
    `[thread_parent, thread_broadcast]`, default `--threads none`: two messages, the
    broadcast once.
  Fails: the first test finds the broadcast `ts` twice.
- [x] Implement in `conversation.py`: keep an `emitted: set` of `ts` per conversation. When
  walking the sorted history, skip a message whose `ts` is already in `emitted`. When
  inlining replies, skip the root `ts` and any `ts` in `emitted`; add every emitted `ts`.
  The root is always older than its broadcast, so the root's expansion runs first and
  the history copy is the one skipped. Comment the reasoning.
- [x] Verify green.

**Satisfies:** D9, P8 (a transcript a model can read); Gate-3

**File(s):** `src/slackline/commands/conversation.py`, `tests/test_cmd_conversation.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_conversation.py -q
```

**Commit message:** `Emit a broadcast reply once, under its root, rather than from history as well`

---

### Step 7: Multi-conversation reads are a supported feature

They already work; this step makes them tested and documented so they cannot regress
into the "not built" state the README claims.

- [x] Write the failing test first: add to `tests/test_cmd_conversation.py`
  - `test_several_selectors_read_several_conversations_with_a_header_and_page_each`:
    `--channel #announcements --channel #engineering`: exit 0; record types in order are
    `conversation, message..., page, conversation, message..., page`; the two
    `conversation` records carry `C100` and `C101`; each `page` carries the matching
    `conversation_id`.
  - `test_the_audit_summary_lists_every_conversation_read`: after the run,
    `ctx.audit["conversations"] == ["C100", "C101"]`.
  Note: these pass already if the behavior is intact. That is acceptable here because the
  step's purpose is to pin existing behavior, not to fix a defect; say so in the test
  docstring. Confirm they fail if `conversation.run` is made to stop after the first
  conversation (do this by hand, do not commit it).
- [x] Implement docs only:
  - `conversation.py` `HELP`: "Selectors are repeatable. Several conversations are read in
    one invocation, each with its own header and trailing page record."
  - `README.md`: in the `conversation` section add an example
    `slackline-read conversation --channel '#eng' --channel '#ops' --since 24h` and one
    sentence about per-conversation framing; delete the "Multiple conversations per
    invocation" row from "What is not built yet".
  - `docs/plans/development-roadmap.md`: in Phase 2.0, replace the multi-conversation
    bullet with a note that it shipped in this plan.
- [x] Verify green.

**Satisfies:** P1, P2, PRD 9.4; roadmap Phase 2.0 bullet; Gate-6 (capability half)

**File(s):** `src/slackline/commands/conversation.py`, `README.md`, `docs/plans/development-roadmap.md`, `tests/test_cmd_conversation.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_conversation.py -q
```

**Commit message:** `Document and pin multi-conversation reads, which the loop already supports`

---

## PR 2: Directory and resolver

Branch: `fix/directory-resolver`

### Step 8: `channels --include-archived` shows archived conversations

- [x] Write the failing test first:
  - Add two rows to `tests/fixtures/directory.json` `conversations`: `C102`, name
    `old-project`, public, `is_archived: true`, `num_members: 3`; and `C103`, name
    `engineering`, private, `is_archived: true` (a stale duplicate of the live `C101`).
    Both names are synthetic.
  - `tests/test_cmd_inventory.py`:
    `test_archived_conversations_are_hidden_by_default` (`C102` and `C103` absent),
    `test_include_archived_shows_them` (both present with `archived is True`).
  - `tests/test_directory.py`: `test_the_directory_asks_slack_for_archived_rows_too`: the
    `users.conversations` call has `exclude_archived` equal to `False` (or `"false"`).
  - `tests/test_resolve.py`: `test_a_live_conversation_wins_over_an_archived_one_with_the_same_name`:
    `resolve(channels=["#engineering"])` returns only `C101`;
    `test_an_archived_conversation_is_reachable_by_id`: `resolve(conversations=["C103"])`
    returns it; `test_a_name_that_only_matches_archived_rows_resolves_to_them`:
    `resolve(channels=["#old-project"])` returns `C102`.
  Fails: the request sends `exclude_archived="true"`, so archived rows never enter the
  directory, and `--include-archived` shows nothing.
- [x] Implement:
  - `directory.py` `conversations()`: `exclude_archived=False`. Comment: the flag exists
    to let a reader see archived history; filtering happens locally so the cache holds
    the whole inventory (D5, D7).
  - `directory.py` `find_conversations_by_name()`: after computing `exact`, return
    `[c for c in exact if not c["archived"]] or exact`. Same for the ID branch.
  - `channels.py`: no change to the filter; confirm the help text is accurate.
  - `README.md`: mention `--include-archived` under `channels`, and note that a
    conversations cache written before this change lacks archived rows until
    `--refresh-cache` or its one-hour TTL.
- [x] Verify green. Check that every pre-existing inventory, directory, and resolver test
  still passes with the two new fixture rows.

**Satisfies:** D5, D7, P1, PRD 9.2; Gate-4

**File(s):** `src/slackline/directory.py`, `tests/fixtures/directory.json`, `tests/test_cmd_inventory.py`, `tests/test_directory.py`, `tests/test_resolve.py`, `README.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_inventory.py tests/test_directory.py tests/test_resolve.py -q
```

**Commit message:** `Fetch archived conversations so --include-archived can actually show them`

---

### Step 9: `resolve` reports verified group DM members

- [x] Write the failing test first: `tests/test_resolve.py`
  - `test_a_group_dm_found_by_the_fallback_scan_reports_its_verified_members`:
    `resolve(with_users=["@joan", "@jamien"])[0]["members"]` equals
    `["U001", "U002", "U007"]` (sorted), not the name-derived `["U001", "U002", "U004"]`.
  - `test_a_name_matched_group_dm_also_reports_verified_members`:
    `resolve(with_users=["@joan", "@kim"])[0]["members"] == ["U001", "U002", "U004"]`.
  - `test_a_tie_report_prints_each_candidates_members`: in the genuine-tie test's setup,
    the report contains `"members:"` and `"@kim"` on both candidate lines.
  - `tests/test_cmd_resolve.py` (new): with the `ctx_for`-style fixture from
    `test_cmd_conversation.py` and `conversations.members` returning per-channel lists as
    in `test_resolve.py`'s `MEMBERS`, `resolve --with @joan --with @jamien --format jsonl`
    emits a `resolution` whose single match has `members == ["@jamien", "@joan", "@sam"]`
    (handles, sorted).
  Fails: `members` comes from `_shape()`'s name parsing.
- [x] Implement in `resolve.py`:
  - `_resolve_with`: return `[dict(c, members=sorted(self._members_of(c["id"]))) for c in confirmed]`.
    The verified list is already in `_member_cache`, so this costs no extra call.
  - Tie report candidates: `f"{c['id']}  {c['label']}  (name: {c['name']})  members: "
    + ", ".join(sorted(self.directory.handle_for(u) for u in self._members_of(c["id"])))`.
  - `commands/resolve.py`: sort the rendered member handles.
  - Comment in `directory.py` `_shape()`: name-derived members are a pre-filter only;
    the resolver replaces them with the verified set (D6).
- [x] Verify green.

**Satisfies:** D6 step 5, D8, PRD 9.3; Gate-7

**File(s):** `src/slackline/resolve.py`, `src/slackline/commands/resolve.py`, `src/slackline/directory.py`, `tests/test_resolve.py`, `tests/test_cmd_resolve.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_resolve.py tests/test_cmd_resolve.py -q
```

**Commit message:** `Report the verified member set for group DMs instead of the one parsed from the name`

---

## PR 3: Hardening

Branch: `fix/hardening`

### Step 10: Audit log is owner-only

- [x] Write the failing test first: `tests/test_audit.py`
  - `test_the_log_file_is_created_owner_only`: after `record()`, `stat(path).st_mode & 0o777 == 0o600`.
  - `test_the_state_directory_is_created_owner_only`: `stat(path.parent).st_mode & 0o777 == 0o700`.
  - `test_an_existing_looser_file_is_tightened`: pre-create the file with `0o644`, record,
    assert `0o600`.
  Fails: default umask permissions.
- [x] Implement in `audit.py` `record()`: `path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)`
  then `os.chmod(path.parent, 0o700)` in a try; open with
  `fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)` and
  `os.fdopen(fd, "a", encoding="utf-8")`; `os.chmod(path, 0o600)` in a try. All inside the
  existing `try` so a failure still degrades to a warning. Comment: same reasoning as the
  cache directory, the log names conversations and carries argv (D20).
  **As built, after review:** the parent is chmodded only when Slackline created it
  (the path is user-configurable and may be the working directory or `$HOME`); the file
  mode is set with `os.fchmod` on the open descriptor before anything is written, never
  through a symlink, and a platform without `fchmod` still gets its line.
- [x] Verify green.

**Satisfies:** D20; Gate-8

**File(s):** `src/slackline/audit.py`, `tests/test_audit.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_audit.py -q
```

**Commit message:** `Create the audit log owner-only, matching the cache directory`

---

### Step 11: Cap `Retry-After`

- [x] Write the failing test first: `tests/test_api.py`
  - `test_an_absurd_retry_after_is_refused_rather_than_slept`: `Retry-After: 900`;
    `client.call` raises `SlackApiError` whose message contains `"900"` and `"ceiling"`;
    `slept == []`.
  - `test_a_retry_after_within_the_ceiling_is_still_honored_exactly`: `Retry-After: 120`
    sleeps `[120.0]`.
  - `test_the_ceiling_is_a_named_constant`: `api.MAX_RETRY_AFTER_SECONDS == 300`.
  Fails: the client sleeps 900.
- [x] Implement in `api.py`: `MAX_RETRY_AFTER_SECONDS = 300.0`; in `_wait_for_rate_limit`,
  if `delay > MAX_RETRY_AFTER_SECONDS` raise
  `SlackApiError(f"{method}: Slack asked for a {delay:g}s wait, above the "
  f"{MAX_RETRY_AFTER_SECONDS:g}s ceiling; try again later")`. Add to D17: honored
  exactly up to five minutes, refused beyond, so an intermediary cannot park an agent.
- [x] Verify green.

**Satisfies:** D17; Gate-9

**File(s):** `src/slackline/api.py`, `docs/architecture/architecture.md`, `tests/test_api.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_api.py -q
```

**Commit message:** `Refuse a Retry-After longer than five minutes instead of sleeping through it`

---

### Step 12: Strip terminal control characters from text output

- [x] Write the failing test first: `tests/test_formatters_text.py` (new)
  - `test_escape_sequences_do_not_reach_the_terminal`: a `message` record whose `text` is
    `"\x1b[31mred\x1b[0m and \x07bell"` renders with no `"\x1b"` and no `"\x07"`, and
    still contains `"red"` and `"bell"`.
  - `test_newlines_and_tabs_survive`: `"a\tb\nc"` renders with the tab and both lines.
  - `test_c1_controls_are_stripped_too`: `"x\x85y"` renders as `"xy"`.
  - `test_jsonl_is_untouched`: `format_jsonl` of the same record still contains the
    six-character JSON escape `\u001b`; JSON encoding is the protection there.
  Fails: the text renderer passes the bytes through.
- [x] Implement in `formatters/text.py`: `_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")`
  and `_clean(text) -> str`; apply to every message text line in `_message`, and to
  `label`/`name` fields in the other renderers. Comment: Slack content is untrusted, and
  a human's terminal interprets escapes (P8, gate security review).
- [x] Verify green.

**Satisfies:** P8, P11; Gate-12

**File(s):** `src/slackline/formatters/text.py`, `tests/test_formatters_text.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_formatters_text.py -q
```

**Commit message:** `Strip control characters before message text reaches a terminal`

---

### Step 13: Validate `team_id` before it becomes a filename

- [x] Write the failing test first: `tests/test_cache.py`
  - `test_a_team_id_that_is_not_a_slack_id_is_refused`: `Cache(root=tmp_path, team_id="../x")`
    raises `ValueError`; same for `""` and `"T1/../../etc"`.
  - `test_real_looking_team_ids_are_accepted`: `"T0ABC1234"`, `"T1"`, `"unknown"`.
  Fails: no validation.
- [x] Implement in `cache.py` `__init__`: `re.fullmatch(r"[A-Za-z0-9_-]+", team_id)` or
  raise `ValueError(f"refusing to use {team_id!r} as a cache key")`. Comment: the value
  comes from Slack, but a path component is never built from an unchecked string (D7).
- [x] Verify green.

**Satisfies:** D7; Gate-12

**File(s):** `src/slackline/cache.py`, `tests/test_cache.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cache.py -q
```

**Commit message:** `Refuse a team ID that could not be a Slack ID before it names a cache file`

---

### Step 14: Never drop `text` in favor of a subset of itself

- [x] Write the failing test first:
  - Add fixture `text_superset_of_blocks` to `tests/fixtures/messages.json`: `text`
    `"Deploy finished. Remember to rotate the staging credential."`, `blocks` a single
    `rich_text` section rendering `"Deploy finished."`. Synthetic.
  - `tests/test_render_message.py`:
    `test_text_that_says_more_than_the_blocks_is_kept_in_full`: rendered contains
    `"rotate the staging credential"`;
    `test_and_the_overlap_is_not_repeated`: `rendered.count("Deploy finished.") == 1`.
  Fails: the block rendering is kept and the longer text dropped.
- [x] Implement in `render.py` `render_message()`: replace the `_equivalent` branch with
  three cases on the alphanumeric essences: text within blocks, keep `block_lines`;
  blocks within text, keep `text` only (it already has mentions resolved via
  `resolve_mrkdwn`); otherwise keep both. Rewrite `_equivalent` as
  `_containment(left, right) -> Optional[str]` returning `"left"`, `"right"`, or `None`,
  and update its docstring: the conservative direction is the one that never loses a
  line (D9).
- [x] Verify green, including `test_text_is_not_repeated_when_blocks_say_the_same_thing`
  and `test_genuinely_different_text_and_blocks_are_both_kept` unmodified.

**Satisfies:** D9, P8; Gate-10

**File(s):** `src/slackline/render.py`, `tests/fixtures/messages.json`, `tests/test_render_message.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_render_message.py -q
```

**Commit message:** `Keep the text when it says more than the blocks do`

---

### Step 15: `thread --ts` given a reply's ts reads the thread from its root

Slack's `conversations.replies` answers a reply's `ts` with that one message. The help
text promises any message's `ts` works; make that true with one extra read-only call.

- [x] Write the failing test first: `tests/test_cmd_thread.py`
  - Fixture: `conversations.replies` is a callable keyed on `ts`: for `ROOT_TS` return
    `[thread_parent, thread_reply, thread_broadcast]`; for `thread_reply["ts"]` return
    `[thread_reply]` only.
  - `test_a_reply_ts_reads_the_whole_thread_from_the_root`: `--ts <thread_reply ts>`
    yields three messages, root first, `conversation["thread_ts"] == ROOT_TS`.
  - `test_the_redirect_is_announced`: `ctx.warn` captured; a warning containing
    `"names a reply"` and `ROOT_TS`.
  - `test_the_redirect_makes_exactly_two_calls`: `conversations.replies` called with the
    reply ts, then with `ROOT_TS`.
  - `test_no_redirect_when_the_ts_is_the_root`: one call only, no warning.
  - `test_no_redirect_on_a_continuation_page`: with `--cursor`, one call only.
  Fails: one call, one message, root missing.
- [x] Implement in `thread.py` `run()`: after the first `collect`, if no cursor was given,
  find `root_ts` as the `thread_ts` of the first message carrying one; if
  `root_ts != options["ts"]`, warn and `collect` again with `ts=root_ts`, replacing the
  result. Update `HELP` and the README sentence to say a reply's ts is accepted and costs
  one extra call.
- [x] Add an opt-in live check to `tests/live/test_smoke.py`: skipped unless
  `SLACKLINE_LIVE_TEST=1`, `SLACKLINE_LIVE_CHANNEL`, and `SLACKLINE_LIVE_REPLY_TS` are all
  set; runs `thread --channel $CH --ts $REPLY_TS --format jsonl` and asserts the first
  message has `ts == thread_ts`. Document the two variables in the file's docstring.
- [x] Verify green offline.

**Satisfies:** D13, P4; Gate-13

**File(s):** `src/slackline/commands/thread.py`, `tests/test_cmd_thread.py`, `tests/live/test_smoke.py`, `README.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_thread.py -q
.venv/bin/python -m pytest tests/live -q   # must report skipped
```

**Commit message:** `Read a thread from its root when --ts names one of its replies`

---

### Step 16: `KeyboardInterrupt` exits 130

- [x] Write the failing test first: `tests/test_review_regressions.py`
  - `test_an_interrupt_exits_130_not_1`: monkeypatch `auth.resolve_token` and
    `api.SlackClient.call` as the neighboring tests do; monkeypatch
    `cli_read._load_command` to return an object whose `run` raises `KeyboardInterrupt`
    and whose `HELP` is `""`; `cli_read.main(["whoami"]) == 130`.
  Fails: returns 1.
- [x] Implement: `cli_read.py` returns 130; add `130  interrupted` to `EXIT_CODES`, the D18
  table, and the README exit-code table with one line of rationale (the shell convention,
  so a harness can tell an interrupt from an API failure).
- [x] Verify green.

**Satisfies:** D18, P12; gate nit

**File(s):** `src/slackline/cli_read.py`, `docs/architecture/architecture.md`, `README.md`, `tests/test_review_regressions.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_review_regressions.py -q
```

**Commit message:** `Exit 130 on interrupt so it is distinguishable from an API failure`

---

## PR 4: Documentation that did not move with the code

Branch: `docs/gate-followups`

### Step 17: Credential precedence documented as implemented

- [x] Test-first: n/a, documentation. Verification is the grep below.
- [x] `docs/architecture/architecture.md` D4a: state the actual rule. Without
  `--token-file`, the order is environment, Keychain, 1Password, `.env`. With an explicit
  `--token-file`, the file is consulted first because an explicit instruction overrides
  the ambient environment, and a missing file is an error even when the environment has
  a token. Keep `--token-source` as the way to pin one source.
- [x] `README.md` credentials table: same rule, one sentence under the table.
- [x] `src/slackline/cli_read.py` `USAGE`: reorder or annotate the numbered list to match.
- [x] `.env.example`: same.
- [x] Verify: `grep -n "token-file" docs/architecture/architecture.md README.md src/slackline/cli_read.py .env.example`
  shows the override rule in each, and `.venv/bin/python -m pytest tests/test_auth.py tests/test_review_regressions.py -q`
  is green (the behavior is already tested there).

**Satisfies:** D4a, P16, P18; Gate-5

**File(s):** `docs/architecture/architecture.md`, `README.md`, `src/slackline/cli_read.py`, `.env.example`

**Test:**
```bash
grep -n "token-file" docs/architecture/architecture.md README.md src/slackline/cli_read.py .env.example
.venv/bin/python -m pytest tests/test_auth.py tests/test_review_regressions.py -q
```

**Commit message:** `Document that an explicit --token-file overrides the environment, as the code does`

---

### Step 18: README status, architecture layout, and stale comments

- [x] Test-first: n/a, documentation.
- [x] `README.md`: replace the status paragraph (lines 15-17) with: v0.1, every read command
  built, tested offline, and verified against the live workspace on 2026-08-28 (link the
  roadmap's Phase 1.0 entry). Delete the "A live end-to-end pass" row from the
  not-built table. Add a short "The directory cache" section: location, the three files,
  TTLs, that it holds names, IDs, handles, display names, emails, and group DM
  membership, never message content, and that it is owner-only.
- [x] `docs/architecture/architecture.md`:
  - Section 1 layout: replace `formatters/jsonl.py` and `formatters/raw.py` with
    `formatters/__init__.py  jsonl, json, raw, and the registry`; add `transcript.py`
    and `commands/_common.py` with one-line descriptions.
  - D3 code sample: add `"conversations.members"` so the sample matches `api.py`.
  - D7: add `email` to the list of cached fields.
  - D15: state that `--max-messages` counts top-level messages and how replies are
    bounded (from Step 5).
  - D19: one sentence that records are rendered once at the end, so JSONL is streamable
    to consume but not produced incrementally; bounded by `--max-messages`.
- [x] `src/slackline/directory.py` lines 176-179: the comment says membership is verified
  against `conversations.info`; it is `conversations.members` (D6).
- [x] `docs/plans/slackline-read-mvp.md` Step 14: add a note beside the formatter
  checkbox that jsonl, json, and raw live in `formatters/__init__.py`.
- [x] `docs/plans/development-roadmap.md` Phase 3.1: add a "Before any publication" list:
  first-name handles in `tests/fixtures/directory.json`, the owner's identity, team ID,
  team name, and workspace URL in `tests/test_cmd_whoami.py`, and the real handles quoted
  in D6 and in `tests/test_resolve.py` and `tests/test_directory.py` docstrings, each to be
  replaced or consciously kept (Gate-14).
- [x] Verify: `grep -n "not yet been walked\|not yet done" README.md` returns nothing;
  `grep -n "jsonl.py\|raw.py" docs/architecture/architecture.md` returns nothing;
  `grep -n "conversations.info" src/slackline/directory.py` returns nothing;
  `.venv/bin/python -m pytest -q` still green (the guard test scans the package and the
  conflict-marker test scans the docs).

**Satisfies:** roadmap "Docs move with the code"; D3, D7, D15, D19; Gate-6 (README half), Gate-8 (email documented), Gate-11, Gate-14

**File(s):** `README.md`, `docs/architecture/architecture.md`, `src/slackline/directory.py`, `docs/plans/slackline-read-mvp.md`, `docs/plans/development-roadmap.md`

**Test:**
```bash
grep -n "not yet been walked\|not yet done" README.md; grep -n "jsonl.py\|raw.py" docs/architecture/architecture.md; grep -n "conversations.info" src/slackline/directory.py
.venv/bin/python -m pytest -q
```

**Commit message:** `Bring the README and architecture layout back in line with what shipped`

---

### Step 19: Remove the vacuous branch from the help-text test

- [x] Test-first: the test is the deliverable. In `tests/test_review_regressions.py`
  `test_every_command_exposes_the_help_text_dispatch_relies_on`, delete the
  `try/except ArgumentError: continue`. Every command in `COMMANDS` must import and expose
  a non-empty `HELP`; a missing module is now a failure, not a skip. Confirm by hand that
  adding a bogus name to `COMMANDS` makes it fail (revert; do not commit that).
- [x] Verify green.

**Satisfies:** roadmap "No test passes by asserting broken behavior" (a skip that hides a missing command is the same failure); gate test-truthfulness note

**File(s):** `tests/test_review_regressions.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_review_regressions.py -q
```

**Commit message:** `Make the help-text test fail on a missing command instead of skipping it`

---

### Step 20: Close out

- [x] Test-first: n/a.
- [x] Every checkbox in this plan checked; `docs/plans/development-roadmap.md` moves this
  plan to Completed with the date, and notes that the phase gate should be re-run to close
  the Read-Only MVP gate item.
- [x] Full suite green: `.venv/bin/python -m pytest -q`.
- [x] Live check, read-only, opt-in: `SLACKLINE_LIVE_TEST=1 .venv/bin/python -m pytest tests/live -q`,
  then by hand against the live workspace: a `conversation --all-pages --max-messages 10`
  read followed by `--cursor` continuation, checking no overlap and no gap on the `ts`
  boundary; a `--threads full` read on a channel with threads; `channels --include-archived`.
  Record the results in the PR description. Done 2026-08-30 on PR 4; see its description.
  The roadmap move to Completed happens at merge.

**Satisfies:** PRD section 9; roadmap definition of done for this item

**File(s):** `docs/plans/development-roadmap.md`, `docs/plans/read-only-mvp-gate-fixes.md`

**Test:**
```bash
.venv/bin/python -m pytest -q
```

**Commit message:** `Mark the gate fix plan complete`

---

## Files Modified (Summary)

| File | Steps |
|------|-------|
| `tests/conftest.py` | 1 |
| `tests/test_fake_client.py` | 1 |
| `src/slackline/transcript.py` | 2 |
| `tests/test_transcript_collect.py` | 2 |
| `src/slackline/commands/conversation.py` | 3, 5, 6, 7 |
| `tests/test_cmd_conversation.py` | 3, 5, 6, 7 |
| `tests/test_continuation_e2e.py` | 3 |
| `src/slackline/commands/thread.py` | 4, 15 |
| `tests/test_cmd_thread.py` | 4, 15 |
| `src/slackline/formatters/text.py` | 5, 12 |
| `tests/test_formatters_text.py` | 12 |
| `src/slackline/directory.py` | 8, 9, 18 |
| `tests/fixtures/directory.json` | 8 |
| `tests/test_cmd_inventory.py` | 8 |
| `tests/test_directory.py` | 8 |
| `src/slackline/resolve.py` | 9 |
| `src/slackline/commands/resolve.py` | 9 |
| `tests/test_resolve.py` | 8, 9 |
| `tests/test_cmd_resolve.py` | 9 |
| `src/slackline/audit.py` | 10 |
| `tests/test_audit.py` | 10 |
| `src/slackline/api.py` | 11 |
| `tests/test_api.py` | 11 |
| `src/slackline/cache.py` | 13 |
| `tests/test_cache.py` | 13 |
| `src/slackline/render.py` | 14 |
| `tests/fixtures/messages.json` | 14 |
| `tests/test_render_message.py` | 14 |
| `tests/live/test_smoke.py` | 15 |
| `src/slackline/cli_read.py` | 16, 17 |
| `tests/test_review_regressions.py` | 16, 19 |
| `.env.example` | 17 |
| `README.md` | 7, 8, 15, 16, 17, 18 |
| `docs/architecture/architecture.md` | 11, 16, 17, 18 |
| `docs/plans/slackline-read-mvp.md` | 18 |
| `docs/plans/development-roadmap.md` | 7, 18, 20 |
