# Discovery lanes and Workflow judgment 2: the relevance Gate

Read this file whole before running `discover`; it explains what `discover` and `gate` do and how to
score the tables they print.

## Collection

`discover` passes each lane's generated input unchanged to `collect_instagram_trend_candidates`
(`trend-collection-input.json`, and `trend-collection-input-reach.json` for the reach lane); its
optional fields are never rebuilt by hand. When it hands the calls over, make each printed
`collect_instagram_trend_candidates` call with exactly its printed arguments — with two lanes both
calls go out in the same response, as separate individual calls — fetch each complete result (stage
it when `fetch` reports no ready download), and run `discover` again with the same arguments plus `--placed`.

A collector call that fails before returning a candidate set (an MCP error without one, which
`stage` refuses and prints) follows the failed-call rule in `SKILL.md`: one retry for a rate limit,
server error or empty answer, made before staging the other lane's result; never an unchanged retry
of an invalid input. A collector answer that is itself a failed or partial candidate set is staged
and placed like any other.

`discover` persists the returned `trend-candidate-set.v1` as `trend-candidate-set.json` from the
staged record of the result. When it reports that the collection result was not recorded (because
no result for that call could be staged), this stage cannot complete in this run: stop and report
that the Reel search could not be completed, with the tool's own message when there was one, and
that the collection result was not recorded by the host: never write or reconstruct items
yourself, and never call collection again to repair a local file, even if the response might be
cached. Do not create a placeholder candidate set for a tool-level argument error. The saved object
must have the expected schema and an explicit `items` array; missing items are not an empty result.
Keep the canonical deduplicated Search/Popular union. Never compare their incomplete lightweight
fields before detail completion, and never control cursors or retries in the Skill.

With two lanes both results share one schema, so `discover` persists them under neutral names and
lets the lane window bind each one. `drive.py discover` runs this binding itself; do not run it by
hand. For reference, it is equivalent to:

```bash
cd '<run directory>' && python3 '<skill directory>/scripts/bind_lane_candidate_sets.py' --relevance-input trend-collection-input.json --reach-input trend-collection-input-reach.json \
  --set trend-candidate-set-lane-1.json --set trend-candidate-set-lane-2.json \
  --relevance-output trend-candidate-set.json --reach-output trend-candidate-set-reach.json
```

The script reads the `window` each lane's input asked for and the `window` each result echoes, and
writes `trend-candidate-set.json` and `trend-candidate-set-reach.json`; it refuses when a result
matches neither lane or both carry the same window.

## One lane or two

Run **two discovery lanes** when the Request window spans a full year or more: the relevance lane
over the last month, and the reach lane over `last-year`. The reach lane uses the existing yearly
collector bucket; it must fit the frozen Request. Do not create it for an explicit Request shorter
than one year. Same intents, same budget, one call each. The two lanes answer different questions —
what is happening now in this format, and what has travelled furthest in it — and the second is
unanswerable inside a one-month window because a month of a niche rarely contains its biggest posts.

**Score the two lanes with separate Gate tables and never merge them into one pool.** A merged pool
defeats the arrangement: the top relevance tier fills up with both lanes' content, the saturation
point is never reached inside twelve slots, and the delivered list comes out identical to plain tier
descent (measured: identical item for item). Each lane's scores are positions inside its own pool,
so putting them on one list measures with two different rulers. Slot allocation between lanes
belongs to the backend, which is why every item carries its `lane`.

Use a single lane when the Request window is shorter than a year — a month, a named week, or the 90
days of `recent_90d`; `freeze_request_window.py` prints that single lane and its bucket. The reach
lane has nothing to add there, and the builder rejects the combination rather than emit a lane the
backend would filter away wholesale. Do not recover a rejected two-lane build by silently dropping
the reach lane or changing the frozen Request. The builder refuses a single-lane command when a
`-reach` candidate set exists next to its input. Collector buckets have no size between thirty days
and a year, so a full yearly reach lane cannot fit an explicit intermediate window.

## The Gate tables

`discover` then projects the complete relevance-lane union once and prints its numbered table
(`project_relevance_gate_input.py … --output trend-relevance-gate-input.json --table`). With two
lanes, the reach lane's table comes from a command of its own — one table per command, because two
full tables in one output overflow the tool output limit: score the relevance table and chain
`drive.py gate`, which builds the relevance Gate and prints the reach table (into
`trend-relevance-gate-input-reach.json`).

After printing a table, the driver prints `gate_scoring` and `next_stage` for this run. Follow their
exact `scores_file`, ordered `columns`, `header: false` and next-stage command after reading all
pages. The column names describe the format; never put a header in the TSV.

