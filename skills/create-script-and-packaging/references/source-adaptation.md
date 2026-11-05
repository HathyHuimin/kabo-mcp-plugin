# Adapt a source without losing the creative deliverable

Use this route for a request to continue an identified episode, or turn a video
into written clip plans, opening variants or a carousel. A reference-only analysis remains an
analysis task; actual edited media needs a media execution capability from the
host, which this Skill does not provide.
Do not add source research to an independent creative brief.

## Continue an episode or draft without repeating it

First distinguish continuing a published episode from completing a draft the
user supplied or selected. An available selected draft is sufficient for a
fictional continuation; do not ask for publication evidence or another copy.
Use the latest applicable selected text, not a conflicting earlier outline or
superseded ending. When the user asks to complete an existing part, continue after its last written
beat rather than restarting the part, unless the user requested a full rewrite.

Before writing, identify the source's stopping point, current character states
and established story rules: who knows what, what each character can do, and
what has already changed or been revealed. Carry those constraints into each
new action and line of dialogue. A condition may change as a new plot event,
but show its cause or occurrence before using the changed ability or state;
do not silently borrow a reversal from an older version. Check the complete
continuation against this source before delivery. This is a writing check,
not a request for new user inputs or a native-package requirement.

For a published sequel, resolve which episode the user means and establish its actual content before
giving episode-specific advice, including advice about how to approach a sequel
rather than a request for its full script. Reuse its actual script, the user's
description of its ending and unresolved point, or source observations already
available in this conversation. For "my latest post", verify the current listing
and then the selected post's content. An account review, title, thumbnail, genre,
profile description or remembered character may help identify a source; it does
not establish that episode's events. A previous assistant proposal or inference
is not proof of what the creator posted.

If that content is missing, begin the response with the source that can be
identified and the material facts still unknown. Follow the permitted recovery
path under **Obtain only the missing evidence**; when it cannot recover the
content, ask only for the missing script passage or a short description of the
ending and unresolved point. Do not first write a concrete recap, character
action, revelation, personalized hook or next plot beat from the metadata and
append a request for the ending afterward. A late caveat does not make that
invented continuity a grounded sequel. While resolving the gap, general advice
may explain how to advance an established consequence or keep a recap brief,
without filling those slots with unverified story events.

From that source, identify the established events, the ending/reveal and any
unresolved question. Keep the source reference attached to those facts; source
gaps remain gaps. Then choose a new consequence, obstacle, discovery or decision
that follows from the ending. A short recap may orient the viewer, but the main
beats and payoff must advance beyond the previous episode. Check the proposed
hook, central beats and payoff against the original before delivery; changing
wording, character labels or the final CTA does not make repeated events a sequel.
Clearly distinguish new fictional proposals from events observed in the source.

Do not generate a supposed continuation from the topic alone. If the creator says "I already did that",
recheck the source and identify the repeated beat before proposing a replacement;
do not repeat an unchanged failed retrieval or offer another guessed episode.
An independent new story is a different deliverable and needs the user's choice.
Keep a selected draft's fictional continuity distinct from claims about a
published episode; use [request-routing.md](request-routing.md) if the source
reference itself is ambiguous.

Preserve the requested output, source identity, target platform, language and
requested quantity while obtaining evidence. A URL identifies source material;
it does not authorize inventing its contents or a native SelectedIdea.

## Obtain only the missing evidence

Reuse evidence already retrieved for this source in the conversation or the
workspace. For "latest", the channel's current upload listing and dates must be
checked before selecting the video (a `review-creator-account` run lists them);
a remembered URL or matching title is insufficient.

This Skill fetches no video evidence and runs no other Skill. If source content
is missing, name `analyze-content-video` for that URL as the next step, keep the
original creative request with packaging, and say what scope to ask it for: a
source-facts question (what this video actually says or demonstrates, its
ordered actions, named methods or tools, evidence anchors and unresolved gaps),
with timed anchors when the deliverable needs cut points. Its transcript export
or timestamped evidence answers that; the source URL identifies the evidence
target, not an instruction to explain why the video went viral. Reading this
skill does not itself enable a video tool.

The creative request and its secondary originality question are not part of
that scope: the video Skill is not asked to choose a new angle or write the
carousel. Once its output exists in the conversation or workspace, consume the
source facts and limitations within that output's own evidence-validation
limits. If it also contains a diagnosis, craft teardown or creative proposal,
those recommendations do not redefine the main task. Resume packaging with the
original request once evidence is sufficient, rather than relaying that report
as the final adaptation. Do not claim a retrieval failed if no retrieval was
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
Do not invent speech from silent frames. When an analysis of this source is
already pending, wait for its output rather than suggesting another paid run.
Reuse its job id only while the host still accepts it; if the host rejects that
id as not belonging to this turn, the analysis has to run again in this turn —
do not ask the creator to describe a video that a permitted path can still read.
Respect terminal errors recorded in that output. When no permitted path can
recover material facts needed for the adaptation, identify that specific gap
and ask for the minimum missing source material, preserving usable work.

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
