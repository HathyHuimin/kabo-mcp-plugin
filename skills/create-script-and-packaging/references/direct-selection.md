# Direct selection

Read this only for the native direct-selection branch: no upstream SelectedIdea exists and the
Creator has confirmed the required constraints for their selected topic or direction. Commands use
`<run>` and `<skill directory>` exactly as `SKILL.md` defines them.

Preserve a genuine upstream SelectedIdea and its references instead of rebuilding it here. A
selected topic or permission to edit a draft does not confirm every proposed constraint inside it.
Reuse goal, audience, desired audience action and language established by the user. If any required
constraint is still a proposal, use the creative-draft route in [topic-drafts.md](topic-drafts.md);
ask one grouped clarification only when native files are explicitly required. Never invent these
constraints or turn assistant suggestions into user confirmation. Then put only the exact, unedited span that names the selected topic/direction in `request.direct_user_selection.literal_direction`, plus those three confirmed fields in `request.direct_user_selection`; do not append execution constraints such as "use no evidence", output-format instructions, or approval conditions to that literal span. Preserve those constraints separately in the request's applicable constraint/exclusion fields and package gates. Set the SelectedIdea reference to `schema_version: selected-idea.v2`, `artifact_id: direct-selected:<request-id>`, and the intended local output path; leave its `sha256` null until it exists. Run:

```sh
cd '<run>' && python3 '<skill directory>/scripts/build_direct_selected_idea.py' --request script-package-request.json --output selected-idea.json
```

The output path must equal the reference path. The builder preserves the literal direction exactly, uses the `direct-user-selection` sentinel and stable request-scoped IDs, and forces every context/evidence ref empty. Do not attach evidence to this projection; when validated evidence must be selected too, use the ordinary upstream SelectedIdea flow. An account, URL, or generic "write me a script" request is not a direct selection.
