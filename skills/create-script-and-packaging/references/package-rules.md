# Package rules

Read this whole file before writing `script_package.json`. It holds the decision rules, the
interaction gate, the optional thumbnail capability and the package boundaries.

## Decision rules (1–7)

1. **Native package SelectedIdea first**: for a native package, accept only a selected, frozen idea. An unchosen candidate list is blocked. A literal topic/direction is valid only when the user explicitly selects it by asking to script that direction; normalize it only with `build_direct_selected_idea.py`, with no fabricated evidence or constraints. Creative drafts follow `references/topic-drafts.md` without requiring a SelectedIdea.
2. **Evidence basis before claims**: preserve `source_backed`, `brief_based`, or `hypothesis` in the input snapshot. Empty evidence is valid for the latter two, but every factual sentence must remain qualified until proof is supplied.
3. **Mode isolation**: `script_only`, `packaging_only`, and `full_package` are mutually exclusive output shapes. Packaging-only revises a supplied package; it never silently regenerates a script.
4. **Production gates are explicit**: missing proof, assets, permissions, voice, or non-inferable production constraints affecting feasibility produce `draft_needs_user_input` plus one structured question batch. An omitted target duration alone uses `script_duration.py` automatic duration planning; ask only when platform/form is unclear or a non-inferable constraint affects feasibility. They cannot be represented as "easy to shoot" without minutes, assets, and effort assumptions.
5. **Hook-to-payoff traceability**: every Hook option must state its mechanism, payoff, evidence/claim status, and the scene or packaging surface where it is used. Hook options must be materially distinct.
6. **Packaging safety**: title, caption, thumbnail/cover text, CTA, and on-screen copy share the same claim ledger. Unsupported guarantees, fabricated metrics, competitor wording, and publish-time advice are rejected.
7. **Approval is external**: `ready_for_review` is the highest automatic status. `approved_for_production` requires an independent approval event; the Skill does not approve its own output.

## Interaction gate

Do not re-ask facts already confirmed in `SelectedIdea`. Ask once only for missing information that changes the words or whether the content can be produced:

- actual product/customer proof and which claims may be published;
- available UI, footage, locations, people, and likeness permissions;
- Creator voice examples or prohibited language when the profile does not supply them;
- platform-specific packaging inputs that cannot be inferred from the script.

Missing proof does not block a draft. It blocks unsupported sentences and `ready_for_review` / `approved_for_production` status.

For `brief_based` or `hypothesis` SelectedIdeas, empty context/evidence refs are valid. Mark the input snapshot with the evidence basis and keep all unverified wording explicitly hypothetical; `source_backed` ideas must preserve at least one upstream context ref and evidence ref.

When a draft's copy carries a numeric result that is not proven yet, the copy has to say so in the creator's own language, and the claim records which words do it: put that span in the claim's `qualifier`, copied from the copy word for word. A claim with no `qualifier` is read for a short list of Chinese and English hedges instead, which is why the field exists — the hedge is only recognised in a language that list happens to cover.

## Independent thumbnail capability

Existing-image download, resize and preview are an optional host image capability, independent of this skill: image-only requests do not require this skill or `SelectedIdea` (do not fabricate a ScriptPackage to unlock image processing), and a package request without an image operation must not depend on it. Keep the existing validation, evidence and approval gates. Read `references/youtube-thumbnail-tools.md` whole before an explicitly requested image operation. When the host offers none, state that image operations are unavailable here and continue with the text package. Do not reimplement an image engine in this skill and do not use an LLM for image processing. These operations never create new artwork and never upgrade the core `brief_only` visual status or change ScriptPackage approval. On a failure, describe the failed operation and preserve the completed package. Never claim a finished image from a design brief, invent an artifact URL, or repeatedly retry a permanent failure.

## Boundaries

- Do not choose among Idea candidates. Native packages require `SelectedIdea`, while allowing the explicit direct-user-selection normalization path above. Creative drafts use the already selected topic under `topic-drafts.md` without requiring `SelectedIdea`.
- Do not perform trend/competitor research inside this Skill.
- Do not recommend publish time; hand the completed package back to the creator without timing advice.
- Do not synthesize final Thumbnail/cover artwork. The core package produces a designer/image-generator brief only; an optional host image capability may preview or deterministically resize an existing image.
- Do not append Markdown to a strict JSON response. Use `render_script_package.py` as the separate Creator-facing projection.
- Every publishable claim surface (Hook, on-screen text, CTA, headline, caption, cover text and scene) must reference a claim or be explicitly marked non-factual copy. Placeholder/prohibited claims force draft or blocked status.
- Do not reproduce competitor wording, identity, footage, or protected creative expression.
- Do not promise virality or fabricate performance forecasts.
