# Recommend and fulfill a script duration

A current user-specified duration wins. Reuse a prior user-confirmed duration only
when the same brief, platform and format still apply; a new instruction overrides it. Never default
all scripts to 8–10 minutes. Packaging-only/title edits reuse the existing script
and do not trigger duration research.

Keep an assistant's suggested length as a draft estimate unless the user confirms
it. Asking to develop the same topic does not make every earlier suggestion an
explicit current requirement. In a native intake, set `target_duration_seconds`
only for the current user's explicit target; put an applicable prior confirmed
target in `prior_target_seconds` with `prior_applies=true`. Otherwise omit the
target and let the helper estimate it. A creative draft may propose its own
duration without creating a native request; state that it is an estimate.

Before building a script request with no duration, reuse relevant public duration
fields already acquired for the target creator channel: in the conversation, or in
the JSON output of another Skill in this workspace (for example a
`review-creator-account` run for that channel), preserving provenance and the
target-turn cutoff. This Skill fetches no channel data. If none exists, do not
block: write the duration context with `"samples": []` and
`"research_status": "not_available"` (the helper's defaults) plus the content
inputs below, say that a channel-based estimate needs that account review, and
let the helper use its content estimate.
A failed or unavailable path stays recorded and is not silently replaced. Do not query unrelated accounts
or mix Shorts/Reels with long videos. Source URLs, channel identity, date window,
format and topic relevance are necessary for a meaningful distribution.

Write local duration context with optional `channel_id`, `samples`,
`research_status`, `prior_target_seconds` and `prior_applies`; content inputs are
`complexity: light|medium|heavy`, `steps`, and `demonstration_seconds`. Each sample
has `channel_id`, `platform`, `format: short_form|long_form`, `duration_seconds`,
`published_at`, `url` and `topic_relevant`. Project them from acquired evidence,
not model-invented averages. State why these samples are comparable. The helper
excludes mismatched/missing/duplicate records and, with at least five samples,
excludes durations outside one quarter to four times the median. It reports the
retained n, mean, median, range, date window and exclusions; this is a bounded
channel sample, not a platform-wide statistic.

Run `cd '<run>' && python3 '<skill directory>/scripts/script_duration.py' --request intake.json
--context duration-context.json --output script-package-request.json` before the direct
selection builder or generation. `intake.json` may omit
`content_format.target_duration_seconds`. The prepared native request has an
integer target and reproducible `duration_plan`; ordinary explicit requests
remain compatible. The helper combines comparable median with an explicit
content-workload estimate. Without usable channel data it uses a transparent
short/long-form content estimate, never a fabricated platform average. Explain
that estimate briefly in the creator's language and allow adjustment. Only an
unclear platform/form that changes the task needs clarification; missing duration
alone does not. If requested time cannot hold necessary content, explain the
tradeoff instead of silently changing the explicit target.

Write substantive spoken copy and actual production actions to fit the target.
Scripts using a prepared duration plan must include `script.timing` with `pace: normal|deliberate|brisk`
and `non_speaking_segments` (each has a unique `scene_order`, `seconds` and a
concrete `purpose`). Segment time is additional non-speaking time, not time that
overlaps narration. Do not label empty padding as a demonstration. Use contiguous
`MM:SS–MM:SS` scene ranges. The validator estimates spoken words/Chinese characters
using disclosed planning rates, adds justified non-speaking time, and rejects
obvious underlength/overlength scripts and inconsistent structured timelines.
This is an estimate, not a measured performance. Review pace and useful content,
then recommend a read-through before production. Do not add filler to pass.

Planning assumptions are 240 Chinese characters/minute, 300 Japanese kana/kanji
or Korean Hangul/Hanja characters/minute, 130 Arabic words/minute and 160 words/minute
for other space-delimited languages, adjusted by pace. These are explicit
heuristics, not measured language averages. Publication dates accept full
YYYY-MM-DD or ISO date-time values, never truncated invalid suffixes.

Date-only and timezone-free samples use UTC for window ordering; offset-aware
timestamps are normalized to UTC. Report boundaries retain the original source
strings selected by chronological order.

The renderer emits one English set of timing labels. Before delivery, explain this timing basis in the requested language, preserving every estimate, target, sample statistic and the not-measured qualification; never leave an unexplained English fallback for another language. Creative text stays in its original language.
