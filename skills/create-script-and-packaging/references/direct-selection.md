# Direct selection

Read this before workflow step 1 when the Creator explicitly asks for a script around their own
stated topic or direction. Commands use `<run>` and `<skill directory>` exactly as `SKILL.md` defines
them.

For the core Script/Packaging path, load `ScriptPackageRequest`, `SelectedIdea`, optional CreatorProfile, VideoAnalysis, and source ScriptPackage. A Creator who explicitly asks for a script around their own stated topic/direction has made a selection, but only the deterministic direct-selection path may normalize it. Reuse goal, audience, desired audience action, and language already established in the conversation through this turn. Confirm only fields still absent in one question batch; never invent those required constraints. With a genuine upstream SelectedIdea, preserve that object and its references and skip this builder. Only when no such object exists and all required constraints are confirmed, put only the exact, unedited span that names the selected topic/direction in `request.direct_user_selection.literal_direction`, plus those three confirmed fields in `request.direct_user_selection`; do not append execution constraints such as "use no evidence", output-format instructions, or approval conditions to that literal span. Preserve those constraints separately in the request's applicable constraint/exclusion fields and package gates. Set the SelectedIdea reference to `schema_version: selected-idea.v2`, `artifact_id: direct-selected:<request-id>`, and the intended local output path; leave its `sha256` null until it exists. Run:

```sh
cd '<run>' && python3 '<skill directory>/scripts/build_direct_selected_idea.py' --request script-package-request.json --output selected-idea.json
```

The output path must equal the reference path. The builder preserves the literal direction exactly, uses the `direct-user-selection` sentinel and stable request-scoped IDs, and forces every context/evidence ref empty. Do not attach evidence to this projection; when validated evidence must be selected too, use the ordinary upstream SelectedIdea flow. An account, URL, or generic "write me a script" request is not a direct selection.
