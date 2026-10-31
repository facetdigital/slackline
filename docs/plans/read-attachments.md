# Plan: Read attachments, especially images

## Execution Instructions

When executing this plan:

1. **Work step-by-step.** Complete each step fully before moving to the next.
2. **Test-first within each step.** Write the failing test before the implementation, run it, and watch it fail for the reason the step describes. Then implement until it passes. Never edit the test afterwards to make it pass. If a step's test passes before the implementation, stop: either the test is wrong or the defect is not what the step says. Check against `HEAD`, not a staged copy.
3. **Test after each step.** Run the commands under **Test:** and confirm green. Check pytest's own exit status, never a piped tail's.
4. **Commit after each step**, using the given commit message. The body says why.
5. **Update documentation continuously.** README, CLAUDE.md, the architecture doc when a decision changes, the skill, this plan's checkboxes, and the roadmap move in the same PR.
6. **One branch, one PR:** `feat/read-attachments`, merged with `--no-ff`. Never squash, never rebase.
7. **Mark completion.** When every step is done, move "Read attachments, especially images" to Completed in the roadmap. Cutting a release is a separate decision and is not part of this plan.

Conventions: run the suite as `.venv/bin/python -m pytest -q`; cite decision numbers in comments; never use an emdash in any text; fixtures stay synthetic; `FakeClient` in `tests/conftest.py` enforces the allowlists; no runtime dependency (D1); the offline suite stays green from an empty directory with no credential (invariant 10).

**Legend:** `Dn` cites [architecture.md](../architecture/architecture.md). `Pn` and `Rn` cite [product-requirements.md](../requirements/product-requirements.md). "Roadmap" means the "Read attachments, especially images" entry in [development-roadmap.md](development-roadmap.md).

**Decision numbers.** This plan adds D25 (a second egress, guarded by a host allowlist) and D26 (every attachment is accounted for). D23 and D24 are already claimed by the Phase 2.0 plan, which has not landed, so they are skipped rather than renumbered.

---

## Summary

Today a file shared in Slack shows up only as a line like `[file: image.png (PNG, 261.0KB)]` in the message text. Its bytes cannot be fetched, because the token has no `files:read` scope, and nothing tells a model which file it is. This plan adds:

1. **File details in the output.** Each JSONL message record gains a `files` array (ID, name, title, mime type, file type, size, mode, external flag). The text transcript shows the file ID, so a model can name the file it wants.
2. **A `files` command** that saves the attachments of one message into a directory the caller names. The message is selected by conversation and `--ts`, or by a message permalink. Each attachment yields a record: `saved`, with its path, or `skipped`, with the reason. Nothing is dropped silently.
3. **`--save-files DIR` on `conversation` and `thread`**, which saves attachments as the transcript is read, through the same code, bounded by `--max-files`.
4. **A download guard.** A download is a plain HTTPS GET carrying the token, not a Slack method, so the method allowlist (D3) cannot cover it. A host allowlist in `api.py` does: the token is sent only to Slack's own file host over HTTPS, a redirect is followed only to an allowed host, and no other module may open a network connection. Tests fail if either rule weakens.
5. **An audit record of what was fetched**: file ID, mime type, and size. Never the filename or the path, which can say what a file contains (invariant 9).

Out of scope, deferred by decision: OCR and any text extraction from images or documents. This feature saves the bytes and reports where they are; what reads them comes later.

**Decided in planning:**
- A file is selected by its message, never by file ID alone. Fetching by ID would need `files.info` on the method allowlist, which an existing guard test forbids (no `files.*` method). Selecting by message keeps D3's allowlist unchanged: the message is found with `conversations.history` or `conversations.replies`, and it already carries each file's download URL.
- `--save-files` ships in the same plan, sharing one save routine with `files`.
- Saved files are named `<FILE_ID>-<cleaned name>`. Slack file IDs are unique, so names never collide, and fetching again lands on the same path.
- The audit log records file ID, mime type, and size only.

