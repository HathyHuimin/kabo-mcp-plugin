## What changed

<!-- One or two sentences. List each Skill you touched. Write in English (AGENTS.md). -->

## Upstream source (when syncing from creator-skills)

<!-- creator-skills PR and merge commit, upstream versions, and any upstream commits you deliberately skipped and why. Delete this section otherwise. -->

## Risk per Skill

| Skill | Level | Why |
|---|---|---|
| | R0 / R1 / R2 / R3 | |

- **R0**: no behaviour change (comments, tests, docs, dead code).
- **R1**: pure refactor; CLI and outputs byte-for-byte unchanged.
- **R2**: changes rendering, validation, or the commands and steps SKILL.md asks for.
- **R3**: changes `static_host.py`, `persist_*.py`, `contracts/directory-mcp/` pins, tool bindings or the package build. Link an explicit owner approval.

## Evidence before merge

- [ ] `npm run verify` (paste the pass/fail line)
- [ ] `npm run skills:lint` shows no new error or warning for the touched Skills
- [ ] Upstream tests for the change, pointed at the plugin package: fail before, pass after (when syncing)
- [ ] Language handling changed: scripts run on Chinese and English input

## Real-host check (R2 and above)

<!-- Local Codex with this branch installed against https://kabo.sh/mcp-pub: the queries run, the Skill each one activated, the artifacts and whether validation passed. Say what is not covered yet. -->
