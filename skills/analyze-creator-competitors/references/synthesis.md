# Synthesis, comments and the report

Read this whole file before writing `competitor-research-synthesis.json` (and before running
`content --comments`, when comments are needed). `content` printed everything the synthesis reads,
the synthesis schema first; write the synthesis from that output.

## Evidence the synthesis stands on

**Baselines.** Use same-platform, same-account, same-format baselines. The Analyzer may retain a
directional multiple from at least three observations for audit, but Creator-facing outlier proof
requires at least five comparables and a multiple of at least 1.5×. Do not impose a hard
baseline-view floor or a fixed lane quota: eligible evidence is ranked continuously with
`log1p(public views) + log(outlier multiple) * sqrt(n / (n + 5))`, which rewards absolute heat,
relative lift and sample support without letting tiny denominators win on ratio alone. When the
sample or multiple condition fails, show the strongest absolute public metric instead.

- Keep the score internal, and keep sample sizes, the research window, coverage caveats and audit
  boundaries out of synthesis prose; the Composer records scope and limits in the Report, and the
  Renderer closes the reply with one fixed basis sentence (analyzed posts, accounts, window dates,
  and the baseline size only behind a shown multiple). Answer follow-up questions about
  measurement from the structured Report.
- For YouTube, the target item is excluded from its own baseline and only mature (at least
  14-day-old) comparables count. Keep absolute popularity separate from account breakout; videos at
  or below 180 seconds are only provisional Short candidates; no VPH without historical
  snapshots. Preserve `format_confidence` and `format_source` per cited YouTube item; a
  duration-inferred cohort must be described naturally as inferred, not as a platform label.

