# Complete review: retrieval, composition and recovery

`drive.py` applies every rule in this file exactly as written. It is here so you know what each
printed call and placement means, and so the same rules hold for any Envelope you fetch and place
yourself. Commands use `<skill directory>` (the folder holding `SKILL.md`) and run from the run
directory, with `KABO_STATIC_RUN_DIR` set to it on the same command line.

## Retrieval rules

Keep public and owner modes explicit. Retain the canonical handle verified for the user's
requested account.

**Instagram.** Call `instagram_resolve_profile`, then retrieve Feed and Reels through
`instagram_list_posts` and `instagram_list_reels`. Persist every page and keep Feed and Reels as
separate comparable formats.

- The public sample is at most 100 persisted rows across Feed and Reels **combined**. While both
  operations still have another page and the combined count is under 100, take the next page from
  the format that currently has fewer persisted rows. Stop both as soon as the combined count
  reaches 100, even if either side still has `more_available`. Do not spend the whole cap on one
  format first.
- Hitting the combined cap is the latest bounded sample, not the account's complete history: keep
  coverage incomplete, and do not attest an operation as complete while it still had
  `more_available` or was cut off by the cap. Attest a paginated operation as complete only after
  its final persisted Envelope says `more_available=false` and the combined count stayed at or under
  100; intermediate cursors are not incomplete coverage after that terminal page.
- A known handle or an `instagram.com/<handle>/reels/` URL is one account's history. Never use a
  topic-search or trend collector in its place.
- When the handle does not resolve, `fetch` runs `instagram_search_accounts` for it, prints the
  candidates and stops.

**YouTube.** Resolve the requested handle (or channel id) with `youtube_get_channel`, then call
`youtube_list_channel_uploads` with `max_results: 50`. When `data.coverage.next_page_token` is
non-null, make one cursor follow-up, for at most 100 uploads, and persist both pages. If the second
page still has a token, the run covers only the latest 100 uploads and the Snapshot is incomplete;
only a final persisted page with an explicit `next_page_token=null` attests complete coverage. A
completed call by itself is not proof of exhaustive channel coverage.

**TikTok.** Call `tiktok_list_account_posts` once with the canonical handle and `limit: 100`; the
tool pages internally. Persist the single returned Envelope. Attest complete coverage only when
`more_available=false`. Successful account-post resolution is the account-existence evidence for
this review: do not gate it on a separate handle check. Do not use a public account snapshot to
infer private TikTok analytics.

**Assembly.** `build_public_snapshot.py` assembles every persisted public content Envelope with the
resolved platform and the window: the user's exact requested window when one exists, otherwise
`auto`, which derives the observed public-history span. The profile or channel Envelope is included
so `creators[]` carries the public biography and follower count already fetched. Instagram Feed and
Reels rows stay with the resolved account even when Instagram attributes a collab to the other
handle. Never drop Feed rows to force them into the Reel cohort.

## Analysis and the compact input

