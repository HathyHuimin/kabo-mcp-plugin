# Report language and display translations

Read this whole before writing `idea_set.json`.

## Choosing the language

Set `report_language` on both IdeaRequest and IdeaSet to the language requested for the final
report and interaction (BCP 47 tag). This is separate from `language_targets`, which describes the
creative content. Infer the report language from the current user request unless explicitly
specified earlier; preserve quotations, titles and supplied creative text verbatim. Generate all
explanatory fields, questions and rationale in that report language, so the whole report reads in
the user's language. The renderer and validator share the locale contract, including missing-input
and selection actions.

Chinese omission is supported only for legacy requests; new non-Chinese requests must set the
field. Built-in UI catalogs cover zh/en/fr/ar and their regional variants. Any other valid language
tag, including a regional one, uses authored UI label projections instead: run
`python3 '<skill directory>/scripts/report_language.py' <report-language> --labels` to list the
English label sources, and add one `evidence_gap_translations` entry per listed source, preserving
Markdown markers and boundary spacing. Missing labels fail validation; never silently substitute
another language. If a reliable translation cannot be provided, state that limitation.

## `evidence_gap_translations`

For every explicit `report_language` (including Chinese), retain original
`evidence_summary.data_gaps` for provenance and supply an `evidence_gap_translations` entry
(`source_text`, `report_text`) for every gap. When the source already matches the report language,
explicitly repeat it as `report_text`; otherwise translate every substantive constraint, including
whether it describes the old source or the current run, without softening a limitation. Missing
mappings fail validation rather than leaking the source language. The renderer and validator
project these entries natively. These projections never alter user-supplied creative text or
canonical evidence identifiers.

In that same `evidence_gap_translations` array, also supply one entry for each distinct canonical
value actually displayed: `status`, `task_intent`, `source_mode`, non-`unknown` effort when no
minute estimate is present, and each nonempty `primary_evidence_ref`. Translate these values for
presentation only; never edit the canonical enums, evidence IDs or lineage.

For a language without a built-in catalog, the same array also carries the required UI labels
above. Extra translations of schema-defined display enums are allowed but remain unused unless
that value is actually displayed. They never add evidence, gaps or creative copy. Arbitrary
unreferenced text or evidence IDs, duplicate sources, empty translations and missing required
mappings still fail validation. A source present in multiple sets has exactly one entry.
Do not add a new output field or turn a display value into a source gap. When source text already
matches the report language, repeat it explicitly. The renderer projects only real source gaps into
the gap section and only the finite canonical values into their display positions, never into
creative copy or raw audit lineage.

Keep the existing IdeaSet schema unchanged so downstream consumers can read the artifact. Legacy
Chinese IdeaSets without `report_language` retain their old projection; old explicit-locale
IdeaSets require these mappings when explicitly revalidated by this version. This does not rewrite
prior reports or change request/upstream inputs. Review translation meaning and the final report:
coverage validation cannot prove translation semantics.