## Requirements

- New PRD requirement **P19**: save the files attached to a message the user can read, to a directory the caller names, and report every attachment as saved or skipped with a reason.
- The method allowlist (D3) is unchanged. The existing guard tests pass unmodified.
- The token reaches only `https://files.slack.com` for downloads (D25), never another host, including through a redirect. No module other than `api.py` imports `urllib.request`.
- A file Slack does not host (external, Google Drive), a deleted file, a file with no download URL, and a file over `--max-bytes` are each skipped with a stated reason, and counted (invariants 4 and 5).
- Missing `files:read` is exit 3, with a message saying to add the scope to the Slack app and reinstall it. That holds whether Slack reports it in the `X-OAuth-Scopes` header or answers the download with its HTML login page.
- Downloads are bounded by default: `--max-bytes` per file (default 100 MB), and `--max-files` per read under `--save-files` (default 50).
- Writes go only inside the output directory: a name cannot escape it, a temporary file is opened with `O_EXCL` and `O_NOFOLLOW`, files are `0600`, and a finished file is moved into place atomically. A directory Slackline creates is `0700`.
- stdout carries records only. File bytes never go to stdout (invariant 7).
- `parse` checks every argument, including a missing `--out`, before any credential is resolved (D18, invariant 10).

## Implementation Steps

### Step 1: File details in records and transcripts

- [ ] Write the failing tests first:
  - `tests/test_transcript_record.py` (new): `message_record` for the `file_only` fixture carries `"files": [{"id": "F01", "name": "rollback-checklist.pdf", "title": "Rollback checklist", "mimetype": "application/pdf", "filetype": "pdf", "size": 184320, "mode": None, "is_external": False}]`; a message with no files carries `"files": []`; no `url_private`, `url_private_download`, or any other URL appears in the record. Fails: there is no `files` key.
  - `tests/test_render_message.py`: `test_a_file_line_names_its_id`: the `file_only` rendering contains `F01`. Fails: the line has no ID. The existing file tests stay unmodified and must keep passing.
- [ ] Implement in `src/slackline/transcript.py`: a `file_summary(item)` helper and a `"files"` key in `message_record`, built from `message.get("files")`, skipping non-dict items. Missing fields are `None`, `is_external` defaults to `False`.
- [ ] Implement in `src/slackline/render.py`: `_render_files` prints `[file F01: rollback-checklist.pdf (PDF, 180.0KB)]`, and omits the ID only when Slack sent none.
- [ ] Update D13's example record in `architecture.md` with a `files` array.
- [ ] Verify green.

**Satisfies:** roadmap "Add structured file metadata to the `jsonl` records"; roadmap design question on what the renderer prints; D9, D13; invariant 4

**File(s):** `src/slackline/transcript.py`, `src/slackline/render.py`, `tests/test_transcript_record.py`, `tests/test_render_message.py`, `docs/architecture/architecture.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_transcript_record.py tests/test_render_message.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Name each file in records and transcripts, so a model can ask for it`

---

### Step 2: The download guard in `api.py` (D25)

