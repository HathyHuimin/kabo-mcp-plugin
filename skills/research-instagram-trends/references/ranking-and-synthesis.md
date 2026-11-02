# Ranking, Snapshot and Workflow judgment 3: the synthesis

Read this file whole before writing the scores that start `rank`; the synthesis rules below also
govern the Provided Snapshot path.

## Ranked enrichment (`rank`)

With one lane `rank` builds the ranking input with
`build_instagram_ranking_input.py --request trend-research-request.json --candidates trend-candidate-set.json --gate trend-relevance-gate.json --output trend-ranking-input.json`;
with two lanes it passes the reach lane as well (`--reach-candidates trend-candidate-set-reach.json
--reach-gate trend-relevance-gate-reach.json`; both flags are required together).

The builder is the only shortlist selector. It applies topic floor 55, one deterministic order and
the 60-item cap, and emits whichever enrichment form the collection supports: the handle form with
`candidate_set_id` and `selected` when `collect_instagram_trend_candidates` returned an id, otherwise
the inline `items` form.

- **Handle form.** `rank` hands over one `call:` line: `enrich_instagram_shortlist` with the
  ranking input verbatim as the tool arguments — every top-level field, `window` included. Make that
  call exactly once with the printed arguments verbatim, fetch the complete result (stage it when
  `fetch` reports no ready download), and run
  `rank --placed`, which persists it as `trend-enrichment-01.json` with the same stop rule as the
  candidate set.
