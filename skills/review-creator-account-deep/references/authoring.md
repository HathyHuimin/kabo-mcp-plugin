# Authoring the deep review

Read this whole before workflow step 4. Commands use `<skill directory>` (the folder holding
`SKILL.md`) and run from this request's run directory; keep the same run directory for every
command below.

## Reading the packet

`watch_media.py` (workflow step 2) already built `deep_synthesis_input.json`, created the seed with the
delivery language you passed as `--output-language` (`en` by default, or `zh-CN` for Chinese) and printed
the authoring packet after the frame reads. Read that packet's complete output before writing findings: it includes every watched content ID, every selected
opening and later cue with original seconds, metadata status, origin and limitations, baseline
availability, and the fully expanded source-bound authoring schema. It omits raw metadata bulk
while the full original JSON stays unchanged. Check `packet_end.included_content_ids` against
`all_watched_content_ids`.

If the host truncates any output, read the schema with
`cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/read_deep_synthesis_packet.py' deep_synthesis_input.json --authoring --schema-only`
(through `contract_end`) and run
`cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/read_deep_synthesis_packet.py' deep_synthesis_input.json --authoring --video <original content_id>`
for **each** watched video until all selected cues have been displayed; never use a fixed first-N-line
slice of the evidence. The packet proves a bounded reading of these selected cues, not the whole
transcript, audio listening or image viewing. Inspect every frame you cite through the image-viewing
path; available frame counts are not viewed frames.

A non-English seed requires the matching `--stability` input, including catalog formats excluded
from the watch set; a mismatched analysis ID is rejected before writing the seed. A non-English
seed also rejects unsupported unwatched reasons before writing; the English seed stays compatible
with arbitrary or null reasons.

## The authoring basis

The builder exclusively creates this run's `deep_synthesis_input.json` with `authoring_basis`: the
original four source paths and byte hashes, the current working directory and the exact
projection. This is a business provenance check, not a signature or proof of viewing. Every
authoring read and composition rechecks those bytes, identities and the source projection. Keep the
same run directory; the standard analysis may be the standard review's own validated file in its
run. Never edit the generated basis to accommodate changed evidence.

## Writing `account_deep_authoring.json`

The watch resume exclusively creates `account_deep_authoring.json` from
`references/account_deep_authoring.schema.json`. Fill only its judgment and display fields,
`observations`, pattern and recommendation point refs and `other_limitations`. Do not repeat
`analysis_id`, `data_gaps`, cue quotes or ends, or frame seconds.

- Select each actually read cue as `"<cue point_ref>": {}` and each actually viewed frame as
  `"<frame point_ref>": {"description":"<narrow observation>"}` in `observations`. The compiler
  copies that exact original cue or frame point, derives metric gaps from source availability, then
  applies the full canonical validators. It never corrects the meaning of an observation or turns
  available frames into viewing claims. For every video with selected timed cues, read and select at
  least one real cue; leave frame observations empty if images were not viewed. Unknown refs and
  source points that collapse onto the same video, kind and second are refused.
- Use point refs printed by the packet. Opening frames (up to 3 s) are already in it; for already
  viewed non-opening frames, add repeatable `--frame-object-ref <original object_ref>` arguments to
  the read-only command below; this maps their exact source IDs and nominal seconds without an extra
  per-video read. Follow the
  original `object_ref` you read the frame with: a local image filename, hash or displayed image
  number is not a canonical frame ID. Unknown, duplicate or ambiguous object refs are refused. A
  printed ref does not prove viewing; inspect each cited frame.
- The seed is created only once; an existing seed is never overwritten. Include further already
  viewed points with the read-only mode (omit the second argument when one point is enough):

  ```sh
  cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/read_deep_synthesis_packet.py' deep_synthesis_input.json --authoring --frame-object-ref "<original-object-ref>" --frame-object-ref "<another-original-object-ref>"
  ```

  This is a real additional script read, not another fetch or viewing claim. If its output was
  truncated, the `--authoring --video <original content_id>` read displays that video's full point
  index; account for that real extra read rather than assuming it happened.