- [ ] Write the failing tests first, in `tests/test_api_download.py` (new), driving `SlackClient` with an injectable `file_transport` that records every `(url, headers)` it is handed:
  - `test_an_off_list_host_is_refused_before_any_request`: `https://evil.example/x` raises `ForbiddenHost` (exit 7), and the transport saw no call.
  - `test_plain_http_is_refused`: `http://files.slack.com/...` is refused the same way.
  - `test_the_token_goes_only_to_the_allowed_host`: a download from `https://files.slack.com/files-pri/T1-F1/download/a.png` hands the transport an `Authorization: Bearer` header; the test asserts on the header's presence and host, never on its value.
  - `test_a_redirect_off_the_list_is_refused_without_the_token`: the transport answers 302 with `Location: https://elsewhere.example/a.png`; the client raises `ForbiddenHost`, and no request carrying `Authorization` went to `elsewhere.example`.
  - `test_a_redirect_to_an_allowed_host_is_followed`: 302 to another `files.slack.com` URL is followed, at most 3 hops, and a fourth raises `SlackApiError`.
  - `test_an_html_answer_means_the_scope_is_missing`: `200` with `Content-Type: text/html` raises `AuthError` whose message names `files:read` and says to add it to the Slack app and reinstall.
  - `test_the_byte_ceiling_is_enforced_while_streaming`: chunks totalling more than `max_bytes` raise `FileTooLarge` after reading at most `max_bytes + one chunk`.
  - `test_a_download_retries_like_a_call`: a transport error then success succeeds; HTTP 429 with `Retry-After` waits as D17 says.
  - In `tests/test_readonly_guard.py`, new tests only, existing ones unmodified: `test_only_api_py_imports_urllib_request` (AST scan of the package: `urllib.request` is imported, as `import` or `from`, in `api.py` and nowhere else); `test_the_file_host_allowlist_is_exactly_slack_hosted`: `api.ALLOWED_FILE_HOSTS == frozenset({"files.slack.com"})`.
  - Fails: `ALLOWED_FILE_HOSTS`, `fetch_file`, `ForbiddenHost`, and `FileTooLarge` do not exist.
- [ ] Implement in `src/slackline/errors.py`: `ForbiddenHost(SlacklineError)`, exit 7, message "refusing to send the token to HOST: not on the file download allowlist"; and `FileTooLarge(SlacklineError)`, exit 1, carrying the ceiling.
- [ ] Implement in `src/slackline/api.py`:
  - `ALLOWED_FILE_HOSTS = frozenset({"files.slack.com"})`, beside `READ_ONLY_METHODS`, with a comment citing D25.
  - `_check_file_url(url)`: the scheme must be `https` and the host must be in the set, compared after lowercasing, with no userinfo and no port other than 443. Refuses before any I/O.
  - `_urllib_file_transport(url, headers, timeout)`: a urllib opener whose redirect handler never follows on its own and returns the 3xx response to the caller, so the client decides every hop. Returns status, headers, and an iterator of chunks.
  - `SlackClient.fetch_file(url, sink, max_bytes)`: checks the URL, attaches the token only after the check, and follows a redirect only after checking the new URL. It refuses `text/html`, streams chunks into `sink(chunk)` while enforcing `max_bytes`, and reuses the D17 retry policy for transport errors, 429, and 5xx. Returns the byte count.
- [ ] Add D25 to `architecture.md`: why a download is a second egress the method allowlist cannot see, the host rule, the redirect rule, the one-module rule, and why `files.info` stays banned. Amend D18's row for 7 to "Refused by an allowlist: a Slack method outside the read-only set, or a file host outside the download set". Amend D3 with one sentence pointing to D25.
- [ ] Verify green.

**Satisfies:** roadmap "Stay read-only by construction"; R1, P14; D3, D4b, D17, D25; invariants 1 and 2

**File(s):** `src/slackline/api.py`, `src/slackline/errors.py`, `tests/test_api_download.py`, `tests/test_readonly_guard.py`, `docs/architecture/architecture.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_api_download.py tests/test_readonly_guard.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Guard file downloads with a host allowlist the method allowlist cannot provide`

---

### Step 3: Saving one file safely (`files.py`, D26)

