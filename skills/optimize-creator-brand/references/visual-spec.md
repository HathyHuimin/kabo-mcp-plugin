# Visual identity spec

Write `visual_identity_spec/visual_spec.json` against `references/creator-brand-visual-spec.schema.json`.
Colour roles, type choices and per-surface direction are yours to write; only the measurements are
bound to sources. `finish` checks every official quote against the page placed in this task's
`snapshot/`. To check the spec earlier, run:

```sh
cd '<run directory>/visual_identity_spec' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/validate_brand_options.py' spec visual_spec.json --snapshot-dir snapshot
```

## Sizes are read, not remembered

1. `references/sources/visual-spec-sources.v1.json` lists the pages this Skill may take a size from.
   A size question is answered from that list or it is not answered as official. Check `verified_at`
   against `staleness_budget_days` and say so when an entry is past it.
2. Read every page you need through **one** `render_official_pages` call, whatever its
   `retrieval.method` (`js_rendered` pages return an empty shell to a direct read, and a rendered page
   is the only copy every host can read). Give its fields at the top level, each page's `url` and
   `source_id` from the registry, and `"delivery": "download"`:

   ```json
   {"pages": [{"url": "<registry url>", "source_id": "<registry source_id>"}], "delivery": "download"}
   ```

   Fetch it at once by its result id (the 22 characters between `research-export/v1/` and `.json` in
   its download url; ids expire in 10 minutes) and place each page as
   `snapshot/<source_id>.envelope.json`. Never retype a page or download the link yourself:

   ```sh
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' fetch --tool render_official_pages --id=<result id>
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/place_composite.py' pages --dir visual_identity_spec/snapshot
   ```

   Read the placed page files to find the sentences you quote. A page the placement skips (failed or
   empty) is not an unchanged page: that size is not answered as official. A rendered copy may print
   thousands separators (`2,560`) where the plain page does not; match on digits. Anything else follows
   `references/data-calls.md`.
3. Quote the platform's own sentence verbatim into `quote`, with `source_url` and the `retrieved_at`
   date. An official measurement without its sentence and date is a remembered number wearing a
   citation. For a `js_rendered` source, `--snapshot-dir` holds the quote, `source_url` and `retrieved_at`
   to that placed page: copy its characters, line breaks included.
4. Where the registry's `does_not_carry` records that a platform publishes no size for a surface, list
   that surface in `not_published` and give the figure as `derived_working_size` with a `basis` that
   names how it was arrived at. **Do not fill that hole with a number that reads official**: the
   validator rejects an official claim for a surface recorded as not published. Each `not_published`
   entry needs its own `measurements` entry with the same `surface` text:
   `{"surface": <same>, "claim": <working size>, "authority": "derived_working_size", "basis": <how it was arrived at>, "source_id": null, "source_url": null, "retrieved_at": null, "quote": null}`.
5. A figure taken from anywhere that is not the platform is `unofficial`, and its `basis` names the
   source. Say whose figure it is in the delivery; never present it as the platform's.

Do not render, generate or download images. This Skill writes the spec; producing the artwork is not
its job.
