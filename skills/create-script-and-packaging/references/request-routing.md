# Route the deliverable before checking package inputs

Apply this before asking for SelectedIdea, source script, assets or private performance data. Reuse the conversation's selected topic, language, audience and source; a follow-up edits that same deliverable unless the user changes direction.

First distinguish more ideas from packaging one idea. Requests for additional ideas
or same-style variations of earlier directions belong to `develop-content-ideas`,
even when those earlier suggestions include titles, covers or hooks. This Skill
applies when the user selects one specific direction and asks for its script or
packaging; the presence of packaging details in an idea list does not change a
request for more ideas into a packaging task.

## Resolve a referenced input before drafting

For “use option 2”, “revise the previous version”, “continue that episode” or
“another in the same series”, first identify the object the current request
refers to in the conversation through this turn or its available source artifacts.
Resolve a numbered choice to its original list and actual item; preserve that
item's topic, constraints and evidence status. The ordinal alone is not a topic.
Keep a native candidate's identity when available and use the existing selection
path; do not fabricate a SelectedIdea or replace missing source content with a
plausible topic to satisfy package inputs.

When the referent is unique and its needed content is available, reuse it and
complete the requested work without asking the user to supply it again. Check
the requested version and scope rather than automatically using the newest draft.
When several lists, scripts or episodes remain plausible, ask only which one is
intended. When the referenced content is missing, first use any permitted recovery
path for that same source, then ask only for the missing item or passage if it
cannot be recovered. A matching title, account, genre or remembered summary does
not fill in absent content. Preserve known constraints and independent completed
work while resolving the gap.

Keep the source's role explicit. A user-supplied script or an assistant draft the
user selects can support a revision or fictional continuation as a draft; neither
a creative proposal nor permission to edit proves that it was posted. A sequel to
an actual published episode needs that episode's content, ending and unresolved
point under [source-adaptation.md](source-adaptation.md), before supplying any
episode-specific recap or new events. The same check applies to practical advice
that would fill in those events; with a missing source, keep general craft advice
free of invented story details and resolve the gap first. For an existing script's
cover/opening alternatives, retain [partial-script-edits.md](partial-script-edits.md)
and its exact-message, version and authorized-scope checks. Those structural checks
do not establish that the agent chose the correct referent.

Distinguish continuing a source's events from borrowing a style or format. For a
same-series request, preserve whichever story continuity or format the user
actually refers to; ask only when that distinction affects the requested work and
remains ambiguous. An independent original brief, or an explicit request for a
new story using supplied style directions or a visible sample, can proceed without
retrieving a previous episode. Label creative proposals and retain the existing
evidence limits on claims about a named account's actual style or published work.

## Route the requested deliverable

| User needs | Route and minimum input | Completion evidence |
| --- | --- | --- |
| Sequel, part 2, or continuation of a prior post | Follow the continuation check in [source-adaptation.md](source-adaptation.md); establish the actual previous episode before drafting | New main beats and payoff grounded in the previous ending; if the source cannot be recovered, request its missing ending rather than inventing a continuation |
| Platform rules, tags FAQ, repost/duplicate-content question | Answer it as an advisory question from current official rules, outside the package; no SelectedIdea or ScriptPackage required | Answer the question with dated official sources and scope; if no current official source is available, say the answer is not checked against current rules; do not guarantee reach or exemption from duplicate-content rules |
| A generic editing or caption-animation workflow | Give an actionable method and name assumptions without a source clip | Steps and settings as a suggested workflow, not a rendered file or an exact reproduction of an unseen reference |
| Adapt a specified video or a channel's latest upload into written clips, hooks or carousel copy | Packaging owns the final adaptation; follow [source-adaptation.md](source-adaptation.md) and obtain only missing source facts | Source-grounded adaptation in the requested format; timed evidence for clip boundaries; the video Skill's teardown or creative proposal cannot replace the main deliverable |
| Opening hooks, CTA alternatives, captions or cover-copy rewrites for a stated topic | Read [topic-drafts.md](topic-drafts.md) and return the requested copy variants before any optional research; preserve the stated topic and label assumptions | The requested variants plus placement guidance and one grouped question batch only if missing offer/proof details change the copy; never substitute a research report or an account analysis |
| Recent proven hooks, latest clips, thumbnail trends | Relevant public evidence must exist before package synthesis: reuse it from the conversation or another Skill's output, or name the Skill that collects it (`research-youtube-trends`, `research-instagram-trends`, `research-tiktok-trends`, `analyze-creator-competitors`, `analyze-tiktok-competitors`) | Actual source URLs, publication/window dates, observed performance and comparison basis; inspect visual/audio material before claiming its content |
| Titles, covers or a script for a chosen topic | Reuse the exact selection and known constraints. If the requested creative draft is feasible but audience, goal or CTA has not been confirmed, follow [topic-drafts.md](topic-drafts.md); otherwise use the native direct-selection or upstream flow | The complete requested creative deliverable with proposals distinguished from confirmed inputs, or a validated native package when its inputs are available; no CTR or retention requirement unless the claim explicitly depends on private metrics |
| Titles/covers for “my next gaming clip” with no clip facts | Ask only for the concrete moment, outcome or intended story plus genuinely missing audience/action/language constraints | Do not fabricate gameplay events or ask for a whole account history to unlock drafting |
| Produce actual videos or edited shorts | Hand the resolved script and requirements to an available host media capability; follow [media-delivery.md](media-delivery.md) | Real output faithful to the requested subjects, visuals and required audio, verified for playback, layout and delivery; a beat sheet or generic substitute is not a completed video |
| Produce finished covers | Check an available image capability and authorized source or original assets | Real image output faithful to the requested content, verified for layout, dimensions and delivery; a cover brief is not a finished image |

A research request is not fulfilled by replacing requested observed examples with generated templates. If only 2 of 20 examples are verified, deliver those 2 with the evidence gap, or separately offer clearly labeled creative examples. “Latest” requires a fresh source listing and publication-date check, not a remembered video. Do not infer unseen actions, camera presence or thumbnail composition from titles.

A new cover design, an existing-image transform and a platform's AI-cover feature are different tasks: check whether the requested capability is available; never relabel one as another. Do not require finished media for a design brief, nor claim a finished image from the brief.

This Skill acquires no evidence itself; evidence comes from the conversation, the creator's files or another Skill's output, under that Skill's own readiness and retry rules. A failed path cannot silently become another source. An unavailable relevant capability is a platform coverage gap; unknown clip identity or unavailable private media is a user-input gap. Preserve independent completed work. Handoffs themselves are not completed packages.

After these routes, the core package contracts are unchanged. `packaging_only` still needs its validated source ScriptPackage; `full_package` and `script_only` still require a genuine selection. A creative proposal is not a native package and cannot satisfy an explicit request for validated package files or an execution handoff. Never fabricate a source script or approval just to satisfy that gate. Long-form completeness is also constrained by the agent host's delivery and output budget; this routing change does not claim to fix host truncation.
