# Signature content: a run with the Creator's saved account profile

Read this whole when `start` printed `creator profile: @… on youtube` and used it: with numbered
`signature content` lines, or with only the Creator's settings (excluded topics, a language or a
market they set). Its rules add to `references/video-trend-workflow.md`; where the two differ, this
file wins for this run. A run whose `start` printed no profile, or printed it as not used, follows
the workflow reference alone, and every command and call is the one it describes.

## Where the profile comes from

The profile is the complete `get_creator_profile` result for the Creator's linked YouTube channel,
read with `"include": ["content_profile"]` and `"effective": true` (once more without `effective`
when Kabo refuses the read only for it) and saved unchanged as a file. When the file carries
`effectiveContentProfile`, the profile with the user's own edits applied by Kabo, and the scripts
know its version, they read that; otherwise `contentProfile`. The scripts read either that complete
result or its `structuredContent` object, and refuse an error result. Never write, merge, trim or
edit a profile file, and never rebuild one from what a tool displayed: a tidied seed word retrieves
nothing, and a guessed language turns a search label into a filter. Without a saved profile there is
no `--profile`; plan exactly as without one.

The saved profile already carries the edits the user made (renamed, removed or merged signature
content, which one is main, excluded topics, a language or market they set): use it as it is. Never
type a correction the user states in this conversation into the Request or the profile file. To save
one, follow `references/profile-edits.md`; it applies from the next run, because `start` refuses a
different profile file for the same run.

`--profile` and `--video` are two different starts: a supplied video names its own channel as the
Creator, the profile names the channel it was read for. `start` refuses both together.

`start` copies the file into the run as `creator-profile-source.json` and prints, after the schemas,
the profile's content language, accepted languages, formats and numbered signature content items
(the main one first, marked `main`), each with its name, topic, approach and seed terms or hashtags,
then any topics the Creator excluded, the market they set and the edits that no longer match the
current profile (those are not applied). A profile for another platform is not used. A recognized profile with no signature content
keeps its reliable language, audience, formats and settings; only class-specific steps are skipped.

## The Request

- Leave `creator_profile.handle` out (`discover` fills in the profile's) or write that same
  channel's handle; a different handle stops `discover`. A channel id is never compared with a
  handle.
- Never write `content_language`, `accepted_languages`, `audience_count`, `formats`,
  `signature_content`, `excluded_topics`, `market_region`, `own_content_ids` or `profile_basis` into
  the Request yourself: `discover` copies them from the profile file before it freezes the window,
  and removes them when there is no profile file.
- The content language is a retrieval label, not a market and not a filter: never copy it into
  `locale.language`, which the Gate scores `locale_fit` against. A language the Creator set is a
  retrieval label too, and their market only the default search region (the collect call's region
  when `locale.region` is empty, never its language): never copy either into `locale`. Leave
  `locale` to what the user named.

## The query plan

`Request.niche` is the goal for this run. Preserve the user's explicit topic even when their
historical signature content covers something else. Only an account-led ask without an explicit
topic may derive its goal from the profile; write that goal into `niche` before freezing it.

Write natural queries about that goal, in the requested content language or the usable profile
language. Reuse a profile seed only when it helps retrieve the same topic and approach. Never add
unrelated historical seeds, require every class to be covered, or spend an intent on a class just
because it has a high weight. With two intents, keep one directly on the goal; use the other for an
on-topic wording or approach variant. When the profile conflicts, both stay on the goal.

The validator checks structure and excluded topics; it does not certify semantic alignment. Before
collection, read each complete query against `niche` and correct any drift. Finding a seed word is
not proof of alignment. For example, a cozy/indie game review request must not become gaming jobs
or blogging income research because those words occur in the account's historical seeds.

Never search an excluded topic. If the current request conflicts with a saved exclusion, ask the
user to resolve that conflict; do not silently edit their preferences or reinterpret the request.

## Discover

The collection call is unchanged, except that it carries the Creator's market as its region when
`locale.region` is empty. After coverage passes, `discover` writes the eligible candidates
(`scripts/filter_candidate_eligibility.py`) from the channel and duration each candidate came back
with: it removes the Creator's own videos (those of removed signature content items too), the video
format the Creator does not publish (180 seconds or less is a Short), channels outside 0.3–3 times
the Creator's audience where a count is known, and videos whose title or hashtags name a topic the
Creator excluded (those are never topped back up); it keeps every candidate it cannot judge (a
candidate without a duration stays), never removes one for its language, and tops a set under 20
back up with the removed channels closest in audience size. Every later stage reads that eligible
set.

## The Gate

When the table's first line lists `subject.signature_content`, each line gets a fifth number at the
end: `<n> <topic_relevance> <locale_fit> <creator_transferability> <signature_content>` (that list
always comes with `subject.anchor_handle`). The fifth is an integer from 0 to the number of listed
entries; every other rule of the Gate file stays.

- `signature_content`: the `number` of the entry whose approach this item reproduces, judged from
  that entry's `name`, `topic`, `approach` and `examples`, or 0 when it reproduces none. Write the
  number, never an id or a name, and pick the single closest entry. `topic_relevance` always judges the frozen `Request.niche`;
  `creator_transferability` and class matching are separate judgments. An on-topic item may
  match no class, and an off-topic class match must still receive a low topic score.
- Text after `platform summary, not by the author:` was written by the platform, not by the item's
  author. Use it to see what the item shows; never read it as the author naming a mechanism or as
  the author's wording.

When the table's first line lists `subject.excluded_topics` (topics the Creator excluded), with or
without signature content, `topic_relevance` is 0 for an item whose subject is one of them, however
it is worded and in whatever language.

## Rank

`rank` shares the videos the enrichment returned across the signature content items before it
Analyzes (`scripts/allocate_signature_content.py`): each item gets seats by its weight, at least
one each, from the videos the Gate matched to it, ranked by their channel-relative breakout
multiple and views, one video per channel and spread over different hashtags; the analysis reads
them. An item without enough matching videos gives its seats to the other items first, then to
videos that matched none; seats nobody can fill stay empty and are never padded. The enrichment
call itself is unchanged.

## The synthesis

When Analysis has `selected_content_ids`, every featured, pattern and emerging-creator reference
must belong to that set, including selected items whose `signature_content_id` is null. `rank` prints
only selected candidate evidence for synthesis; the saved Analysis retains the full population for
calculation and validation. An empty selection is not relabeled as passed evidence.


When the Analysis lists `signature_content` (each item with the seats it was given), cite at least
one video whose `ranked_content` row carries that item's `signature_content_id`, in
`featured_content_refs` or a pattern's `content_refs`, for every item whose `filled` is above 0;
the report validator rejects a report that leaves one out. The ranking stays as printed: the marks
only make sure each kind of content the Creator is known for is represented. When an item filled
fewer seats than its `quota`, note it briefly in `data_gaps`, never in Creator-facing prose, and
never make up the difference with videos that were not seated.


When no recommendation survives, the report adds up to three `reference_videos` from this run's existing candidates, ordered by the existing topic/locale scores. These are visibly marked as uncertain references, without invented metrics, dates or strong-match claims. Empty authored selections reuse available selected evidence first. Do not stop before the report stage or claim that no videos were found solely because the selected list is empty. No extra search or model step is added; genuinely empty source data cannot supply a video, and explicit excluded topics remain excluded.