**Read the whole table before scoring.** A small table arrives in the numbered format and is
complete. A large table arrives as a JSON page with `table_chunk`, `table_page`, `total_pages` and
`next`. From the run directory, run `next.script` by its absolute path in the skill directory with
exactly `next.arguments`; the `--input` path is relative to the run directory (the read-only
command is `cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/project_relevance_gate_input.py' --input <this run's projection JSON> --table --page N`).
Concatenate all `table_chunk` values in page order without separators; a long candidate can continue
across pages, including its sponsorship disclosures. Only `next: null` marks the last page; it does
not prove earlier pages were complete. If any shell or tool layer reports `truncated` or inserts a
truncation marker, that page is unread: read the same read-only `--input` page completely in this
same run. Do not score or rank an incomplete table, and do not re-run discovery, recollect or inspect
host-specific files to complete it. Page reads never change the projection JSON.

## Scoring

Write one line per numbered item into the scores file (`trend-relevance-gate.tsv`, and
`trend-relevance-gate-reach.tsv` for the reach table) with a shell heredoc —
`<n> <topic_relevance> <locale_fit> <creator_transferability>`, or `<n> <topic_relevance> <locale_fit>`
when the table's first line carries neither `subject.anchor_handle` nor `subject.anchor_mechanisms` —
every number exactly once, integers 0–100, nothing else on the line. Read each line and judge it;
never produce scores with code, keyword or regular-expression rules, or any script — a Gate computed
that way is a failed Gate even when it validates. Chain the next stage onto the heredoc —
`drive.py rank` with one lane, `drive.py gate` after the relevance table with two lanes, and
`drive.py rank` after the reach table. The stage first builds the Gate JSON the later steps read
(`build_relevance_gate.py --input … --scores … --output trend-relevance-gate[-reach].json`). It
refuses a missing, duplicated or out-of-range line; fix the lines it names and run the same stage
again.

- `topic_relevance`: whether the item reproduces one of `subject.anchor_mechanisms`. **Score the
  mechanism, not the subject matter and not the language.** A same-subject item that does not run the
  mechanism is not same-format; an item that runs the mechanism on a neighbouring subject inside the
  same craft still is, because the Creator adapts moves, not topics. Score in three bands and do not
  subdivide **within** a band — the ranking consumes the band, not the number, so a 97 and an 88 are
  the same verdict, and picking numbers inside a band used to manufacture rank changes between
  identical runs.
  - **85–100** — same format: it reproduces a listed mechanism, whatever subject it applies the
    mechanism to and whatever language it is written in. Subject distance belongs in
    `creator_transferability` and language in `locale_fit`; deducting for either here charges the
    item twice.
  - **55–84** — same niche, different mechanism: adjacent education, software technique, workflow
    walkthrough, process showcase or personal narrative that does not run the anchor's move.
  - **0–54** — neither the mechanism nor the niche: incidental lexical overlap, generic
    lifestyle/event content, or another task entirely.
- A caption too short to state the move is not evidence against it. Captions carry the move only
  when the Creator chooses to spell it out, so judge on what is present: `matched_queries` records
  the literal wording that surfaced the item, and hashtags name the craft. A terse caption pairing an
  enumeration or choose-one prompt with a craft hashtag, surfaced by a query that names the
  mechanism, is same-format evidence; scoring it low because the caption is thin discards the
  mechanism the retrieval already found.
- `creator_transferability`: whether its visible move can credibly be adapted by this Creator.
  **Omit this column entirely when the Gate input carries neither `subject.anchor_handle` nor
  `subject.anchor_mechanisms`.** With no anchor there is nothing to judge adaptability against, and
  an invented number is indistinguishable from a real verdict at a quarter of the ranking weight.
  Omitting it is a supported state: the contract accepts its absence and the ranking redistributes
  its weight proportionally across `topic_relevance` and `locale_fit`. Never write a placeholder such
  as 0 or 50 in its place, and never omit it when an anchor is present.
- `locale_fit`: fit to the requested language, culture and target market; it is not geographic
  popularity. When the user named no market or content
  language, the language they write in sets only the reply language: do not score an item down for
  being in another language.

Two promotional caps override the bands, with one exception:

- An item whose main purpose is selling the poster's own services or soliciting clients scores at
  most 40 on topic relevance **when that offer is not the requested mechanism** — generic coaching,
  agency retainers, "comment LINK for my course", or selling unrelated goods. **Do not apply the 40
  cap** when the poster is demonstrating or selling a product that *is* the requested mechanism (for
  example, the niche is a kind of software tool and the Reel demos such a tool doing that job): that
  is same-format 85–100, even if the Reel is a pitch.
- An item that demonstrates a mechanism under an explicit commercial partnership — a sponsor tag, a
  branded hashtag or a gifted-tool credit — scores at most 75, because the move is bound to one
  vendor and transfers less cleanly.

Do not output direct/adjacent labels, reasons, popularity judgments, metrics or route labels. Freeze
the Gate before detail.

## Score-line preflight

Before any Gate write or new fetch, the driver may return `status: needs_correction` with exit 0 for
a wrong single-lane `gate` command or invalid authored score lines in an open, unfrozen run. This is
unfinished work, not success: correct only those lines and follow the returned exact command in the
same run. It neither changes scores nor fetches data. Frozen Gates, missing or corrupt source files,
data faults and internal errors remain fatal; never reopen or replace a failed run, and never use a
correction to change a frozen Gate.
