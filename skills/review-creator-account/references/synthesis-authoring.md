# Writing the one synthesis

Produce one `account-review-synthesis.v3` object from the compact synthesis input `fetch`
printed, the user's original question and the synthesis schema printed with it. Do not open the
schema file separately, and do not load the complete `AccountAnalysis`, implementation source, or
the report or profile schemas once the compact input exists. `scope.user_question`,
`scope.display_name`, `scope.biography` and `scope.followers` are facts for this pass when present.

## Language

- Use the language the user explicitly requests for delivery; otherwise the explicit
  `delivery_language` of the task, or the language of the current user request if that is absent.
  Set `output_language` to its BCP-47 tag (for example `en`, `zh-CN`, `pt-BR`) and write every
  Creator-facing semantic string in it. Do not infer the language from source captions.
- Keep proper nouns, product names and quoted native source titles unchanged, but write all
  analytical labels and explanatory prose in `output_language`; when it is not English, do not
  insert English analysis jargon such as `stakes`, `workflow`, `hook` or `CTA`.

## Answer the question that was asked

- Write `headline` in this same synthesis as a short, natural editorial title stating the
  account's most consequential tension or opportunity. Never a generic label such as "Account
  Review", and no second model call for the title.
- If the user asked a concrete decision (keep posting or change, switch topics, where the funnel
  breaks), open `diagnosis` by answering it. If they also stated a premise, say whether the public
  sample supports it, conflicts with it, or cannot verify it — the only three states. Do not
  correct a premise first and then answer. A stated wish to change topics is an input: respect it,
  name keepable assets and give a conditional transition. Do not invent "nobody will unfollow". Do
  not treat the largest outlier as a hard rule that they must switch. Quote the public bio; do not
  rewrite it as a new positioning.
- The ruler follows the question. If they asked about conversion or whether the audience is right,
  a high view multiple is not "what works". If they asked whether to post as-is or change first and
  every post is low-view, you may place relative interaction next to views; do not change the global
  winner formula. If they named a handle and asked why followers dropped or where the funnel
  breaks, rank from the public recent window first; never deliver a generic checklist.
- An own-account search or category wording audit compares the intended niche with the real
  biography, caption phrases and hashtags of this run. Obtain missing history with the retrieval
  rules; trends cannot replace it. Separate **observed account wording** from **heuristic
  descriptor tests**: test one wording variable without claiming search volume, ranking benefit or
  internal platform classification. Final copy for a post requires its topic or draft.
- If the user only asked to review the account, use the default shape: what the public sample
  shows is working, what is dragging, and one next-cycle plan. Still use biography and follower
  count from `scope` when present; do not invent a second profile paragraph — the Composer already
  attaches the public bio and follower line.

## Evidence boundary

- Write the cited patterns first, and the headline and diagnosis last. Build each summary claim
  about post patterns only from those checked patterns: preserve the same content examples,
  maturity limits and claim type. Do not add another post category while shortening the answer,
  or merge a wording observation about young posts into a performance comparison about mature
  posts. If the cited patterns do not support a comparison, omit it from the summary as well.
  Separately supplied owner figures may support measured sales or funnel outcomes; identify their
  source and scope, and apply [sales-evidence.md](sales-evidence.md) to those claims.
- For sales or conversion questions, apply [sales-evidence.md](sales-evidence.md). Public content
  can reveal wording and observed reach, not which content sells. Put the unresolved sales premise
  in the headline/diagnosis themselves when it is central to the question; do not title the report
  with a sales conclusion and retract it below.
- `scope.baseline_maturity` gives the actual anchor and per-format cutoff. Publication day counts
  as day one: a 14-day threshold is 13 elapsed days at that anchor, not fourteen full 24-hour days.
  Each post and the highest-multiple pointer carry their own maturity status. A young post's low
  cumulative count cannot establish weak content; a young high outlier does not prove effectiveness
  either. The cutoff gives minimum-age eligibility, not identical exposure ages or causal evidence.
