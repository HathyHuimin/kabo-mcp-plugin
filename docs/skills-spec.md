# Kabo Skills Specification v1

Scope: this repository's `skills/` directory. These skills are a fixed subset adapted from
creator-skills and shipped with the plugin. The Skills used by Kabo's web agent and their
distribution mechanism are outside this specification's scope.

This specification builds on the [Agent Skills open specification](https://agentskills.io/specification)
and adds only what Kabo needs. It does not repeat the open specification's rules for frontmatter
and directory structure.

Validation: `npm run skills:lint`. Rule IDs correspond directly to `scripts/skills-lint.mjs`.

## Default assumptions

A skill should run with any model or host (harness) that supports skills and MCP, such as Codex,
Claude Code or Cursor. Skills must not be tied directly to a particular host or model. ChatGPT
has no shell, so skills that depend on scripts cannot run there; it is not currently a target host.

All skill content (`SKILL.md`, `references/` and script output) must be in English. This document
is intended for contributors and is also maintained in English.

## Severity and enforcement

| Severity | Meaning | Blocks `npm run verify`? |
| --- | --- | --- |
| Error | Violates the specification | No (enforcement is disabled; see below) |
| Warning | A change is recommended | No |
| Audit | Requires manual confirmation, item by item during PR review | No |

**Enforcement remains disabled**: keep `enforced` in `skills-spec.json` empty (decision made on
2026-10-20). `verify` only prints a summary for each skill; specification issues are identified
through PR review and `npm run skills:lint`. The scripts still support enabling enforcement per
skill. Whether to enable it, and for which skills, is the user's decision; do not enable it
incidentally in an adaptation PR.

Until enforcement is enabled, existing skills only produce summaries in `verify`. Run
`npm run skills:lint` for full details, or use `--strict` to check as though every skill were enforced.

## Rules

### KS1 Frontmatter (aligned with the open specification)

These rules align with the official validator, `skills-ref` 0.1.1 (`agentskills validate`).
This repository implements them in Node, so CI does not need Python for this validation.

- **KS1.1**: Frontmatter must be valid YAML. `name` must be at most 64 characters, contain only
  lowercase letters, digits and hyphens, neither start nor end with a hyphen, contain no consecutive
  hyphens, and match the directory name.
  - Example: an unquoted description containing `: ` is parsed as a mapping and is therefore invalid.
- **KS1.2**: `description` must be nonempty and at most 1,024 characters.
  - More than 500 characters produces a warning. Codex's skill list can use at most 2% of the context,
    shared by all installed skills. Shorten descriptions first when this budget is exceeded.
  - The description should state only what the skill does and when to use it. Execution rules belong
    in the body. This distinction requires manual review.
- **KS1.3**: Frontmatter may use only specification fields: `name`, `description`, `license`,
  `compatibility`, `metadata` and `allowed-tools`.
- **KS1.4**: `compatibility` must be a string of at most 500 characters. `metadata` must map strings
  to strings.
  - If the source business version is recorded, put it in `metadata.version`. Versioning and release
    rules are not yet covered by this specification.
  - Put the license in `license`; it must match `plugin.json` (enforced by the package check).
    The skills CLI copies only an individual Skill directory, without the repository-root LICENSE,
    so the license declaration must be distributed with the Skill.

### KS2 Dependencies and host independence

- **KS2.1**: `SKILL.md` and text files under `references/` must not refer to host-private runtimes.
  - This includes `kabo-run-pipeline`, `PLUGIN_ROOT` / `CLAUDE_PLUGIN_ROOT`, and host-added tool
    namespace names such as `mcp__*`.
  - Always use the tool names exposed on `/mcp-pub`.
- **KS2.2**: Runtime tool dependencies must come from `/mcp-pub`. List every required tool name in
  `allowed-tools`, separated by spaces.
  - Every entry must appear in `contracts/directory-mcp/tools-list.json`.
  - Every `/mcp-pub` tool mentioned in `SKILL.md` or `references/` must be listed in `allowed-tools`.
  - The abandoned registry tools `list_research_skills` / `get_research_skill` were removed from
    `/mcp-pub` on 2026-10-22 and are absent from the tool list, so they fail the preceding rule.
  - Note: `allowed-tools` is experimental in the open specification. Some hosts treat it as a list
    of preauthorized tools. In Kabo, it is a dependency list. Each host's actual behavior must be
    tested individually.
  - Tool names from the old `/mcp` endpoint are outside this specification's scope; the old endpoint
    remains available in production.
- **KS2.3** (warning): List each dependency beyond the default assumptions in `compatibility`.
  - For example, running `scripts/` requires Python 3 and file read/write access; analyzing local
    video requires ffmpeg.
  - If a host lacks a dependency, the skill must state which step it cannot perform, without
    pretending that the step is complete.
  - Keep local-file features (unpublished videos and local subtitles), declaring their dependencies
    under this rule.
  - Skills that depend on `/mcp-pub` tools must state the required Kabo MCP connection:
    `https://kabo.sh/mcp-pub`.
  - State the Kabo OAuth permissions needed by `allowed-tools`, using the format
    `Kabo permission: data.` For optional permissions, state their purpose and trigger; for example,
    use `workspace:write` only when the user asks to save or link something. Permissions are derived
    from the tool list's `securitySchemes`; lint produces warnings. These are permissions Kabo issues
    to the host's connection, not permissions on the host machine. Hosts authorize connections,
    not individual skills, so this declaration is how users can see each skill's requirements.
  - If scripts download signed URLs returned by Kabo tools, declare that the scripts need access to
    `storage.googleapis.com` (Codex's default sandbox may block it). Host export plus `stage` is a
    fallback, not a replacement for this declaration.
- **KS2.4** (manual): Do not branch by host in the body, use execution concepts such as `subagent` /
  `inline`, or divide responsibilities between a "main agent" and an "executor".
  - For long-running skills, state at the start of the body: execution in a foreground subagent is
    recommended, and the main thread must wait for its final result before replying. Do not run it
    in the background and promise delivery later. Noninteractive hosts such as `claude -p` exit after
    the main thread replies, terminating background subagents and losing the report (observed with
    Claude Code 2.1.308 on 2026-10-22). If the host does not support foreground subagents, follow the
    same steps in the main thread.
- **KS2.5** (warning): Chinese characters in the body produce a warning.

### KS3 Length

The entire `SKILL.md` body is loaded when the skill is activated. After context compaction,
Claude Code reattaches only the first 5,000 tokens of each skill.

- **KS3.1**: The `SKILL.md` body must not exceed 500 lines or 21,000 characters (approximately
  5,250 tokens in English).
  - The character limit increased from 20,000 to 21,000 on 2026-10-29. The first 5,000 tokens reattached
    after compaction cover roughly the first 20,000 characters. The remaining tail may not survive
    compaction, so critical rules must still appear early, as required by KS3.3.
- **KS3.2**: Each text file under `references/` must not exceed 500 lines or 20,000 characters,
  except for `*.schema.json`.
  - Data files read only by scripts are not exempt. If a file is too long, first remove fields unused
    by both the scripts and the body; do not create an exception for it.
- **KS3.3** (manual): Put critical rules near the start of the body. Move branching workflows,
  detailed validation rules and error handling to `references/`, keeping only the main workflow
  and pointers in the body.

Length is measured in characters, not tokens. Models use different tokenizers; measuring in tokens
would tie the rules to a particular model, violating the default assumptions.

### KS4 No nesting

- **KS4.1**: A skill directory may contain only one `SKILL.md`, at its root.
  - Do not package subskills included in third-party material, such as OpenMontage's directors
    and pipelines.
- **KS4.2**: Do not read another skill's files or invoke another skill as a step of this skill.
  - At the end, you may suggest which skill the user could use next. Give its name, not its path.
  - Distribute shared code as copies in each skill's own `scripts/` directory.
  - A script mentioning another skill's name is only an audit item; for example, a shared script
    may distinguish platforms by skill name.

### KS5 Licenses and third-party content

- **KS5.1**: If `license` is present, it must be on the allowlist: `MIT`, `Apache-2.0`, `BSD-2-Clause`,
  `BSD-3-Clause`, `ISC`, `0BSD`, `CC0-1.0` or `CC-BY-4.0`. An omitted license indicates Kabo-owned content.
- **KS5.2**: `LICENSE` / `COPYING` / `NOTICE` files and SPDX identifiers within a skill must not name
  licenses outside the allowlist, including GPL, AGPL, LGPL and NC licenses.
  - Every SPDX identifier must be on the allowlist. A license file that cannot be recognized as an
    allowed license becomes an audit item for manual confirmation.
  - Use the allowlist rather than a "prohibits commercial use" test: AGPL-3.0 permits commercial use
    but has copyleft obligations, including source disclosure when providing a service over a network.
- **KS5.3** (manual): "Third-party" includes libraries, corpora, sample data and external programs.
  PRs introducing third-party content must identify its source and license. Files adapted from
  content under a restrictive license may also be derivative works; rewrite them from our requirements.

### KS6 Nonstandard files

- **KS6.1**: Do not include `manifest.json`. Move or remove its fields as follows:

  | Original field | Destination |
  | --- | --- |
  | `version` | `metadata.version` |
  | `required.tools` / `required.connectors` | `allowed-tools` |
  | `permissions` | `compatibility` |
  | License | `license` |
  | Product configuration | Owned by the consumer; keep it outside the skill |
  | `execution`, `pipeline`, `min_plugin_version`, etc. | Remove |

- **KS6.2**: Do not include `references/mcp-bindings.json`. Replace logical connector-to-tool mappings
  with declarations in `allowed-tools`, and use `/mcp-pub` tool names directly in scripts and the body.
- **KS6.3**: Do not include an `agents/` directory, including Codex's `agents/openai.yaml`.
  - When omitted, `policy.allow_implicit_invocation` defaults to `true`, matching the plugin's needs.
  - MCP entries in `dependencies` declare the same `kabo` connection as the plugin's `mcp.json`,
    duplicating that declaration.
  - Users installing a skill separately from the plugin find the required MCP URL in `compatibility`,
    as required by KS2.3.
  - The only tradeoff is that the Codex UI uses the skill's own name as its display name.

### KS7 Scripts

Place scripts in `scripts/`. There are no strict general constraints, but scripts are audited.

- **KS7.1** (error): Do not read host session records or internal state.
  - This includes `CODEX_HOME`, `.codex/sessions`, rollout files, `.claude/projects`, desktop hosts'
    userData and host skill caches.
  - If a host deliberately hides `CODEX_HOME` from its command interface, scripts must not bypass it.
- **KS7.2** (audit): Do not install software at runtime. Tell users about missing dependencies
  and state the limitations honestly, as required by KS2.3.
- **KS7.3** (audit): Network access is limited to Kabo's own domains and signed download URLs on
  `storage.googleapis.com` returned by Kabo tools (GET only, exact hostname match, no redirects).
  - The latter is a server-side design decision: GCS signed URLs need no additional IAM setup and
    reuse the signing approach already in production. Routing downloads through a Kabo domain would
    require new keys and gateway changes, with the sole benefit of keeping network access on Kabo domains.
  - Network access occurs in only two places, both in each skill's `scripts/static_host.py`:
    - `fetch --input`: read signed URLs only from tool results (a file or stdin, never argv). Match
      the scheme and hostname exactly, treat 3xx responses as failures, cap reads at the declared
      `bytes` and verify sha256 while streaming. Write nothing if verification fails. Errors and
      output must not repeat URLs, signatures or object names.
    - `fetch --id` and the handoff resume option `--id NN=<id>`: GET
      `https://kabo.sh/v1/research-downloads/<id>` using the result ID (kabo ADR-0084, 2026-11-04).
      Match the hostname exactly and do not follow redirects. Require `Content-Length` (within the
      size limit) and `X-Content-SHA256`; write only when both the byte count and sha256 match,
      otherwise write nothing. The ID and signed URL represent the same 10-minute bearer ticket.
      The ID may appear in argv and printed resume commands. During handoff, the model passes only
      the ID, not the signed URL. If a full URL is pasted accidentally, the script extracts only
      its ID and does not use that URL for the request.
  - When a URL expires (`expires_at` has passed, or GCS returns 400/401/403), `fetch` does not retry
    the download; it only prints the next step. For synchronous data results, call
    `get_research_data_download` with the envelope's `evidence_ref` to obtain a new signed URL.
    Apply the same validation to the new URL, using the newly returned sha256 / bytes. Only if
    re-signing itself fails (`not_found` / `service_unavailable`) should the original tool be called
    again with `"delivery": "download"` (which reruns the operation). Re-signing does not introduce
    another network access path: it still uses a signed URL returned by a Kabo tool.
  - Lint flags network-library usage for audit but does not check hostnames. PR review must verify
    that there is no network access beyond the two paths above.
- **KS7.4** (audit): Keep executable scripts under `scripts/`.

### KS8 Data ingestion (guidance, not validated by scripts)

Scripts need complete data to calculate metrics and bind evidence by sha256. Do not have the model
transcribe tool results into files: results passing through the model's context can be truncated.
There has been a case where saving a result containing 100 posts left `rows` empty.

- **Primary path: download delivery**: Pass `delivery: "download"` when calling data tools,
  `get_research_data_job` and `read_research_data_artifacts`. Results provide `download`
  (`status / kind / url / expires_at / sha256 / bytes / content_type`); synchronous envelopes omit
  `data`, and each artifact provides `artifacts[i].download`. The model saves only this small,
  complete result and runs `scripts/static_host.py fetch --tool <tool-name> --input <file>`.
  The script downloads the content, checks its size and sha256, and uses the same validation as
  `stage` to write it to `snapshot/.host-evidence/`. URLs expire after 10 minutes, so fetch immediately.
  - Why this is the primary path: large results are truncated when they pass through the model's
    context, and hosts vary in their ability to export results. Download delivery sends the complete
    bytes through scripts, bypassing the model; validation also happens in scripts.
  - This result contains only links and digests, not the data. Saving it verbatim through the model
    (via host export or a heredoc) is not evidence transcription: a single incorrect character fails
    hostname or sha256 validation, preventing incorrect data from being saved.
  - **Result IDs (preferred since 2026-11-04)**: Do not have the model save even this small result.
    The 22-character ID in the envelope download URL's `research-export/v1/<id>.json` path is the
    result ID (kabo ADR-0084, 2026-11-04). The model passes only that ID to the script, either through
    the driver's handoff resume command `--id NN=<id>` or through
    `static_host.py fetch --tool <tool-name> --id <id>`. The script retrieves the same bytes from
    `https://kabo.sh/v1/research-downloads/<id>`, validates them against the response headers
    `Content-Length` and `X-Content-SHA256`, and saves them through the usual `fetch` path.
    Rationale: in the 1.0.10 Codex regression tests, having the model save this small result was still
    the leading cause of handoff failures: malformed patches, omitted parts of signed URLs, rewritten
    `calls[].tool` values and incorrect directories. Writing files programmatically from returned
    values succeeded in 37/40 cases; model transcription succeeded in 3/10. The plugin registers no
    lifecycle hooks, and installations containing only Skills have no hooks either, so having the host
    persist results on the model's behalf is not an available path. Results without a download URL
    (download unavailable or delivery not requested) must still be saved as files as described above.
    Artifacts (frames and transcripts) have no result IDs and are downloaded individually as below.
  - **Frames and images also use downloads**: Pass `delivery: "download"` to
    `read_research_data_artifacts` when reading frames. `fetch` saves images in the same form as
    `stage`: `<id>-<i>.<jpg|png|webp>` plus `.meta`. Its output includes each absolute `path`.
    The model must then open the file using the host's own file-reading / image-viewing tool before
    claiming to have seen it. Rationale: Claude Code 2.1.308 stores inline images separately in its
    own session directory. Exportable tool results omit those images, so `stage` cannot obtain the
    complete result. In end-to-end tests on 2026-10-22, this failed five consecutive times, after
    which the model reconstructed results by sha256 from the host's session directory. That directory
    is internal to the host and must not be relied on. Codex 0.163 retains images in tool results,
    so inline delivery plus `stage` remains usable there and is retained as a fallback.
    - Download validation: accept only `https://storage.googleapis.com/`, do not follow redirects,
      cap reads at the declared bytes, and verify sha256 while streaming. Accept only a closed set
      of types (`image/jpeg|png|webp`, `text/vtt`, `application/json`), matching the artifact record.
      If the response includes `Content-Type`, it must also match. A missing header does not block
      acceptance because size and sha256 already pin the bytes. If any check fails, write nothing
      for the entire operation.
    - Fallback: if an item's `download` is unavailable, record `not_found` as a gap; for other reasons,
      read it again inline. If the host blocks script network access, also reread those `object_refs`
      without `delivery`, then save through host export plus `stage`. The body must explicitly forbid
      reconstructing results from the host's own session or tool-results directories.
  - Calls handed to the model by driver scripts (`public_calls`): data tools, `get_research_data_job`,
    `read_research_data_artifacts` and the four aggregation tools (`collect_*` / `enrich_*`) all receive
    `delivery: "download"`. Readiness queries have no such parameter and remain unchanged.
  - **Aggregation-tool downloads are copies only**: `items` is required in their published output
    schemas, so the server keeps complete inline items even when a download is ready. The model still
    reads inline items to select candidates; scripts use the download copy to obtain complete data.
    The downloaded bytes contain the entire output with `download` removed. `fetch` identifies the
    shape by `schema_version`; `stage` also accepts it and removes the `download` field containing
    signed URLs before saving.
  - **Composite fetch tools (`kabo-composite-fetch.v1`, KS9)**: `collect_account_history`,
    `collect_tiktok_video_candidates`, `collect_youtube_recent_videos` and `render_official_pages`
    each complete a sequence of fetch rounds in one call. Their output is
    `{schema_version, tool, status, calls:[{tool, parameters, group, result}], summary, coverage, limitations}`.
    Each `calls[i].result` is identical to an individual data tool's result envelope.
    `stage` / `fetch --tool <composite-tool>` (whose downloaded bytes contain the entire output with
    `download` removed) splits `calls[]` into individual entries. Each entry uses the same validation
    as single-tool `stage`, according to `calls[i].tool`, and is saved as the same `<id>.json` plus
    `<id>.meta`. The content is byte-for-byte identical to individually staged output, so downstream
    parsing is unchanged. Sidecars additionally record `composite_tool`, `composite_id`,
    `composite_index`, `composite_group` and `composite_parameters`. An overall record is then written
    as `<composite_id>.json` plus `.meta`; its sidecar uses the composite tool as `tool` and
    `kabo-composite-fetch.record.v1` as `schema_version`. The record contains
    `status / summary / coverage / limitations` and `calls[{index, tool, group, status, evidence_id}]`
    in call order. Drivers read it through `static_host.composite_records(tool)`; each entry includes
    the evidence file's absolute `path`. Reject the entire result and write nothing if any entry
    fails validation, any called tool is not a data tool declared by this Skill, or the composite
    tool is not one of the four listed above. Staging the same composite again deduplicates both
    individual entries and the overall record by sha256, referring back to the previous files.
  - Expired URLs: calling the same read-only tool (`get_research_data_job` /
    `read_research_data_artifacts`) again issues a new signed URL. For synchronous data tools, call
    `get_research_data_download` with the envelope's `evidence_ref`; this neither reruns the operation
    nor consumes quota. The new URL's sha256 and bytes may differ from the old values; validate only
    against the newly returned values. If re-signing itself fails, fall back to calling the original
    tool again with `"delivery": "download"`. This reruns the operation and is the last resort before
    losing results already paid for. Business-level lookup tools (`lookup_*`) are an exception:
    re-signing returns the original envelope recorded by the answer route, not its projection,
    so the lookup itself must be called again. Aggregation tools already return complete inline
    results; use export plus `stage` instead of calling them again.
- **Fallback: host export plus `stage`**: When `fetch` reports that no download is ready (the server
  returns `unavailable` and falls back to inline delivery, an older server is in use, or delivery is
  inline), save the complete result through the host's result-export capability and
  `scripts/static_host.py stage`. Keep this path: whether Codex's default sandbox permits script
  network access requires testing in the actual host. `stage` does not accept artifacts in download
  mode; the fallback is to reread them without `delivery` and then use `stage`, as described above.
- **Idempotency keys for deferred media calls**: Generate `public_video_*` parameters with
  `static_host.py job-call`, including a top-level `idempotency_key` in the form
  `<skill>:<run-directory-name>:<tool-name>:<parameter-digest>:a<attempt-number>`. If a request must be
  resent because no response was received, reuse the key unchanged; the server returns the original
  job without charging again. Use the next attempt number after receiving an error result, changing
  parameters, or choosing to rerun a terminal job. Frame extraction calls for `analyze-content-video`
  and `review-creator-account-deep` are capped at 60 frames (the server limit is 120). If omitted,
  `job-call` adds `max_frames: 60`, reducing sampling density only after the opening and preserving
  the opening's density. Explicit values above 60 are rejected. Planned values for the deep review
  are defined in `plan_media_jobs.py`.
- **Bounded waiting (implemented)**: Deferred tasks first return a job_id, then wait through
  `get_research_data_job` with `wait_s` (at most 30 seconds). Do not add server-side waiting to the
  initial call (decision made on 2026-10-20). Do not resubmit queued tasks.
- **Long-running tasks**: The p90 for video analysis and collection / enrichment is 60–100 seconds.
  Skills must not assume tools return synchronously.

### KS9 Complete fetch orchestration in the backend (hard CI gate)

One backend endpoint must complete a sequence of fetch rounds (search → filter → fetch history →
fetch details). The skill only directs that one call, and all hosts call the same endpoint.
Use `collect_*_trend_candidates` / `enrich_*_shortlist` as references.

- **KS9.1** (error): Do not add handoff rounds of "driver prints `call:` → host model calls each tool →
  save results → resume". Each round adds two or three model calls. A competitor workflow takes
  14–16 steps, exceeding the Web Agent's 12-step limit per turn and also slowing the plugin.
  Existing drivers are legacy implementations awaiting migration. `scripts/fetch-orchestration.test.mjs`
  records the round count per skill; counts may only decrease.
- **KS9.2** (error): Scripts must not detect a host-specific fetch path (connector-outbox, session
  thread IDs or turn tokens). Branching by host hides platform differences in scripts and causes
  behavior to diverge across hosts. The same test records the existing files; their number may
  only decrease.
- **KS9.3** (manual): If the logic between fetch rounds is deterministic (scoring, thresholds or
  selecting the top N), move those rules into the backend too. If model judgment is needed at only
  one or two points, the workflow may split at those points, allowing the host model to decide before
  making another backend call. If there are many judgment points, extract the guidance into a
  separate prompt; the backend calls a model at fixed nodes without relying on the host model.
  Analysis and writing after fetching remain the host model's responsibility.

## Not yet covered

- Versioning and release rules
- The full security boundaries of scripts
- Standard wording for honestly reporting gaps
- Acceptance across models