- [ ] Extend `FakeClient` in `tests/conftest.py` with `fetch_file(url, sink, max_bytes)`, served from a URL-to-bytes map, enforcing `api._check_file_url` the same way the real client does, and raising `FileTooLarge` past `max_bytes`. Test it in `tests/test_fake_client.py`.
- [ ] Write the failing tests first, in `tests/test_files.py` (new), against `tmp_path`:
  - `test_a_hosted_file_is_saved_under_its_id`: a file `{"id": "F01", "name": "a.png", "url_private_download": "https://files.slack.com/...", "size": 3}` is written as `F01-a.png` with the exact bytes, mode `0600`, and the record is `{"type": "file", "status": "saved", "file_id": "F01", "name": "a.png", "mimetype": ..., "size": 3, "path": "<abs path>", "conversation_id": ..., "ts": ...}`.
  - `test_a_name_cannot_escape_the_directory`: names `../../x`, `/etc/passwd`, `a/b`, `..`, an empty name, and one with control characters all land directly inside the output directory, as `F01-<cleaned>` or `F01-file`. Names are capped at 120 characters, keeping the extension.
  - `test_fetching_again_replaces_the_same_path`: two saves of F01 produce one file with the second content, and no temporary file is left behind.
  - `test_a_symlink_at_the_temp_path_is_not_followed`: a symlink planted at the predictable temp name makes the save fail for that file (skipped, with the reason); the link's target is untouched.
  - Skips, each a `"status": "skipped"` record with a `reason`, and no request made: `is_external` or `mode == "external"` ("not hosted by Slack"); `mode == "tombstone"` ("deleted"); no `url_private_download` ("Slack sent no download URL for this file"); declared `size > max_bytes` ("larger than --max-bytes N").
  - `test_an_off_list_host_is_skipped_and_warned`: a hosted file whose URL is on another host is skipped ("download host not allowed") and a warning goes to stderr, with no request made.
  - `test_streaming_past_the_ceiling_skips_and_cleans_up`: a file that declares 3 bytes but streams more than `max_bytes` is skipped, and the partial temp file is gone.
  - `test_the_output_directory_is_created_owner_only`: a missing `out/sub` is created; the leaf Slackline created is `0700`, and an existing directory's mode is left alone.
  - `test_a_missing_scope_is_not_a_skip`: `AuthError` from `fetch_file` propagates, so the command fails with exit 3 rather than reporting a skip per file.
  - Fails: `slackline.files` does not exist.
- [ ] Implement `src/slackline/files.py`: `safe_name(file_id, name)`, `prepare_dir(path)`, and `save(client, item, out_dir, max_bytes, conversation_id, ts, warn) -> record`. The temp file is `.<FILE_ID>.part` in the output directory, opened `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW`, mode `0600`, removed on any failure; `os.replace` moves it into place.
- [ ] Add D26 to `architecture.md`: every attachment yields exactly one record, saved or skipped; the skip reasons; the naming rule and why the ID prefix; the write-safety rules.
- [ ] Verify green.

**Satisfies:** roadmap design questions on filename collisions, path safety, refusing files Slack does not host, and a size ceiling; P13; D15, D26; invariants 4 and 5

**File(s):** `src/slackline/files.py`, `tests/test_files.py`, `tests/conftest.py`, `tests/test_fake_client.py`, `docs/architecture/architecture.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_files.py tests/test_fake_client.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Save an attachment inside the output directory, or say why not`

---

### Step 4: The `files` command

- [ ] Write the failing tests first, in `tests/test_cmd_files.py` (new):
  - `test_files_saves_every_attachment_of_a_top_level_message`: with `conversations.history` serving the message at `--ts` (`oldest == latest == ts`, `inclusive`, `limit=1`), both files are saved and two `file` records plus a trailing `page` record `{"files_saved": 2, "files_skipped": 0}` are emitted.
  - `test_files_finds_a_reply_through_replies`: `conversations.history` returns nothing for the ts, so `conversations.replies` (`ts=T`) is asked and the message whose `ts == T` is used.
  - `test_file_narrows_to_named_files`: `--file F02` saves only F02. `--file F99`, which is not on the message, is exit 5 with the message's file IDs listed as candidates.
  - `test_a_message_with_no_files_is_not_an_error`: exit 0, a page record with zero counts, and a warning on stderr.
  - `test_a_missing_message_is_exit_5`.
  - `test_a_scopes_header_without_files_read_is_exit_3_before_any_download`: when `client.last_headers` carries `X-OAuth-Scopes` without `files:read`, the command raises `AuthError` and `fetch_file` was never called. Without the header, the command proceeds, and the HTML check in Step 2 is the backstop.
  - Argument checks, all exit 2 with no credential resolved (driven through `cli_read.main` with `SLACK_USER_TOKEN` unset): missing `--out`; missing `--ts`; a time window flag; `--max-bytes 0`; an unknown option.
  - `tests/test_formatters_text.py`: a `file` record renders as `[saved F01 a.png (PNG, 3B) -> /abs/F01-a.png]` or `[skipped F02 b.png: not hosted by Slack]`, and the page line includes `2 files saved; 1 skipped`.
  - Fails: there is no `files` command.
