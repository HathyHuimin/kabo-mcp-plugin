# Brand strategy brief

Write `brand_strategy_brief/strategy_brief.json` against
`references/creator-brand-strategy-brief.schema.json`. `finish` validates and renders it; to check it
earlier, run:

```sh
cd '<run directory>/brand_strategy_brief' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/validate_brand_options.py' brief strategy_brief.json
```

The brief and the handoff are separate promises:

1. **The brief is always deliverable.** A creator with no account still gets positioning, audience, pillars, formats, exclusions and tone. Never hold the brief back for a handle it does not need.
2. Everything the brief had to guess goes in `assumptions`, in the creator's own terms. An assumption presented as settled is the failure here.
3. **The handoff carries handles, so it is earned, not assumed.** Set `handoff.status` to `ready` only when every account came from the creator or from a public profile you actually resolved, and record each one in `accounts_provenance` with that source. The validator rejects a handle in `creator_profile.accounts` that has no provenance row.
4. With no account, set `handoff.status` to `blocked`, `creator_profile` to `null`, and say in `blocked_reason` what is missing. That is a complete run, not a degraded one.
5. When ready, `handoff.creator_profile` is a `creator-profile.v3` object the topic Skill consumes directly through its `upstream_sources[]`. Keep it byte-valid for that contract: it takes exactly `schema_version`, `profile_id`, `accounts`, and optionally `markets`, `audience`, `positioning`, `content_profile`, `key_missing_info`, `extensions` - nothing else. Tone and exclusions have no field there; carry them in `extensions`.
6. Every `assumptions` entry must also appear in `creator_profile.key_missing_info`, or the topic Skill reads a guess as an account fact.
