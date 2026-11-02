# Bind selected content to existing history (`selected_timing`)

Read this whole when the request is `selected_timing`. Commands use the run directory and
`<skill directory>` as `SKILL.md` sets them up.

When the user supplies a newly selected `selected-idea.v2` and an existing real PublishHistory,
reuse that history without retrieving or projecting new data. Preserve the original files and
copy the SelectedIdea and History into this run as `analysis/selected_idea.json` and
`analysis/publish_history.json`. Author a new `analysis/intent.json` with a new request id, the existing creator
timezone and the exact history format vocabulary (`youtube_long` or `youtube_short` for a public
YouTube snapshot). Content language comes from the SelectedIdea; the language of the
Creator-facing report is separate. Do not rename history rows to a generic `youtube_video` format
or change approved content to fit a request.

`build_publish_request.py` with `--selected-idea` binds the actual selection bytes. It derives the
selected id, schema, hash, relative path and content language, fills unambiguous missing
platform/format and the history account, and refuses explicit conflicting intent fields. It also
refuses to overwrite an existing output in this mode. A conflict requires a new honest intent; it
does not authorize editing the SelectedIdea or History. The direct-input timing path without
`--selected-idea` remains available.

With `analysis/intent.json`, `analysis/selected_idea.json` and the unchanged `analysis/publish_history.json` present, the
first command group is these four non-final steps, in this order:

```sh
cd '<run directory>' && python3 '<skill directory>/scripts/build_publish_request.py' --intent analysis/intent.json --history analysis/publish_history.json --output analysis/publish_request.json --selected-idea analysis/selected_idea.json
cd '<run directory>' && python3 '<skill directory>/scripts/compute_timing.py' analysis/publish_request.json analysis/publish_history.json --full --run-id <run_id> --generated-at <now> --output analysis/publish_recommendation.json
cd '<run directory>' && python3 '<skill directory>/scripts/validate_publish_recommendation.py' analysis/publish_recommendation.json analysis/publish_history.json analysis/publish_request.json
cd '<run directory>' && python3 '<skill directory>/scripts/build_publish_schedule.py' analysis/publish_recommendation.json --draft-ref <draft_ref> --now <now> --output analysis/publish_schedule.json
```

`<run_id>` is this run's id (the last segment of the run directory), `<draft_ref>` is the
SelectedIdea's `selected_idea_id` read from its JSON, and `<now>` is the current ISO instant. This
operation starts at the existing History and performs no retrieval, assembly or projection. It does
not validate the final Schedule; when it proposed one, run that check before handing the chain to a
calendar:

```sh
cd '<run directory>' && python3 '<skill directory>/scripts/validate_publish_recommendation.py' analysis/publish_recommendation.json analysis/publish_history.json analysis/publish_request.json --schedule analysis/publish_schedule.json
```

Only a `recommended` outcome produces a proposed Schedule. For other outcomes validate without
`--schedule` and preserve the absence of a slot. Pass the entire new Request/History/Recommendation/
Schedule lineage to a calendar, with its target and cadence publish format copied verbatim from the
bound Request. Changing only an old Schedule's `draft_ref` leaves the old Request unbound and is
not a complete typed handoff. These local artifacts grant no calendar, reminder or publish action.

Then write the result and run the second command group exactly as `SKILL.md` says.