- [ ] Implement `src/slackline/commands/files.py` with `HELP`, `parse`, and `run`. It uses `_common` selectors with the same single-conversation rule as `thread` (exit 4 when several match), `--ts` required, `--file` repeatable, `--out` required, and `--max-bytes N` (default 104857600). The message is found with `conversations.history` and then `conversations.replies`. Records go through `ctx.emit`.
- [ ] Add `files` to `COMMANDS` and `USAGE` in `cli_read.py`. Add the `file` renderer to `formatters/text.py`, and file counts to the `page` renderer when present.
- [ ] Verify green.

**Satisfies:** P19; roadmap "Download files attached to messages, images first, to a caller-named directory, and report each saved path with its mime type, size, and the message it came from"; D8, D18, D19; invariants 3, 6, and 7

**File(s):** `src/slackline/commands/files.py`, `src/slackline/cli_read.py`, `src/slackline/formatters/text.py`, `tests/test_cmd_files.py`, `tests/test_formatters_text.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_files.py tests/test_formatters_text.py tests/test_cli_argument_errors.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Add files: save a message's attachments to a directory you name`

---

### Step 5: Select the message by permalink

- [ ] Write the failing tests first, in `tests/test_permalink.py` (new): `parse_message_permalink("https://example.slack.com/archives/C0123ABCD/p1756310012000100")` is `("C0123ABCD", "1756310012.000100")`; a `?thread_ts=...&cid=...` query is accepted and ignored, since the `p` number names the message itself; a non-Slack host, a missing `p` segment, a file permalink (`/files/U.../F.../name`), and a malformed number are `ArgumentError` with a message saying what a message permalink looks like. In `tests/test_cmd_files.py`: `--permalink URL` works in place of a selector plus `--ts`, and combining it with either is exit 2.
- [ ] Implement `parse_message_permalink` in `src/slackline/commands/_common.py`, so `thread` can adopt it later without a second spelling. Wire `--permalink` into `files`.
- [ ] Verify green.

**Satisfies:** roadmap likely shape "<id or permalink>"; P2; D8

**File(s):** `src/slackline/commands/_common.py`, `src/slackline/commands/files.py`, `tests/test_permalink.py`, `tests/test_cmd_files.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_permalink.py tests/test_cmd_files.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Accept a message permalink in files, the form people actually copy`

---

### Step 6: `--save-files` on `conversation` and `thread`

- [ ] Write the failing tests first:
  - `tests/test_cmd_conversation.py`: with `--save-files DIR`, each message's `file` records follow its `message` record. The page record carries `files_saved` and `files_skipped`. Messages are unchanged, and without the flag no `fetch_file` call happens and no `files_*` counts appear.
  - `test_max_files_bounds_a_save`: with `--max-files 1` and three attachments, one is saved and two are skipped with reason "over --max-files 1", counted in the page record.
  - `test_a_save_does_not_change_the_read`: the message records, the cursor, and `truncated_by` are identical with and without `--save-files`, so saving is never a filter.
  - `tests/test_cmd_thread.py`: the same flag on `thread`, saving root and reply attachments.
  - Argument checks, exit 2 with no credential: `--max-files` without `--save-files`; `--max-files 0`; `--max-bytes` without `--save-files`.
  - Fails: unknown option `--save-files`.
