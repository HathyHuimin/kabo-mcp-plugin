# IdeaSet contract rules

Read this whole before writing `idea_set.json`. These rules apply on top of
`idea-set.schema.json`; the validator enforces many of them, not all.

## Choosing task_intent

Choose `capacity_plan` only when a positive total-time budget and nonempty `available_assets` are
explicitly supplied or evidenced. A time budget alone does not establish available equipment or
props. For a brief-only topic shortlist with unknown assets, use `task_intent="brief_only"`; with
upstream evidence use `open_shortlist`. Preserve all stated time/cost limits under
`production_constraints` (status `partial` when some constraints are supplied), keep
`available_assets=[]`, and treat feasibility as conditional. When no production constraints are
supplied, use `status="absent"`, null scalar fields and empty assets; a feasible topic or template
request does not require inventing a time limit or asking the creator to supply one for the schema.
If the user specifically requires a confirmed capacity plan, ask for the missing
assets rather than silently downgrading that deliverable.

## Rules

- For a new-account first batch, use `overview.portfolio_logic` to explain the proposed audience
  and positioning, the order in which the ideas establish that promise, and what audience feedback
  would determine the next batch. Mark an unstated audience segment as a proposal. Give a usable
  first-batch content plan without requiring account history, production assets or
  posting-frequency decisions before choosing topics. Do not replace this plan with an
  unprioritized list or invent an algorithmic cadence. The concise renderer carries this field.
- `open_shortlist`: generate the requested number of distinct ideas.
- For reusable sentence patterns, keep the fill-in slots in `working_title` and include each
  requested fully filled example in that candidate's `making_direction`, clearly labelled as an
  illustration. This field reaches the Creator-facing report; an example only in audit fields or
  an instruction telling the user to supply their own example does not fulfill the request.
- `capacity_plan`: requires declared available assets and a positive total-time budget. Record an
  estimated-minute range and assets used for every idea; the sum of maximum minutes must fit the
  declared total budget. Preserve explicit time limits for other intents too: provide conditional
  estimates fitting the budget, and disclose any unconfirmed production assumptions.
- `rework_content`: a VideoAnalysis may be used with or without Trend/Competitor context. Generate
  one target idea with the requested number of Hook options, all tied to the same analyzed content.
- `brief_only`: require a literal `generation_basis.direct_topic` or a bound prior IdeaSet/chat
  reference as the continuation rules define. Allow empty evidence arrays, mark Creator fit as
  hypothesis or unassessed, and make no external factual claims.
- A request for account performance, trends, competitors, Creator context, or a specific video
  cannot silently become a brief-only request. If the required evidence is missing and fallback was
  not explicitly authorized, return the missing-source block without ideas.
- Set `evidence_basis` on every idea: `source_backed` only when upstream/context refs and evidence
  refs are present; use `brief_based` or `hypothesis` when the idea is derived from a brief or an
  unverified inference. Never invent metrics to satisfy a schema.
- For `open_shortlist`, `capacity_plan`, and `brief_only`, `hook_option_count=1` requires
  `hook_preview.progressive_line=null`; count `2` requires a nonempty progressive line adding
  information rather than repeating the opening. Keep `hook_options=[]` for these intents. For
  `rework_content`, preserve the requested options and make `hook_preview` mirror the first option.
- `primary_evidence_ref`, when present, must belong to that idea's `evidence_refs`.
  `assets_used` must be a subset of the request's declared available assets. A shortlist may name
  additional future `required_assets` only with `requires_user_confirmation`, explicit
  assumptions and a `production_missing` gate. The report must show these pending requirements.
  This does not allow assessed feasibility, unavailable resources in a capacity plan, or excluded assets.
- Keep `platforms` within `platform_targets`, reject excluded formats/assets, and use `why_now` as
  an evidence-backed claim only when a dated source supports it; otherwise say that timeliness is
  unverified.
- Apply hard filters first (exclusions, missing required source, impossible production budget),
  then rank Creator fit, evidence strength, differentiation, timeliness, and production feasibility
  with stable rank/title tie-breakers. `selection_status` remains `candidate` until the user
  selects an idea.
- Large supported counts must be prepared as supported batches in the same core generation budget;
  do not split a batch request into hidden sub-tasks to bypass the two-call target.
