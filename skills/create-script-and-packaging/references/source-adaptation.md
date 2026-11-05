# Adapt a source without losing the creative deliverable

Use this route for a request to continue an identified episode, or turn a video
into written clip plans, opening variants or a carousel. A reference-only analysis remains an
analysis task; actual edited media needs a media execution capability from the
host, which this Skill does not provide.
Do not add source research to an independent creative brief.

## Finish a supplied partial draft

For an available draft, distinguish appending its missing ending from rewriting or expanding it.
"Continue" or "finish this part" means append from the latest applicable stopping point;
return the whole earlier section only when the user asks for a full rewrite or expansion.
Do not require proof of publication or ask for the same draft again.

1. Copy the selected partial draft verbatim into `previous-script.txt` in the workspace. Identify
   its final action, already completed beats, current character states and the condition of props
   that the continuation uses. A superseded outline
   does not override the latest selected text.
   Before drafting, write `source-state.md` from that text: the stopping action, established past
   events and every loss or constraint on a character, object or resource, with its short source quote. Include
   unavailable abilities and broken, lost or consumed objects even if the planned continuation
   does not use them. Keep them unavailable unless the new text explicitly establishes a transition.
2. Write only the missing continuation into `continuation.md`. Its first new action follows the
   source's last action; do not restart the part. One brief orienting sentence is enough. Preserve
   established past events. Distinguish what the source showed from a character's belief or an
   unresolved question. A new twist can develop the latter without undoing the former. If a new
   account contradicts an established event, depict the conflict and reconcile it in the story,
   or leave that account explicitly unverified; do not silently adopt it as the new truth.
3. Run the literal-overlap check on those actual files, with both texts returned for review:

   ```sh
   python3 '<skill directory>/scripts/check_continuation_overlap.py' --source '<workspace>/previous-script.txt' --continuation '<workspace>/continuation.md' --review-texts
   ```

   A nonzero result requires fixing the indicated input or repeated passages, then checking the
   same files again. Never edit this checker or call its success semantic validation. It catches
   substantial copied passages, not plot, character or prop errors; step 4 remains required.
4. Review the returned source and actual finished text before replying. Save a short
   `continuity-review.md`: the source's last action versus the continuation's first new action;
   then every entry in `source-state.md` versus its actual use or absence in the finished text,
   including the ending and cliffhanger. Do not drop a constraint from review because it was
   absent from the outline. Quote the relevant short passages, rather than only declaring that a check passed.
   Compare any new explanation of a past event to the source's actual account; a denial of that
   event is not an added consequence just because the review calls it a revelation.
   A damaged or lost object cannot silently work again; a character cannot silently regain an
   ability. Depict any repair, replacement, recovery or other transition before relying on it.
   Explicitly attribute recordings, imitations, gestures and inner thoughts so they cannot read as
   ordinary dialogue. Remove repeated completed sequences, including paraphrases. Fix every
   contradiction in `continuation.md`, then rerun step 3 and update the review against that file.
5. Deliver the checked continuation itself. Do not generate a second, different version in the
   final reply. Keep work files out of the creative text unless the user asks to download them.

For an explicit complete rewrite or expansion, preserve the selected story's facts and states
and read the finished text back as in step 4; literal overlap is expected and the append-only
check does not apply. For a published post with missing content, use the clarification boundary
below instead of manufacturing either source file.

## Continue an episode without repeating it

Resolve which episode the user means before writing its sequel. Reuse its actual
script or source observations already available in this conversation. For "my
latest post", verify the current listing and then the selected post's content.
An account review, title, thumbnail, genre or remembered character is not the
episode. A previous assistant proposal is not proof of what the creator posted.

From that source, identify the established events, the ending/reveal and any
unresolved question. Keep the source reference attached to those facts; source
gaps remain gaps. Then choose a new consequence, obstacle, discovery or decision
that follows from the ending. A short recap may orient the viewer, but the main
beats and payoff must advance beyond the previous episode. Check the proposed
hook, central beats and payoff against the original before delivery; changing
wording, character labels or the final CTA does not make repeated events a sequel.
Clearly distinguish new fictional proposals from events observed in the source.

For a published episode whose content is absent, follow the evidence boundary below
when the creator supplied or confirmed its exact link. Do not ask for that link again or infer
the episode from an account post list. When clarification is needed, ask only for the missing
script or a summary including the ending and unresolved point; offer a source link as an
alternative only when none was supplied, then stop. Do not append
examples, hooks or a guessed continuation. Do not insert guessed characters, events or a series
into the clarification itself; ask neutrally about the actual previous post. An available selected draft does not need this
clarification: use its actual text and stopping point. If the creator says "I already did that",
recheck the source and identify the repeated beat before proposing a replacement;
do not repeat an unchanged failed retrieval or offer another guessed episode.
An independent new story is a different deliverable and needs the user's choice.

Preserve the requested output, source identity, target platform, language and
requested quantity while obtaining evidence. A URL identifies source material;
it does not authorize inventing its contents or a native SelectedIdea.

