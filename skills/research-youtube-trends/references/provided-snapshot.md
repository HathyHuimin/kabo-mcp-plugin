# Provided Snapshot input

Read this whole file before the first command of this route. `<run directory>` and
`<skill directory>` are the absolute paths from *Running commands* in `SKILL.md`; run every command
from the run directory and write new artifacts only there.

Use this route when the user provides one `public-content-snapshot.v1` for YouTube and asks to
analyze that evidence, instead of the collection workflow. Preserve the supplied Snapshot bytes.

1. Print the Request schema once with `cat '<skill directory>/references/trend-research-request.schema.json'`
   and build one `TrendResearchRequest` in `trend-research-request.json` from the user's requested
   niche and mode, preserving the Snapshot's exact platform, window, locale and formats. If the
   requested scope conflicts with the supplied evidence, ask for compatible evidence or explicit
   permission for a separate fresh collection; never silently change either scope or claim that
   historical evidence is current.
2. Validate the Request, the Snapshot and their scope consistency, then analyze:

   ```bash
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/validate_provided_snapshot.py' --request trend-research-request.json --snapshot '<provided-snapshot-path>'
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/analyze_public_snapshot.py' '<provided-snapshot-path>' --mode trend --request trend-research-request.json --output public-content-analysis.json
   ```

   On validation or analysis failure, stop this route with the actual error. Do not create a query
   plan, call collection or enrichment, reassemble the supplied Snapshot, invent a Gate or pass
   `--relevance-gate` or `--trend-enrichment`.
3. Print the synthesis schema once with `cat '<skill directory>/references/trend-research-synthesis.schema.json'`
   and write one fresh `trend-research-synthesis.v3.3` into `trend-research-synthesis.json` from
   the intersection of the current Analysis's `relevance.candidate_content_ids`, ranked content and
   the supplied Snapshot evidence. Never reuse a synthesis created for a different analysis.
   Lexical overlap is recall, not proof of semantic relevance; exclude unsupported references.
   Follow the synthesis rules of `references/video-trend-workflow.md` (*The synthesis*).
4. Compose, validate and render, passing the same supplied Snapshot path to Compose; this is the
   route's final step:

   ```bash
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/compose_report.py' --request trend-research-request.json --snapshot '<provided-snapshot-path>' --analysis public-content-analysis.json --synthesis trend-research-synthesis.json --output trend-research-report.json && python3 '<skill directory>/scripts/validate_output.py' trend-research-report.json --request trend-research-request.json --analysis public-content-analysis.json && python3 '<skill directory>/scripts/render_creator_report.py' trend-research-report.json creator-report.md --language <the user's chat language> && cat creator-report.md
   ```

A failed validator is not a deliverable; insufficient evidence is partial, not permission to invent
findings or recollect without consent. This single-Snapshot route supports only conclusions
justified by its existing public evidence. It does not establish rising, falling, acceleration or
confirmed momentum; do not combine multiple snapshots or invent private metrics. The idea handoff
in `SKILL.md` applies here too.
