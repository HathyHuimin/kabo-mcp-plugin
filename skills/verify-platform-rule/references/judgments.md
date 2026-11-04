# Writing the judgments

Read this whole file before writing the judgments for `drive.py report`. The JSON shape is
`references/rule-judgments.schema.json`.

Write statements as separate assertions. Use evidence references `source_id:Pstart-Pend`
(or `source_id:Pnumber`), not copied quotes, URLs or retrieval dates. Choose complete statements
with their conditions; do not cherry-pick a passage that changes the page's meaning. Complete means
keeping the conditions the assertion depends on, not quoting a whole section: select the shortest
range that carries them.
Supply a `gloss` when the page's language differs from the user's: translate every sentence in the
selected range, in order, and nothing else. No summary, no lead-in such as "the page says", and no
conclusion about the claim; if a range is too long to translate whole, narrow the range. Original
text is never replaced with its translation. What the evidence means for the claim belongs in the
claim's `statement` (and `scope` or `inference` where those are allowed), never in a gloss.

- `confirmed`: the selected text states what the assertion says, with its conditions.
- `refuted`: the selected text itself states something incompatible with the assertion, such as a
  different requirement or limit, an explicit permission, or that the rule does not apply. A page
  that lists related requirements, tips or conditions and omits the asserted one does not refute
  it: help and guidance pages are not exhaustive lists.
- `partly_true`: evidence plus `scope` explaining which part holds and its limits.
- `no_official_text` - relevant pages were read but do not answer the claim. List `checked_sources`.
  This is not a claim that the platform has no rule. It is also the verdict when a page covers the
  same topic without the asserted requirement: an assertion that the platform requires something,
  checked against pages that never state it, is `no_official_text`, not `refuted`.
- `not_checked`: name the relevant unread page in `unreached_source`.
Only `no_official_text` and `not_checked` claims may carry `inference`, with no evidence. Explain
uncertainty and practical implications separately from platform facts. For an unsupported
benefit, explain why not to prioritize it solely for the unestablished benefit, without claiming
it can never help. Account-specific strategy beyond the established facts remains outside this
Skill.

Operational claims need quotes supporting the specific path, choice or step, not merely its
effect. Split independently supported controls without expanding into unrelated advice.
Use `unreachable_sources` to explain failed or navigation-only pages in the user's language;
recorded fetch failures cannot be dropped. The driver binds metadata and exact original passages,
then runs `validate_sources.py`, `validate_rule_check.py` including snapshot checks and
`record_passages.quoted`, and `render_rule_check.py`. It retains `rule-check.v1` for downstream
consumers. Validation failure produces no deliverable; fix the named judgments without refetching.
The fixed-topic Light pipeline uses `scripts/fixed_topic_claims.py` to build this same contract:
an explanation is one claim; an optional action is a second claim with its own exact quotation.
Both are ordinary `rule-check.v1` claims, so the Standard validator and renderer consume all
their statements and evidence without inventing a second advice schema. If continuing a supplied
Light check, retain those claims and their evidence as the original check; add new findings
separately. Do not rewrite its operational claim as unsupported strategy or silently replace its
original evidence. If only the explanation is supported, omit the action rather than inventing it.
Exact quotation validation establishes source matching, not semantic entailment: the analyst must
still check the full assertion, necessary conditions and action path against that quotation.