**What `content` printed.** `topic-coverage.json` as one title line per account: that is what a
missing-topic claim is checked against. `synthesis-evidence.json`: every included account with its
relationship, its analyzed items (title, views, a Creator-facing outlier multiple only where at
least five comparables support one, and the Analyzer's `evidence_score`, highest first), included
accounts without analyzed items, the seed item, and `citation_allowlist`. Do not query
`public-content-analysis.json`, the Snapshot or the coverage file again with `jq`, `python` or
`cat`.

**Posting cadence.** Each account in `synthesis-evidence.json` carries `cadence`: posts in the
request window, posts per week and the median gap in days — or, when the fetched history ends
inside the window, only `posts_in_window_at_least` with `count_is_lower_bound` true and no rate.
`subject_cadence` is the subject's own. The reply prints every compared account's cadence beside
the subject's and, from these numbers alone, whether each posts more often, less often or about as
often; a lower bound ranks only where it already exceeds the other side's exact count. Do not rank
cadence in the synthesis: no account is faster, slower, more or less frequent than another there.
When the user asks about posting cadence or frequency, quote the numbers in the overview or a
pattern mechanism — a lower bound only as "at least N posts", never as a weekly rate — and say what
the cadence means for the content; never describe it only as a rhythm or habit.

**Citation allowlist.** It is the exact intersection of eligible direct, adjacent or media account
references from `competitor-eligibility.v2`, items present in the final Snapshot, and `content_id`
values present in `analysis.evidence`. History rows absent from `analysis.evidence`, including
outside-window rows, are not allowlisted; collaborator/UGC content owned by any unselected account
is excluded even when it appeared in an eligible account's feed. `subject.seed_content_ref` is the
one exception: if it is on the Snapshot and in `analysis.evidence`, it may be cited as the starting
sample even when the subject is not an included competitor. Every other `featured_content_refs`,
pattern reference and gap reference must come from the allowlist. Preflight every planned account
and content reference against it before the single synthesis write.

## Writing the synthesis

Write **`competitor-research-synthesis.json` in the run directory** using the printed schema;
`competitor-research-synthesis.v3.1` is its `schema_version`, not a file name. It is a semantic
skeleton, not the final Report. Write it exactly once; after it exists, treat it as immutable and
do not rerun Compose, Validate or Render merely to shorten or inspect the output.

**Accounts and references.**

- Use canonical `creator_id` values as every `account_ref` and exact `content_id` values from the
  analyzed evidence; the Composer accepts a unique public handle as a defensive fallback. When an
  included account has no Snapshot creator (no in-window items came back), use its public handle as
  `account_ref`; do not invent a creator id.
- The reportable accounts are `competitor-account-selection.json.included_handles` intersected with
  the citation rows. Put **every** included account into `competitors`, including one with no
  analyzed in-window item; each row carries only `account_ref` and `relationship`.
- The union of `patterns[].account_refs` must include every included account that has at least one
  analyzed in-window item: still at most three mechanisms, but they compare across the
  evidence-bearing roster instead of featuring one hero. An eligible included account with zero
  such items stays on the roster (the Composer records it as a zero-sample account) and cannot
  enter `patterns[].account_refs` or content-performance findings.
- Keep the standard the earlier steps used: direct relationships before adjacent ones and at most
  one media reference, judged on the stated account-selection constraints and sustained topical
  focus, against whatever `subject.kind` made the word *competitor* mean for this run. One strong
  post still does not make an account a competitor, and a thin-but-direct account still outranks a
  prolific adjacent one.
- A handle withheld from `included_handles` (including an account the evidence Gate excluded or the
  media cap left out) must not re-enter `competitors` or the patterns.
- List the most important evidence in `featured_content_refs`. Every reference there, and in
  `patterns[].content_refs` and `gaps[].content_refs`, must belong to an account you actually wrote
  into `competitors`; passing the citation allowlist is not enough, because the Composer builds its
  notion of a competitor from this file. The Composer backfills every `included_handles` row onto
  the roster (with a handle stub when the Snapshot has no creator for it), and the Renderer prints
  that roster before the overview summary. Never write that an account was dropped, excluded or
  reconsidered.
- Use `analysis.account_outliers[].evidence_score` only to rank evidence that actually supports the
  mechanism. Semantic relevance remains mandatory: never cite an off-topic history item merely
  because its score is high.
- Do not copy metrics, baselines, URLs, sample sizes or proof-point calculations.

**Evidence layers.** Do not describe an adjacent or media account's creative evidence as proof of
what direct competitors do; when direct evidence is sparse, say so and label the evidence layer
explicitly.

**Openings and mechanisms.** Analyze opening mechanisms and differentiation from the retrieved
competitor evidence in the existing pattern/differentiation fields. With titles or captions only:

- keep findings, including overview and pattern labels, about their wording, and label inferred
  mechanisms as title/caption interpretations — say "the titles suggest a demonstration
  direction", not what the creators show or do;
- reserve action order, visible proof, first frames, text timing, sound and delivery for observed
  media, which this skill does not retrieve;
- state any proposed execution as a recommendation for the creator's next video. Public
  performance numbers do not establish why an opening worked.

**Editorial voice.**

- Write `overview.title` as a short, compelling editorial headline and `overview.summary` as one
  concise supporting paragraph. Write each `patterns[].pattern` as a natural Creator-facing section
  headline, not a taxonomy label: name the competitor move plus its stakes or the opening it
  creates (for example, turn "fast-response messaging" into "Make a slow reply feel like a sale
  already being lost"). Vary the phrasing; do not force every finding into the same template or
  numbering.
- Write plain `@handle` mentions for Instagram, but use the channel display name without an `@` on
  YouTube unless the data returned a verified public handle; do not invent a YouTube handle from
  the channel title; do not write Markdown links.
- Use one output language consistently across all Creator-facing editorial fields, matching the
  user's request language. For `zh-CN`, write the overview, pattern headings and interpretation,
  differentiation and gaps in Chinese; brand, product and account names, URLs, exact source titles
  and common abbreviations may remain unchanged, but localize generic role or category nouns in
  your prose. Immediately before the single synthesis write, self-check those fields for one
  consistent language. Apply this semantically rather than with a broad English-word blacklist.

**Fields to write.** Only what the Creator reply uses: `overview.title` and `overview.summary`;
`competitors[].account_ref` and `relationship`; `patterns[].pattern`, `mechanism`, `account_refs`,
`content_refs` and `differentiation`; `featured_content_refs`; and `gaps[]` — plus `report_id` and
`status` (`report` fills `schema_version` and `request_id`). Omit `overview.key_findings`,
`overview.next_action`, `competitors[].summary`, top-level `data_gaps` and the pattern idea fields
(`audience_problem`, `content_promise`, `angle`, `recommended_formats`, `why_now`, `creator_fit`,
`must_avoid`): nothing Creator-facing shows them, and the Composer records scope, recall and
zero-sample notes itself. The one exception is a run where idea generation follows (see *Idea
handoff*). Keep it compact: one concise overview paragraph; for each mechanism, one or two
sentences of interpretation and one or two sentences of Creator differentiation; never say what
"the report", "Creator-facing output" or "the Renderer" should display.

## Audience comments

Comments are a pre-synthesis step. When the user did not ask what selected accounts have not done
or what the audience wants, skip them entirely. When they did, run `drive.py content --comments`
in place of `content`: it runs the whole comment block (targets → `instagram_list_comments` or
`youtube_list_comments` → persist) before printing the evidence, hands over the histories and then
the comments, and prints `audience-comments.json`. If you decide only after `content` has finished,
run `drive.py comments` as its own command. Complete any comment hand-over before writing the
synthesis, never after the report exists.

- The sample is bounded and drawn from included-account posts already in `analysis.evidence`; each
  post keeps its caption. Do not scrape, call a vendor API, use posts outside `analysis.evidence`,
  or hand-write comment text. Never create `comment-source.json` or `audience-comments.json`
  yourself. Posts whose fetch was refused or never made are recorded as `not_fetched`.
- Public comment pages may finish as `completed_partial`: the stage persists returned rows, original
  status and limitations. A page is not a complete audience sample, and reply counts are not
  fetched replies. Failed, unsupported or setup-blocked paths carry no comment evidence; keep the
  diagnostics and continue the supported analysis. Read each post's limitations when interpreting
  `audience-comments.json`.
- Read each `posts[].caption` with its rows. Use a leftover comment as audience-want only when it
  names a need or scene you can check against retrieved titles, and that post's caption does not
  already explain the comment as a reply the post asked for. Discard comments that fail that test.
  Do not keep a keyword list or a list of invalid comment kinds.
- Write `gaps[]` from the comments that pass: each audience-want gap names one scene those comments
  already say, plus a retrieved title that does not cover it. If none pass — or `texts[]` is empty
  because there were no targets, the operation is unsupported or the fetch failed — do not claim
  audience-want, and still deliver "they have not done X" from titles. Do not invent a format or
  day-N series.
- Comment counts, high views, search suggestions, magazine copy, Shop reviews and transcripts are
  not audience-want evidence. A Reddit or community substitute must be labeled cross-platform
  audience discussion, never original-post comments or the Creator's own audience.

## Compose, validate, render

`report` composes, validates and renders the native Report deterministically; it is the run's
final step and its deliverable is `creator-reply.md` in the run directory. Both rendered Markdown
files are in the run root: `report/creator-report.md` is not created, so do not move, copy or
re-render a file to satisfy that path.

- The Composer resolves every referenced account and content item, inserts URLs and public
  metrics, calculates report coverage, carries Analyzer outliers verbatim, creates concise proof
  points and adds every pattern/gap reference to `top_content`, so no model repair pass is allowed.
- If Compose or Validate fails, stop the run with the validation error; do not inspect
  implementation source or mutate the Request or synthesis after the fact, and do not repair the
  immutable synthesis.
- The validator checks only the composed `competitor-research-report.v3`; never run it against
  `public-content-analysis.v1` or the synthesis skeleton, and do not run validators by hand:
  `report` runs them.
- The Renderer is the only thing that ever creates `creator-report.md`; the run is not complete
  until it has written that file. It links account handles and content references, then prefers
  proof from different accounts and stronger account-relative outliers over ordinary absolute
  samples. Do not add another model pass to rewrite or repair the Markdown.

## The two Markdown files

**`creator-report.md` is written by the Renderer and by nothing else.** It is a deterministic
projection of the validated Report: the selected-account roster first, then the first mention of
every public account or content item linked when its URL exists, each proof point's public metric
with its account-relative median and multiple, and fixed section labels and proof-point sentences
in English while the synthesis prose stays in the Creator's language. Those links and numbers are
the product — a `creator-report.md` that describes performance with vague prose (such as "well
above the account's usual level") instead of naming the handle, the view count and the multiple did
not come from the Renderer. Never edit, rewrite, summarize or "tidy" it, and never place a file of
your own at that path. If the prose reads poorly, fix the synthesis and run `report` again; this is
the one case where the synthesis may be rewritten.

**`creator-reply.md` is your answer.** `report` writes it in the Creator's language, leading with
the Report's `overview.summary`, then the compared accounts, at most three findings each with its
decisive proof (handle, views and, where supported, the account-relative multiple) and how this
Creator can differ, then what nobody is doing yet. It prints the file's path and content. Deliver
that entire file once as your answer; it is the deliverable, and `creator-report.md` stays the
separate research artifact.

- If every part is already in the user's language, relay it unchanged. Otherwise translate the
  complete reply, including the fixed English zero-sample notice, preserving structure, counts,
  figures, account labels, exact URLs, source-title attribution and every stated gap; a quoted
  source title stays in its original wording.
- Do not shorten it into a summary, add closing advice, or append a second paraphrased report.
  Never append a coverage note, `data_gaps`, audit boundary or section narrating what this run could
  or could not confirm; keep the Renderer's own missing-account notice and closing basis sentence.
- When the Creator then asks how it was measured, answer from the structured Report.

## Idea handoff

When the creator wants content ideas next, the synthesis must also carry the pattern idea fields
listed above. After `report` succeeds, run:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/project_idea_context.py' competitor-research-report.json
```

It selects the semantic fields present in `patterns[]` and supported gaps from the validated
Report; it invokes no model and passes no overview, IDs, URLs, evidence graphs, confidence,
freshness or status. Idea generation must never parse Creator-facing Markdown. Suggest
`develop-content-ideas` by name as the next step; do not run it from this skill.