- Declare each pattern's `claim_type`: `public_observation` for bounded wording/count descriptions,
  `performance_association` for mature same-format baseline comparisons, or `hypothesis` for a
  clearly labelled proposed explanation to test. Young posts may support wording observations and
  tests, but not established performance patterns. The composer checks the declared comparisons;
  it does not understand free-text claims, so check the title and explanations before delivery.
  Check every cited post's maturity before choosing a performance association. If one is too young,
  remove the performance conclusion or choose eligible evidence; changing only `claim_type` while
  keeping the same unsupported conclusion is not a correction.
  Do not compare an ineligible post's cumulative views with the mature median even inside a
  hypothesis: a hypothesis label does not make exposure ages comparable. Describe its wording,
  current count and insufficient age together, without using the count as support for weakness.
  Summaries must inherit these limits; do not compress mixed evidence into a claim that a whole
  content style underperforms. Each example mentioned in an explanation must appear in its cited IDs.
  Select eligible same-format examples before drafting performance sections. Keep any young or
  age-unknown example in a separate current-observation statement: publication date, current count
  and the missing comparable exposure window. Do not place it beside an older post's count or the
  mature median as evidence for a stronger/weaker style, even in an optional recommendation.
  Review the complete answer, including its short summary, for that comparison before delivery.

- Apply the same boundary to `headline`, `verdict`, `diagnosis` and every pattern claim. Without
  historical observations, a single snapshot of cumulative views and dated uploads cannot verify
  recent follower or view growth or explain its cause: say the growth premise cannot be verified,
  then describe the observed sample distribution and concentration. Changes between upload cohorts
  are sample comparisons, not a measured growth trajectory. Label any proposed craft mechanism as a
  hypothesis to test in the next shoot; a later limitation cannot repair an unsupported causal
  title or opening.
- History not in this run's envelopes is unseen. Do not write, for example, a 2022–23 follower
  cliff as fact unless this run fetched that window; if it cannot be fetched, say so and stay
  inside the public near window.
- A gold-standard post that is not in `synthesis_input.content` was not seen. Do not fill it from
  memory or a third-party write-up. Select semantic patterns only from content present in
  `synthesis_input.content`.

## Identifiers and numbers

- Copy `analysis_id` and `snapshot_id` exactly from the top level of the compact input; when writing
  JSON with Python, assign them from the loaded input object. Never generate or retype a hash.
- Put referenced `content_id` values in `what_works` and `what_hurts`, copied character for
  character from this run's `synthesis_input.content` after checking the associated title. Never
  reconstruct an ID from a title, URL, memory or a previous run; omit an uncertain reference rather
  than guess. Do not copy or recalculate metrics.
- A `what_hurts` claim about weak performance must use below-baseline or bottom-ranked evidence.
  Above-baseline content may support a positioning-dilution observation only when the text labels it
  as strategic inconsistency rather than performance drag.
- Describe baseline eligibility with `scope.baseline_maturity`: its anchor, per-format cutoff and
  inclusive convention. `maturity_days: 14` means day 14 counting the publication day as day one,
  so the cutoff is 13 elapsed days before the anchor, not 14 full days. Use the supplied cutoff or
  say "comparable mature posts" in the delivery language; never change the sample or recompute a
  stricter baseline to fit the wording.
- When `scope.highest_view_multiple` is present, name that in-window post and say whether to adopt
  it — topic, presentation, distribution or one-off event; adopt or not, and why. Do not treat the
  highest multiple as a post that must be copied. Tests return to the most valuable current
  hypothesis, not automatically to that post. Do not hard-code a format such as interviews.
  Check its maturity and the format baseline's `metrics.views.available_n` first. If its age is
  too recent or unknown, or fewer than three measured views support that baseline, name it only
  as a current observation, omit the multiple and withhold performance-based adoption; propose a
  wording test only on its own merits. The highest-multiple pointer does not override comparison
  eligibility.
- Growth figures, when present, are only those `drive.py growth` printed for this run.

## Recommendations