- `account_analyzers.py --operation all` runs with the canonical handle of the creator whose
  resolved platform account id matches the requested account (on YouTube this may be the channel
  title or custom handle normalized into the Snapshot, not the user's alias). It resolves that
  creator deterministically and fails closed when the handle is missing or ambiguous; owner
  snapshots need no focus handle. Its coverage, baselines, rankings, outlier ratios, metric values
  and claim boundaries are authoritative.
- YouTube format evidence is authoritative too: declarative provider fields and canonical Shorts
  URLs are `explicit`; a duration-only fallback is `provisional`. Format confidence, source,
  cohort classification counts and source limitations travel through analysis and synthesis. A
  provisional baseline is usable fallback evidence, never a platform-declared format fact, and
  the Creator-facing evidence line says when a cited same-format multiple depends on
  duration-inferred classification, without exposing enum or field names.
- `build_synthesis_input.py` checks that rankings, outliers, baselines and coverage agree, then
  writes one compact `account-review-synthesis-input.v1` view without changing the complete
  analysis. It carries each item's public media length (`duration_seconds`) when the platform
  reported one, so a length the data has is never described as unavailable, and the stored profile
  Envelope's biography and follower count into `scope`. `--user-question` carries the user's
  original request verbatim.

## Passing the original request

Pass the user's complete original request to `fetch` on standard input with `--question-file -`,
preserving the user's platform, handle or channel id and window options. Never write a question
file first, and never write question or synthesis files into the Skill directory.

When the request contains a line that is exactly `TEXT`, or the host cannot feed standard input to
a command, pass the same request as one argument instead: `--question '<original request>'`.
Enclose it in single quotes and encode each original apostrophe as `'"'"'`. Keep embedded newlines
and trailing whitespace inside that literal. This encoding changes no argument bytes; backticks,
`$` and command-substitution text remain data. Never paste the request into double-quoted or
unquoted shell source or use `eval`. A `--placed` resume repeats the first command exactly,
including the same question argument or standard input, and only adds `--placed`; any other change
is a different request, which the run refuses. Do not reserve again or replace the stored question.

## A TikTok post already read

Only when the request comes with an actual `linked-media-evidence.v1` file for a TikTok post
(directly or inside its `linked_media` field), pass that exact file as `--linked-evidence <file>` on
`fetch`. The driver binds it to the original question and account, preserves its photo or video
type and original link, and keeps it across `--placed` hand-overs. Keep the file intact; never
manufacture one from connector fields. Do not infer a post's format from the account-list operation
or compare a photo post against a video baseline; insufficient same-format samples remain
insufficient.

## Composition and validation

- `report` checks the complete author schema and exact source IDs before composition, then runs
  `compose_review.py` with the complete `AccountAnalysis` to bind selected patterns to Analyzer
  URLs, metrics, baselines and account identity (with the same profile Envelope as the compact
  input).
- The Composer attaches `content_profile.caption_phrases` deterministically from the creator's own
  caption text, and `extensions.content_style.hashtags` when the synthesis did not supply them.
  Never write, reorder or translate `caption_phrases`: they carry the creator's literal wording in
  the original content language, and downstream retrieval matches them literally.
- The Composer projects the composed facts to `ACCOUNT_REVIEW.md` deterministically: the diagnosis,
  what works, what hurts, and next actions grouped as one move to do first, moves worth testing and
  moves to hold off on. Never invoke another model to rewrite, translate, shorten or expand it. For
  Simplified Chinese (`zh`, `zh-CN`, `zh-SG`, or `zh-Hans` with CN/SG) it renders fixed section
  labels, public identity, metrics and conditional platform checks in Chinese; other languages
  keep English labels, and semantic strings stay in `output_language`. Right after the executive
  diagnosis it renders the sample-window start and end and the authored `data_gaps` inline,
  preserving values and gap order; missing dates are not inferred. There are no standalone
  evaluation-basis or data-boundary sections: complete baselines and coverage stay in the native
  JSON.
- `report` then runs `validate_artifacts.py`, and `validate_creator_report.py` on
  `ACCOUNT_REVIEW.md` with every connector Envelope this run stored: that validator reads their
  source strings and rejects the report if any reached the Creator-facing text. Both validators
  must pass before the result is presented. When no `needs_revision` came back earlier in this
  run, `report` says the report is final and is the final step; after a correction,
  `verify-report` reruns both on the existing bytes as the final step. Do not run them by hand.

## Corrections and closed runs

- If a `drive.py` command stops with an error while the run remains open, its message names what to
  fix. Fix it and run the same command again (a fetch exactly as first given); add `--placed` when it says its data calls had
  already gone out, so they are not made twice.
- A `growth` input rejection and a `needs_revision` from `report --allow-authoring-correction` are
  corrections in this same open run. Invalid source or provenance, a closed run or any other
  failure is fatal.
- A run is closed once its final step succeeded or a step failed with no correction named. Stop
  that attempt and return its failure. Do not repair or rewrite its synthesis, reports, envelopes
  or state, even to correct a content ID or profile field; preserve the failed bytes and recorded
  hashes. A later authorized attempt uses a new run; a corrected-Skill regression is a separate
  test round, never a continuation of the closed run.
- If a message says the network or the data service was interrupted, tell the user to try again in
  a few minutes; do not guess a cause.
- If the printed compact input or schema is truncated by the host, obtain the remaining output
  before the synthesis. If the host cannot return it, read only this run's `synthesis_input.json`
  in complete bounded chunks and run
  `cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/profile_contract.py'` to
  print the fully resolved synthesis schema. This is the only exception to the no-readback rule: it
  does not permit reading the full analysis, another fetch, or guessing missing content IDs or
  nested fields.

## Placing Envelopes you fetch yourself

In a complete review `drive.py` places every Envelope. Outside it (an engagement-rate profile,
for example), fetch each complete result as `SKILL.md` says, then persist every Envelope this run
fetched, one per Envelope in fetch order (a batch of calls returns several):

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --dest snapshot/envelope-NN.json --connector <connector_id> --operation <operation> --handle <verified handle> --any-status
```

`connector_id` and `operation` are the fields inside the result (for example `instagram-discovery`
and `resolve_profile` for `instagram_resolve_profile`). Retrieve what a step needs first: the
profile and the content pages are independent once the handle is verified, so send those calls in
the same response, then place one operation's responses with one command, in the order the calls
were made (one `--handle` per target when the operation needs it):

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_many.py' --connector <connector_id> --operation <operation> --dest snapshot/envelope-NN.json --dest snapshot/envelope-MM.json --handle <verified handle> --handle <verified handle> --any-status
```

Each persist takes the earliest staged response of that connector and operation not yet placed, so
the order of the chain binds each response to its file. It copies the staged complete copy, never
the tool result you were shown. Calls you make yourself are always persisted with `--any-status`, so
a failed response is kept as that route's evidence and cannot slide the next response into its slot.
A result `static_host.py fetch` or `stage` refused (a tool error with no connector evidence, or a
job still queued) was never staged: do not persist it; record that route's gap and continue. Never
write `envelope-NN.json` yourself, and never `echo` or `python -c` a connector payload. If the
script finds no complete copy of a result you staged, stop: empty or truncated files fail closed. Do
not compose, and do not substitute an envelope or report from another directory, and do not search
other runs for a substitute `ACCOUNT_REVIEW.md`.

## Downstream idea context

Only when a downstream idea task asks for account-performance context, and only after the native
report passed both validators, project its compact context deterministically (no model call):

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/project_idea_context.py' report/account_review.json --output report/account_idea_context.json
```

Pass its compact `account` object onward while the report's identity and lineage stay separate. Do
not pass the complete report into the idea model by default, and do not parse `ACCOUNT_REVIEW.md`.
Reused artifacts do not establish a new fetch.
