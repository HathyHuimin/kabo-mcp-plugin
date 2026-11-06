# Brand kit

A `brand_kit` is the one-page media kit, press kit or brand kit a creator sends to brands for outreach.
Write `brand_kit/brand_kit.json` against `references/creator-brand-kit.schema.json`. It is one task
with several sections: identity, collaboration formats, representative works, numbers, contact, what
brands look at on a profile, and what is still missing. Do not split it into separate runs or pull in
a second Skill to assemble it. Resolving the profile is step 1 below, so a kit needs no account review
first; run one only when the creator also asks for a review.

To check the kit before `finish`, run:

```sh
cd '<run directory>/brand_kit' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/validate_brand_options.py' kit brand_kit.json
```

## 1. Resolve before you fill

With a named account, check readiness once with `get_research_capabilities` (or reuse a supplied
readiness note), then make one `collect_account_history` call per platform, with its fields at the top
level and `"delivery": "download"`. It resolves the profile and lists the latest posts in one backend
run; never call the per-account tools one by one:

```json
{"platform": "<instagram|youtube|tiktok>", "accounts": [{"handle": "<handle>"}], "profile": true, "history": {"max_items": 12}, "on_identity_miss": "candidates", "delivery": "download"}
```

(A YouTube channel id goes in `channel_id` instead of `handle`; several accounts on one platform go in
the same `accounts` list.) Fetch it at once by its result id, the 22 characters between
`research-export/v1/` and `.json` in its download url (ids expire in 10 minutes), and place every
envelope into the task's `snapshot/`. Never retype returned data or download the link yourself:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' fetch --tool collect_account_history --id=<result id>
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/place_composite.py' accounts --dir brand_kit/snapshot
```

Anything else (no ready download, an expired id, a queued job, a refused result) follows
`references/data-calls.md`. If the account still cannot be retrieved, deliver the `checklist` kit and
say the account data was not retrieved; never present it as a kit built from the account.

Write `brand_kit.json` with what you author, then fill each resolved account from its placed
envelopes, once per account, giving `--envelope` for every file `place_composite.py` placed for it:

```sh
cd '<run directory>/brand_kit' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/project_brand_kit_facts.py' --platform <platform> --handle '<verified handle>' --envelope snapshot/<file> ... [--highlight <post link>] --into brand_kit.json
```

It writes the account's `resolved_public` provenance row, the counts the profile shows with its link
and retrieval date, and the latest posts; never type those yourself. An account the creator named but
you did not resolve is yours to record as `user_supplied`; the validator rejects a number or a work on
an account with no provenance row.

When the call reports the account as `needs_identity` (the handle has no public record at the source,
so the backend searched it once and returned candidates), filling the kit from it would produce a page
of blanks. Name the candidates in `missing` as the account to confirm, and keep `mode` on what the
brief alone supports. Never pick a candidate yourself: a near-miss handle carries someone else's
numbers.

## 2. Every number carries its source

Followers, views and the like go in `metrics`, each with `source`. A `resolved_public` number comes
from the projection above, with the `source_url` it was read from and `retrieved_at`; a number the
creator gave is `user_supplied`, and you write it. A number neither the creator gave nor a resolved
profile showed is left out and named in `missing`; the projection names a count the profile hid. Do
not estimate audience demographics, reach or engagement a public profile does not show.

## 3. Highlights are labelled as highlights

The projection lists the latest posts as `recent_sample` (unpicked). A post the creator picked because
it did well goes in with `--highlight <its link>` and is labelled `curated_highlight`. A curated
highlight never stands in for typical performance, and the rendered kit says so.

## 4. Contact comes from the creator

Put a contact route in `contact` only when the creator gave it; otherwise `null`, and list it in
`missing`. Never look one up.

## 5. No price in the kit

Rates are a separate conversation: `rate_card` points to `price-creator-brand-deals`, and no text in
the kit carries a figure; the validator rejects any currency amount. If the creator asks for a rate
card, that is a pricing run, not this task.

## 6. Official fields apart from suggestions

In `profile_checklist`, an item the platform's own creator marketplace or help page names is
`official` with its `source_url`; everything else is `inferred`. They print as separate lists.

## 7. No account is still a delivery

Without an account, set `mode` to `checklist`: identity from the brief, collaboration formats, and the
profile checklist of what brands look at; no metrics, works or provenance. That answers "what does a
brand look at on my profile" in full; it is not a degraded kit.