- [ ] Implement a shared `save_message_files(ctx, message, conversation_id, plan, counts)` in `files.py`, so `conversation` and `thread` cannot drift (CLAUDE.md, shared behavior goes in a shared module). Wire it into both commands' emit loops and page records. Scope preflight as in Step 4, once per invocation.
- [ ] Verify green.

**Satisfies:** roadmap "or a flag on `conversation` and `thread` that saves a transcript's attachments as it reads"; P13; D15, D16; invariant 5

**File(s):** `src/slackline/files.py`, `src/slackline/commands/conversation.py`, `src/slackline/commands/thread.py`, `src/slackline/transcript.py`, `tests/test_cmd_conversation.py`, `tests/test_cmd_thread.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_conversation.py tests/test_cmd_thread.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Let conversation and thread save attachments as they read`

---

### Step 7: The audit log records what was fetched, not what it was

- [ ] Write the failing tests first, in `tests/test_audit.py`: after a `files` run that saves F01 and skips F02, the audit line has `"files": [{"id": "F01", "mimetype": "image/png", "size": 3}]`. The line does not contain the filename, the output path, or the skipped file's name. A run with no downloads has `"files": []`. Fails: there is no `files` field.
- [ ] Implement: `Context.emit` collects saved `file` records into `ctx.audit["files"]` as `{id, mimetype, size}`. `audit.record` and `FIELDS` gain `files`.
- [ ] Amend D20 in `architecture.md`: the new field, and why the name and path are excluded.
- [ ] Verify green.

**Satisfies:** roadmap "every fetch writes an audit log line that records what was fetched (ID, name, size), never its content", narrowed in planning to exclude the name; D20; invariant 9

**File(s):** `src/slackline/cli_read.py`, `src/slackline/audit.py`, `tests/test_audit.py`, `docs/architecture/architecture.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_audit.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Audit downloads by ID, type, and size, never by name`

---

### Step 8: Scope, docs, and the skill

- [ ] Test-first: the existing drift tests define done. `tests/test_skill.py` fails while the skill names a flag or command the CLI lacks, so update the skill and the CLI help together and run it. Add one test to `tests/test_package.py`: `docs/slack-app-manifest.yml` requests `files:read`, and requests no scope ending in `:write`. Fails: the manifest lacks `files:read`.
- [ ] `docs/slack-app-manifest.yml`: add `files:read` to the user scopes, and say in the header comment what it is for.
- [ ] `README.md`: the quick start's sample `whoami` output gains `files:read`; a `files` section under Commands, and `--save-files`, `--max-files`, and `--max-bytes` under `conversation` and `thread`; an "Upgrading from 0.2" note: add `files:read` to your Slack app and reinstall it, or downloads fail with exit 3; remove the "Downloading attachments" row from "What is not built yet".
- [ ] `docs/reference.md`: `file` records and the page record's file counts; exit 7's wider meaning.
- [ ] `.claude/skills/slackline/SKILL.md`: a recipe step for attachments (read with `--format jsonl`, see `files`, then fetch by message); exit 7's wider meaning.
- [ ] `CLAUDE.md`: invariant 1 names the second guard, that the token goes only to `ALLOWED_FILE_HOSTS` and only `api.py` opens connections. Add `files.py` to the layout table.
- [ ] `docs/requirements/product-requirements.md`: add P19 under 5.1, and a row in R1's explanation for the download egress.
- [ ] Roadmap: check this plan's boxes; record anything Step 9 learns.
- [ ] Verify green.

**Satisfies:** P19; R2; "Docs move with the code"; invariant 1

**File(s):** `docs/slack-app-manifest.yml`, `README.md`, `docs/reference.md`, `.claude/skills/slackline/SKILL.md`, `CLAUDE.md`, `docs/requirements/product-requirements.md`, `docs/architecture/architecture.md`, `docs/plans/development-roadmap.md`, `tests/test_package.py`, `tests/test_skill.py`