- Write `recommendations` for the question just answered. By default, when the user gave no
  count: one `continue` that is the primary next move, at most two `test` items, and `stop` only
  when there is a real stop. If the user asked for N tests, write N separate `recommendations.test`
  objects; never merge them.
- For an opening test that fits the question, apply *Opening guidance* below inside this synthesis
  and the existing `recommendations` fields, keeping this review's content citations and sample
  rules. In that same `recommendations.test` action, name the materials to prepare and their
  availability conditions. When no authorised materials were supplied, include a feasible
  self-created demonstration fallback, clearly labelled as a demonstration. A proposed first spoken
  line must describe the visible object or the creator's verified workflow; do not invent a result
  or response-time number for the example line. This completes the opening recommendation in its
  existing place, without a new report section or a second synthesis.

## Opening guidance

When an opening mechanism or recommendation fits the task, keep it in the existing analysis
fields. Choose a feasible mechanism from declared material: proof needs authorised readable proof,
comparison needs both sides, and demonstration needs the actual action or workflow. Make the
recommended opening one complete production choice in the existing recommendation or
differentiation prose: describe the first picture and the first spoken or heard sound (or an
intentional silence) together, then the immediate meaning or question their combination conveys.
Intentional quiet, no title and first-person narration can be valid; apply checkpoints to the
content form.

With titles or captions only, keep findings about their wording, including overview and pattern
labels. Label inferred mechanisms as title or caption interpretations — "the titles suggest a
demonstration direction" — rather than narrating what the creator shows or does. Reserve action
order, visible proof, first frames, text timing, sound and delivery for observed media; this review
observes none. State proposed execution as a recommendation for the creator's next video. Public
performance numbers do not establish why an opening worked; a transcript is not a listening
observation. Keep current citations and metrics unchanged.

The compact input carries `hook_context`: a catalog of opening-mode IDs and names. The names alone
are not a mode's details. For a relevant short-video direction, read
`references/hook-core/research-review.md`, choose a suitable mode, and read its picture, sound,
joint expression and material requirements only with this command, in this same synthesis:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/prepare_hook_context.py' --platform <platform> --purpose account_review --pattern-id <Mxx> --output hook-opening-<Mxx>.json
```

Never open the files under `references/hook-core/` other than `research-review.md`. Then add one
concrete opening execution in the existing recommendation, angle, differentiation or next-shoot
field. Unconfirmed materials are preparation requirements, not assets the creator already owns.
A mode is method context: it supplies no viewing or listening evidence and is never a citation for
a current claim. Mode IDs, internal labels and helper commands stay in run artifacts, never in Creator-facing prose;
write the production choice in the delivery language.

## The slim profile

- Produce the profile in the same synthesis, using only `markets`, `audience`, `positioning`,
  `niche`, `content_profile`, `key_missing_info` and `extensions`; omit fields without sufficient
  input support. Time-window observations such as cadence or current rankings belong in the
  review, not the stable CreatorProfile. The Composer rejects unknown profile fields instead of
  discarding them. Do not invoke a second model for the CreatorProfile.
- The printed schema includes the complete nested profile contract, and every nested object is
  closed: `audience` accepts only `summary` and `needs`, not `description`, `basis`, confidence or
  evidence wrappers. Put source limitations in the review's `data_gaps` and explain uncertainty in
  its prose. A minimal supported audience is
  `{"audience":{"summary":"<bounded audience hypothesis>","needs":["<supported need>"]}}`; omit
  unsupported fields, or use `"profile":{}` when no stable profile claim is supported. Follow the
  printed closed fields for `content_profile`, `extensions` and `extensions.content_style` too, and
  add no provenance metadata anywhere in profile values.
- Optional `profile.niche`: one English sentence of at most 120 characters, from
  `scope.display_name`, `scope.biography` and `scope.followers` only — for example
  `Practical fitness for busy people`. Omit it if the profile cannot support a specific
  audience-and-topic sentence. No posts, no enum or id; English whatever `output_language` is.