- `headline` is at most 80 characters, counting spaces and punctuation. Patterns use `pattern`,
  `explanation` and `pins`, where pins are point_ref strings selected from this same video's
  evidence; `next_shoot.pins` uses the same shape. Keep the required judgments (`practices` is a
  string), available current counts and evidence limits. Do not invent arrays or machine
  overrides. A cue onset with unknown end does not establish completed speech by a checkpoint;
  sparse frames support sampled moments, not continuous motion. Explanations still need truthful
  source judgment.
- Existing canonical `account-deep-synthesis.v1` inputs remain supported with their original shapes,
  literal cue and point checks and availability checks. An input without `authoring_basis` cannot
  use the authoring mode; never convert an old failed draft into a passing claim. Marked evidence
  still requires its matching synthesis input.

## Opening guidance

`deep_synthesis_input.json` supplies the lightweight `hook_context` catalog of opening-mode IDs and
names; the names alone are not a mode's details. Before writing the next-shoot recommendation, read
`references/hook-core/research-review.md`, choose a suitable mode, and read its picture, sound,
joint expression and material requirements only with this command, in this same synthesis:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/prepare_hook_context.py' --platform <platform> --purpose deep_review --pattern-id <Mxx> --output hook-opening-<Mxx>.json
```

Never open the files under `references/hook-core/` other than `research-review.md`. No additional
fetching or viewing is needed.

- When an opening mechanism or recommendation fits, keep it in the existing fields. Choose a feasible
  mechanism from declared material: proof needs authorised readable proof, comparison needs both
  sides, demonstration needs the actual action or workflow. Make the recommended opening one complete
  production choice in the existing recommendation or video-card prose: the first picture and the
  first spoken or heard sound (or an intentional silence) together, then the immediate meaning or
  question their combination conveys. Intentional quiet, no title and first-person narration can be
  valid; apply checkpoints to the content form.
- Keep opening observations in the watched-video findings and pins, bound to the current watched
  refs and sampling limits. Reserve action order, visible proof, first frames, text timing, sound and
  delivery for observed media. With titles or captions only (unwatched rows), keep findings about
  their wording and label inferred mechanisms as title or caption interpretations — "the titles
  suggest a demonstration direction" — rather than narrating what the creator shows or does. State
  proposed execution as a recommendation for the creator's next video. Public performance numbers do
  not establish why an opening worked; a transcript is not a listening observation.
- A mode is method context for the proposed next shoot only: it supplies no observed frame, spoken
  line, craft pin or viewing or listening evidence. Unconfirmed materials are preparation
  requirements, not assets the creator already owns. Mode IDs, internal labels and helper commands
  stay in run artifacts; write the production choice in the delivery language.

Write `account_deep_authoring.json` using `deep_synthesis_input.json` and `catalog_stability.json`
as current-task evidence, and bind every observation to the watched evidence.

## Counts, gaps and presentation

- Keep current public counts, historical observations and input visibility separate.
  `baseline_availability.current_followers` carries the current count only when the standard
  analysis already includes it. `not_carried` means this input did not carry that field, not that
  the platform lacks or hid it. Existing public evidence may contain a current subscriber count even
  when the compact deep input does not; do not fetch it again to fill this field. Historical follower
  counts and repeated view observations are not established by upload dates and current cumulative
  views; their absence prevents verifying recent growth without erasing available current counts.
- In source-bound authoring, metric gaps are compiled from `baseline_availability`; an available
  count is never authored again as `not_carried`. Add only other real limitations in
  `other_limitations` as strings. Existing canonical inputs keep their structured `data_gaps`
  contract, including rejection of any gap contradicting available source data.
- For non-English output, author `presentation` in the same synthesis turn. Its `format_labels`,
  `role_labels`, `pin_kind_labels`, `seconds_unit` and `metric_gap_labels` are display text in
  `output_language`, keyed by the unchanged canonical codes. Fill every format used in the stability
  rise/fade rows, every role in watched and unwatched evidence, both `cue` and `frame` labels, and
  every metric used in structured gaps. Add `not_watched_reason_labels` for every reason displayed
  when there are unwatched items. The seed contains blank display fields, not translations: complete
  them and add any used format missing from the seed. Null, empty or missing used display values are
  rejected. English may omit `presentation`.
- Metric-gap display text is a complete sentence preserving the matching `not_carried` scope and its
  historical/current distinction; it never invents platform absence or changes the canonical
  availability fact. Keep metric gaps structured in every language, never moved into a free-text
  `detail`. Display fields change no IDs, codes, pin seconds or evidence. Findings and free-text
  details use the delivery language too. The report numbers videos (`Video 1`, or the
  delivery-language equivalent, watched first in evidence order) instead of printing platform
  content IDs; authored prose and gap details never quote a content ID either, so refer to a video by
  what it shows or its role. Never defer translating these labels to another round.

## Check before composing

`compose_deep_review.py … --check-only` is a read-only, non-final step of the same run. A `valid`
result permits final composition. Only two cases return `needs_correction`:

- A pin to an exact available source point that was not selected in `observations` (or registered
  in legacy `video_observations`) names the authored JSON path, the original same-video cue or frame
  timestamp, and the points already registered. This is material for correction, never a viewing
  claim: read the cue or view the frame before recording a truthful observation; otherwise remove or
  narrow the unsupported pin and explanation.
- Authored text over its canonical limit (such as `headline`) is listed in `issue.text_too_long`
  with its path, length and `max_length`, only when no other error remains; rewrite that field
  shorter without truncating it mid-sentence.

Correct only this open run's author file, then check again. The check never changes files, observes
media, fetches data or repairs a closed run. Unknown IDs, invented points, invalid source bindings,
malformed files and other errors remain failures. Never proceed to final composition on
`needs_correction` or any failure.

A successful check also returns `grounding_review`: report-body claims with their field paths and
pins, beside each video's authored frame descriptions and literal recorded speech. Read it through
`packet_end`; truncated material is an unresolved review gap, not permission to skip it. `valid`
checks structure and source binding, **not pixel interpretation**. Compare the frame descriptions
with the actual images already displayed, then the headline, answer, patterns and next-shoot claims
with those observations. A cue start is speech evidence, never the first visual appearance of its
subject; a sampled frame proves neither an unseen interval nor a first occurrence. If necessary,
narrow unsupported text once in this same open run and recheck, sharing the one correction
opportunity rather than adding a review loop. If the uncertainty remains, omit or explicitly limit
that claim. Never fetch again, invent observations, add another model pass, or claim the script
recognized an object. The check itself neither rewrites the draft nor certifies semantics.

If a command is rejected before running while the run remains open, correct it and run it again.
Once a terminal failure is reported, stop and preserve every artifact of that run; never overwrite
or replace it.

## Report voice and fields

Required voice: the report is written to the creator about their account, never about this Skill.
It does not narrate stages, does not compare what the catalog said before with what the videos said
after, and does not use this Skill's internal words for its own machinery. A video that yielded
neither frames nor a transcript is simply one the conclusions do not rest on; saying so once in the
evidence list is enough.

- `question_answer` is a complete-sentence answer to the frozen original question. Catalog figures
  come from `catalog_stability.json`.
- `insight` is one sentence that still helps after deleting view counts and practice names.
- `practices` names practices locked from watched videos, under the name the frames and spoken lines
  support — a class the catalog labelled from its title alone is simply renamed, with no
  before-and-after. The report is what this account should do next, not a record of how this Skill
  reached it. Unwatched rows stay provisional.
- Craft pins come after the answer and only from watched videos. Every authored pin is an original
  point_ref; compilation supplies the watched `content_id`, `cue` or `frame`, and original
  `start_s`. Legacy canonical pins keep that three-field shape.
- `next_shoot.keep` / `next_shoot.stop` are 1+1: keep the Leo class, then name the next-shoot cut
  inside that class (hook, first frame or payoff second). Never replace the class with a topic
  slogan.