**Test:**
```bash
.venv/bin/python -m pytest -q
```

**Commit message:** `Document attachments, and ask for files:read in the manifest`

---

### Step 9: Live verification, read-only and opt-in

Requires the owner action first: add `files:read` to the Slack app, reinstall it, and confirm `slackline-read whoami` lists the scope.

- [ ] Test-first: `tests/live/test_live_files.py`, skipped unless `SLACKLINE_LIVE_TEST=1` and `SLACKLINE_LIVE_FILE_PERMALINK` names a message with a small image. It saves into `tmp_path`, asserts one `saved` record whose file size equals the record's `size`, and asserts the audit line has the ID and no name.
- [ ] Run it, and record in the roadmap entry, as results, not URLs or names:
  - Whether `url_private_download` answered directly or redirected, and to which host. A redirect to another Slack-owned host is a design change: stop and bring it to the owner before widening `ALLOWED_FILE_HOSTS` and D25.
  - Whether the `X-OAuth-Scopes` header arrived on API calls, which decides whether Step 4's preflight or Step 2's HTML check is the one that fires in practice.
  - Whether `conversations.history` returned the top-level message for its own ts, and `conversations.replies` the reply.
  - What happens without `files:read`, on a token from before the scope was added, if one is still at hand: expect exit 3 with the reinstall message.
- [ ] Full suite green, and the empty-directory run: `cd "$(mktemp -d)" && <repo>/.venv/bin/python -m pytest <repo>/tests -q`.

**Satisfies:** D22; PRD section 9's spirit for the new surface; roadmap exit

**File(s):** `tests/live/test_live_files.py`, `docs/plans/development-roadmap.md`, `docs/plans/read-attachments.md`

**Test:**
```bash
SLACKLINE_LIVE_TEST=1 SLACKLINE_LIVE_FILE_PERMALINK='<permalink>' .venv/bin/python -m pytest tests/live/test_live_files.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Verify attachment downloads against a real workspace`

---

## Files Modified (Summary)

| File | Steps |
|------|-------|
| `src/slackline/transcript.py` | 1, 6 |
| `src/slackline/render.py` | 1 |
| `src/slackline/api.py` | 2 |
| `src/slackline/errors.py` | 2 |
| `src/slackline/files.py` (new) | 3, 6 |
| `src/slackline/commands/files.py` (new) | 4, 5 |
| `src/slackline/commands/_common.py` | 5 |
| `src/slackline/commands/conversation.py` | 6 |
| `src/slackline/commands/thread.py` | 6 |
| `src/slackline/cli_read.py` | 4, 7 |
| `src/slackline/audit.py` | 7 |
| `src/slackline/formatters/text.py` | 4 |
| `tests/test_transcript_record.py` (new) | 1 |
| `tests/test_render_message.py` | 1 |
| `tests/test_api_download.py` (new) | 2 |
| `tests/test_readonly_guard.py` | 2 (new tests only) |
| `tests/conftest.py` | 3 |
| `tests/test_fake_client.py` | 3 |
| `tests/test_files.py` (new) | 3 |
| `tests/test_cmd_files.py` (new) | 4, 5 |
| `tests/test_formatters_text.py` | 4 |
| `tests/test_permalink.py` (new) | 5 |
| `tests/test_cmd_conversation.py` | 6 |
| `tests/test_cmd_thread.py` | 6 |
| `tests/test_audit.py` | 7 |
| `tests/test_package.py` | 8 |
| `tests/live/test_live_files.py` (new) | 9 |
| `docs/architecture/architecture.md` | 1, 2, 3, 7, 8 |
| `docs/slack-app-manifest.yml` | 8 |
| `README.md` | 8 |
| `docs/reference.md` | 8 |
| `.claude/skills/slackline/SKILL.md` | 8 |
| `CLAUDE.md` | 8 |
| `docs/requirements/product-requirements.md` | 8 |
| `docs/plans/development-roadmap.md` | 8, 9 |