## Obtain only the missing evidence

Reuse evidence already retrieved for this source in the conversation or the
workspace. For "latest", the channel's current upload listing and dates must be
available before selecting the video (an existing account-review artifact can supply them);
a remembered URL or matching title is insufficient.

This Skill fetches no video evidence and runs no other Skill. If source content is
missing for a supplied URL and no terminal retrieval failure is already recorded,
suggest `analyze-content-video` for that URL as the next step, preserve the original
creative request, and end this turn. State the needed scope in that suggestion: a
source-facts question (what this video actually says or demonstrates, its
ordered actions, named methods or tools, evidence anchors and unresolved gaps),
with timed anchors when the deliverable needs cut points. Its transcript export
or timestamped evidence answers that; the source URL identifies the evidence
target, not an instruction to explain why the video went viral. Reading this
skill does not itself enable a video tool. Do not invoke another Skill, wait for
its job, reuse a job id or orchestrate a return to packaging within this turn.
If analysis is already pending, state that the evidence is pending and end the
turn without recommending a duplicate run.

The creative request and its secondary originality question are not part of
that scope: the video Skill is not asked to choose a new angle or write the
carousel. When packaging starts with an existing analysis artifact in the
conversation or workspace, consume its source facts and limitations within its evidence-validation
limits. If it also contains a diagnosis, craft teardown or creative proposal,
those recommendations do not redefine the main task. Complete the original creative
request from sufficient existing evidence rather than relaying that report as the
final adaptation. Do not claim a retrieval failed if no retrieval was
attempted. Bind a video-analysis result through `video_analysis_sources` by its
`analysis/video_analysis.json` (`video-analysis.v1`, `artifact_id` = its
`analysis_id`), never by its report Markdown or a summary written here.

A missing transcript does not establish that visual evidence is unavailable.
If the video Skill can analyze this source and duration, it can obtain visual
observations without a transcript. Preserve the
evidence origin: when `frames.origin` is `model_observation`, describe the
observations as reported rather than directly seen. Paraphrase and attribute
`observations[].on_screen_text`; never present it as a verbatim source quote,
even if an upstream narrative suggests quoting it. An exact quotation needs
independently verifiable source text or a directly inspected source frame.
Do not invent speech from silent frames. Respect terminal errors recorded in
existing evidence. If retrieval of a supplied URL failed or the available result
still lacks necessary facts, identify the gap and ask only for the missing script
or a summary covering the ending and unresolved point; do not request that URL
again. If no URL was supplied, a source link is also a valid alternative. Preserve
the original deliverable and usable work, and end this clarification turn.

## Return to the requested format

Once sufficient evidence is available, complete the adaptation in the same
task. A video-analysis summary, a plan to write later, or generic originality
advice alone is not the requested package.

Distinguish transferring source content from borrowing its format. When the
user asks to turn this video's content into another format, retain its central
subject, substantive takeaway and evidenced workflow or examples in the main
deliverable. A named method or tool may remain when it is part of what the
source teaches; describing its evidenced steps in new copy does not require
discarding the method. New framing and wording may change, but a viewer should still
learn the source's underlying content. Do not replace a demonstrated workflow
with a generic lesson about adapting content. Borrow only a hook or structure
for a different subject when the user actually requested that transfer.

Treat secondary requests about originality, attribution, tone or accessibility
as constraints or accompanying notes, not as a replacement topic. Before
delivering, compare the main pages or clips with the source subject and the
requested transformation; correct topic drift using the evidence already held.
This check needs no extra research, tool call or separate planning response.

- For a carousel, give each page's copy and visual direction, including the
  opening and ending. Add original framing or explanation while preserving
  source facts; distinguish proposed examples from observed events.
- For written clips, give the requested cut ranges, reasons, hooks and captions.
  Each cut boundary must be supported by timed transcript or visual evidence
  for the same video. Distinguish original-video times from a proposed edit's
  timing. Resolve conflicting timestamps before recommending precise cuts;
  untimed themes are only a partial result when the user requested cut points.
  Write timecodes as `mm:ss` or `hh:mm:ss`, with the seconds field below 60;
  do not imply millisecond precision from unreviewed automatic subtitles.
  Keep the recommended range consistent with the intended excerpt: do not
  include the next topic and then instruct the editor to remove it. When the
  exact sentence boundary is uncertain, mark that boundary as approximate
  and identify the source phrase to check instead of inventing a precise cut.
- For opening variants, retain the evidenced mechanism while delivering the
  requested new wording for the creator's subject, not a copy of the source.

Do not demand private performance metrics to draft an adaptation. Credit alone
does not establish permission, and new wording alone does not establish rights
to reuse footage. Do not invent a number of changes or percentage threshold
that guarantees originality or permission. Do not claim a rendered video,
image, approved package or
publication. If native package files were requested, use the existing selection,
validation and rendering contracts; this route does not replace those gates.