- **Inline form.** The full inline ranking is larger than a reply can carry, so `rank` prints the
  lean inline call as compact JSON (also saved as `trend-enrichment-call.json`) and stops: six items
  of each lane (the first two by meaning, then the hottest of the lane's top semantic tier), in the
  builder's order, each trimmed to the fields the backend needs, every top-level field kept. Call
  `enrich_instagram_shortlist` exactly once with that JSON verbatim as the tool arguments, fetch (or
  stage) the result, then run `rank --placed`, which persists it and goes on; do not open the file again.
- An enrichment call that fails before returning a result (an MCP error without one, which `stage`
  refuses and prints) follows the failed-call rule in `SKILL.md`. If no result can be staged after
  that, `rank --placed` stops and says the enrichment result was not recorded: this stage cannot
  complete in this run, so report that the evidence enrichment could not be completed, with the
  tool's own message, and stop. Never write or reconstruct the result yourself.
- Never rebuild the arguments or `selected`, re-derive them from the Gate, add or drop entries, or
  reorder them, even when a tool description recommends the handle form. Do not run a legacy
  selector, allocate route slots, hand-pick or reorder.
- Copy every `content_key` with its `instagram:` prefix exactly as printed. If the enrichment
  answers that selected keys are absent from the candidate set, the arguments were mis-copied, not
  the data missing: send the printed arguments once more, verbatim, then run `rank --placed`; do
  not close the run.

Two lanes always use the inline form — the handle form binds one `candidate_set_id` and there are
two — with `ranking_mode` set to `relevance_saturated.v1` and each item tagged with its `lane`. Items
found by both lanes count as relevance: having already proved themselves in-window and on-topic,
they need not spend a reach slot. The report window becomes the union of the two discovery windows,
so the Request window must be long enough to contain the reach lane; the builder fails loudly when
it is not, rather than emit a lane the backend would filter away in full.

### What the backend computes

The backend applies topic floor 55, fills missing date/play fields with bounded requests, filters
the window, and computes:

- semantic = 50% topic + 25% transferability + 25% locale;
- pre-score = 35% semantic + 45% heat + 20% velocity;
- final = 45% semantic + 45% primary evidence + 10% secondary evidence.

Heat is a tie-aware percentile of public Instagram plays. Velocity is public plays divided by content
age, converted to a tie-aware percentile in this run. They and all weighted scores are deterministic
backend calculations, not model output. Missing stays missing, never zero. Heat and velocity are
frozen on the full exact, in-window eligible pool; shortlist truncation never changes their scale.

The backend first removes candidates whose optimistic final-score bound cannot reach the current
Top 12. Inside the remaining pool it retains every candidate whose evidence-free lower score already
clears the Top-12 cutoff, then fills any remaining part of a 16-item final-source budget with the
strongest optimistic challengers. If more than 16 lower-bound-qualified candidates exist, it keeps
the 16 highest lower scores, breaking ties by the original deterministic union order; otherwise it
keeps all of them and ranks challengers by optimistic score, then pre-score, then `content_key`,
taking only enough to reach 16. This is not a fixed `6+6` or a Search/Popular lane quota: the extra
positions let outlier evidence change membership while keeping one call bounded. The backend reranks
after evidence and uses only unused budget for verified reserves when final-source detail fails.

Under `relevance_saturated.v1` the final twelve are filled differently. Relevance is still the
primary key, but only until the relevance lane's top tier runs out; the slots left over go to the
reach lane by absolute reach, instead of descending through the lower relevance tiers. The relevance
lane keeps every slot it can genuinely fill and the rest buy the largest posts the format has
produced. The split is not a ratio to be tuned: how many items sit in the top tier is decided by the
pool on the day, so any fixed 30/70 would drift out of calibration. When the reach lane also runs
short, the remainder falls back to the relevance lane's lower tiers in the usual tier order.

### Snapshot and Analysis

`rank` then builds the Snapshot and analyzes exactly once — `build_trend_workflow_snapshot.py` over
the candidate set, the Gate and `trend-enrichment-01.json` (merged into `trend-enrichment.json`),
then `analyze_public_snapshot.py --mode trend` — and prints `public-content-analysis.json`, the
opening-pattern catalog (`hook-context.json`) and the synthesis schema. With two lanes both steps
read the lanes as one pool (`trend-candidate-set-union.json`, `trend-relevance-gate-union.json`), so
reach-lane items are evidence like any other.

If no item reaches the floor, `rank` uses the builder's `--empty-enrichment-output` path; it never
calls enrichment with an empty array. A saved response with an explicit empty `items` array still
proceeds through Snapshot, Analyze, Compose, Validate and Render. Preserve its original status and
limitations in the audit artifacts: no eligible results, incomplete evidence and actual execution
failure are distinct. A partial or empty result is not a reason to omit the report, and optional
enrichment failure must not discard evidence already verified by the workflow. Never widen an
explicit time window or treat undated or out-of-window candidates as verified matches.

## The synthesis

Write one `trend-research-synthesis.v3.3` into `trend-research-synthesis.json` from the printed
Analysis and schema (in the Workflow leave `schema_version`, `request_id` and `report_id` out:
`report` fills them and keeps any value you write). This is the last model judgment.

- Use only allowlisted ranked evidence. **Cite the hottest allowlisted items that support a
  mechanism** (highest public plays, then highest account-relative multiple). Do not feature a
  low-play podcast, news clip or growth tip when a higher-play product demo or workflow Reel is
  already in the allowlist.
- Write natural headlines, `why_now`, up to three mechanisms and Creator-fit directions. Begin
  `overview.summary` directly with the content conclusion; never mention research dates or window,
  capture time, snapshot status, candidate/analyzed counts, coverage or limitations there. Never
  copy formulas, routes, quotas, shortlist mechanics, experiments, window boilerplate or audit
  inventory. One strong reference is valid; do not pad.
- When no ranked evidence remains, set `status: partial`, leave `featured_content_refs`, `patterns`
  and `emerging_creators` empty, and provide a useful task-specific next action as a proposed test,
  not an observed trend. Briefly qualify any claim that cannot be established; never invent tracks,
  metrics, dates, examples or growth. Continue to the deterministic report steps rather than ending
  with a refusal or a hand-written failure note. When no verified ranked evidence exists, the
  Composer owns the empty-result overview and proposed test using its fixed English template; the
  original synthesis remains an audit artifact, and unsupported model findings are never restored
  over the composed empty-result report.
- Put scope, coverage, sample-size, capture-date, window and missing-baseline notes in the
  synthesis `data_gaps` array, never in model-written prose (see *Evidence rules* below).
- Keep every stage and artifact dependency-driven: every input/output field must have a named
  downstream consumer; do not add completeness-only, usually empty or unused fields.
- Current user-declared production limits take precedence over saved research framing. Treat niche,
  formats and observed creator mechanisms as relevance context, not an inventory of available
  footage or audio. Every Creator-fit direction and suggested test must use the declared available
  material; never assume missing locations, footage, proof or audio because a reference uses them.

### Opening choices

For a Reel mechanism or Creator-fit direction that needs an opening choice, apply this guidance in
the existing mechanism/direction fields. Keep the current ranked evidence and the caption wording
used for retrieval. It applies to video-content synthesis, not to the audio board or scene-sound
paths. Read `references/hook-core/research-review.md` before choosing a mode.

- Choose a feasible mechanism from declared material: proof needs authorised readable proof,
  comparison needs both sides, and demonstration needs the actual action or workflow. Make the
  recommended opening one complete production choice in the existing recommendation,
  differentiation or direction prose: describe the first picture and the first spoken or heard
  sound (or intentional silence) together, then the immediate meaning or question their combination
  conveys. Intentional quiet, no title and first-person narration can be valid; apply checkpoints to
  the content form.
- With titles/captions only, keep reference-related findings, including overview and pattern
  labels, about their wording. Label inferred mechanisms as title/caption interpretations ("the
  titles suggest a demonstration direction") rather than narrating what the creators show or do.
  Reserve action order, visible proof, first frames, text timing, sound and delivery for observed
  media. State proposed execution as a recommendation for the creator's next video. Public
  performance numbers do not establish why an opening worked; a transcript is not a listening
  observation. Keep current-task citations and metrics unchanged.
- Consume the printed `HookResearchContext` (`hook-context.json`) in the existing synthesis. For a
  relevant short-video direction, choose a suitable mode, read its picture, sound and material
  details with
  `cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/prepare_hook_context.py' --platform instagram --purpose trends --pattern-id <Mxx> --output hook-opening-<Mxx>.json`
  in that same synthesis, then add one concrete opening execution in the existing recommendation,
  angle, differentiation or next-shoot field. That script is the only way to read pattern details;
  never open the files under `references/hook-core/` other than `research-review.md`.
- Unconfirmed materials are preparation requirements, not assets the creator already owns. Method
  context supplies no viewing or listening evidence and is never current-task proof: keep it out of
  proof citations. Write the production choice in the delivery language; keep mode IDs, internal
  labels and helper commands in task artifacts, outside Creator-facing prose.

## Evidence rules

- Reliable outlier = multiple at least 1.5 and strength `moderate` or `strong`. Directional n=3–4
  remains in structured audit but does not outrank strong absolute heat.
- The account baseline excludes the target Reel itself, pinned rows, incomplete rows and Reels newer
  than 24 hours. These are deterministic cohort-integrity rules, not synthesis choices.
- Show a reliable multiple when available; otherwise show strongest absolute plays or velocity.
  Never mechanically announce a low multiple or missing baseline. When reliable outlier evidence is
  unavailable, state the strongest absolute public metric directly without explaining the missing
  evidence.
- Do not narrate research scope, coverage, sample size, capture dates, the research window or a
  missing baseline in model-written Creator-facing prose, in any language and under any heading.
  Write those notes into `data_gaps`: it stays in the structured Report and is not rendered
  verbatim. The renderer closes the report with one fixed basis sentence — analyzed public posts,
  the window dates and, only when a multiple is shown, the baseline size behind it — written in the
  `--language` you pass (the language the user writes in, not the Request's `locale.language`;
  unsupported languages fall back to English, which you relay), and rejects internal method
  identifiers in model prose.
- When the Creator asks how the result was measured, answer from the structured Report (window,
  collected and eligible counts, baselines, `data_gaps`) instead of re-running anything.
- For display, the Composer stably sorts only each mechanism's already selected `content_refs` by
  public plays/views descending, then account-relative multiple descending, retaining authored order
  on ties and each ref's original paired proof. It shows at most two, but keeps one when it is
  stronger.
- Baselines stay within the same account and format. Prefer Instagram-only plays; older combined
  public rows may include Facebook plays and must not be compared with YouTube or owner Insights.
- One snapshot supports current performance and recurrence, not momentum. Never infer private
  reach, saves, watch time, follows, demographics, revenue or conversion.
- Keep coverage, low multiples and limitations in the structured Report; the Creator-facing Markdown
  carries only the renderer's one basis sentence, without routine bounded-search banners or internal
  method terms.
