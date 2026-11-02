# Discovery Gate and Profile Eligibility

Read this whole file before writing `competitor-discovery-gate.tsv`. It covers the two
account-relationship judgments: the discovery Gate (before `select`) and Profile Eligibility
(before `content`). Apply the channel-specific Gate instead of forcing one discovery formula
across both channels.

## Instagram Gate: scores

Score every numbered author in the printed table exactly once. Write one line per author into
`competitor-discovery-gate.tsv` — `<n> <category_fit> <audience_job_fit> <sustained_focus>
<locale_fit>`, integers 0–100, nothing else on the line — and chain `drive.py select`; it builds
the Gate JSON and refuses a missing, duplicated or out-of-range line, so fix the lines it names
and run it again.

- **Read each author's line and judge it; never produce scores with code, keyword or
  regular-expression rules, or any script** — a Gate computed that way is a failed Gate even when
  it validates.
- Read the whole line, not only the captions: an account's rows are the evidence for
  `sustained_focus` and for whether it really makes the subject's kind of content, and two
  accounts whose captions read alike are usually told apart by their dates, counts and lanes.
- A row a Popular lane returned from outside the window (`window` is not `within_window`) is an
  old post, not current output; a profile-search row is a bio, not a post, and an account with a
  handful of followers and no posts is not a competitor whatever its bio says.
- Search and Popular rows remain complementary recall evidence until account selection; do not
  compare missing Search plays with missing Popular dates, and never let a big play or like count
  raise `category_fit` on its own — counts say how a post travelled, not what the account
  supplies.
- `category_fit` measures whether the account visibly offers the same product/service category,
  not whether one post mentions the same workflow. `audience_job_fit` measures the buyer job
  independently, `sustained_focus` uses all returned visible content, and `locale_fit` cannot
  rescue weak product fit.
- Do not output direct/adjacent labels, reasons, popularity judgments, metrics or route fields.

**Read `subject.kind` first and score against the matching definition of "same category" — the
two are not interchangeable.**

- **`creator`:** the subject's category *is* making that content, so educators, media and
  tutorial accounts publishing the same kind of content **are** the same category and must be
  scored on the merits; do not deduct for being an educator when that is exactly what the subject
  is. `category_fit` asks whether the account makes the same content form — the form
  `subject.description`, `subject.caption_phrases` and, when the run started from one video,
  `subject.seed_caption` describe — and an account on the same topic in a different form does not
  get a high `sustained_focus`.
- **`product`:** the category is the *offer*, so scoring flips: an account earns a product-level
  `category_fit` only when its own visible evidence shows it markets a comparable offering that a
  buyer weighing the subject would also evaluate — its own product, pricing, signup or demo.
  Accounts that only produce content *about* that category cap at 40 no matter how on-topic they
  read: educators and tutorial accounts, news and roundup accounts, shoutout and promo accounts,
  agencies serving the category, and the official account of the platform or channel the product
  runs on. On-topic is not the test; being an alternative purchase is. This half is easy to skip
  because every one of those accounts truthfully matches the subject's market, audience and topic.
  Measured on one product subject, 31 of 58 candidates came back with byte-identical scores, so
  the ordering fell through to retrieval support and rewarded whichever accounts posted the most
  generic category content.
- Under either reading, an account whose visible evidence shows only affiliate promotion,
  customer testimonials, or generic agency work outside the subject's own category must not
  receive product-level category scores.
- Skipping the subject read is not a small mis-weighting: measured on one educator subject, the
  unconditional exclusion drove the gold accounts' median `category_fit` to 30–78 across four runs
  of the same model on the same pool, with zero gold in the delivered six on one real run, while
  the subject-relative read held the same accounts at 88–91 and lifted top-ten gold from 3 to 4–6
  on both models.

**What `select` does with the scores** (`evidence-score-v3`): it deterministically keeps strong
product-category candidates ahead of workflow-only fallbacks, then chooses up to six profile
targets plus two reserves by 75% semantic fit and 25% comparable retrieval support. When the
Request names a market, that six-seat budget is filled from the complementary profile-search lane
first, then leftover seats from Search/Popular (measured: local product accounts entered
the pool via profile search but lost the six seats to English Search/Popular agencies because
retrieval rewarded more posts). Caption phrases do not flip this lane: profile search on a form
phrase often returns accounts named after the phrase. Profile-search authors are merged into the
Candidate Set *before* the Gate, and if any of them is missing from the scored Gate, selection
fails closed — score them; never silently reject them and fill six seats from Search/Popular. The
lower tier exists only to fill unused profile capacity and the frozen reserve budget; Profile
Eligibility still makes the final direct/adjacent/media/excluded decision.

## YouTube Gate: relationship labels

Classify every numbered channel in the printed table exactly once as direct, adjacent or reject:
one line per channel in `competitor-discovery-gate.tsv`, `<n> d`, `<n> a` or `<n> r`, nothing
else on the line. `select` builds `competitor-discovery-gate.v2` from it, refuses a missing or
repeated number, and runs the Selector with the Request's `competitor_selection.strategy`
`comparable-v2` (a Request value, not a command-line option: run `drive.py select` exactly as
shown and never add `--strategy`).

Apply the same request-relative rule as eligibility: a channel that matches the core comparable
axis but misses one or more stated account-selection constraints is adjacent, not direct. YouTube
topical search is rich in tutorial publishers whose content text is highly similar to the subject
even when the account is not a product competitor; this first-pass account-relationship judgment
keeps those publishers from displacing direct product accounts before Profile retrieval.

## Both channels

Retrieval support uses only cross-lane-comparable intent coverage, Search/Popular route coverage,
matching-content recurrence and provider rank; it never treats a missing Search play count as zero
or lets popularity upgrade semantic relevance. Preserve lightweight public author fields such as
display name, verification and follower count when the provider returned them; the selector uses
them only to resolve identity and profile priority, and in your reasoning they are evidence about
the account, never a score of their own. Never hand-pick or silently omit a returned author.
Completeness here means the Gate covers every author in the persisted Candidate Set; it does not
erase the discovery-coverage limitation.

`select` then resolves the six selected public profiles together with each account's first history
page in one `collect_account_history` call, which replaces every unavailable profile one-for-one
from the two frozen reserves, and prints the eligibility projection and its schema.

## Profile Eligibility

Read only that projection and write `competitor-eligibility.json` (`schema_version`
`competitor-eligibility.v2`, see `references/competitor-eligibility.schema.json`), placing every handle present in `profiles[]`
exactly once in `direct_refs`, `adjacent_refs`, `media_refs` or `excluded_refs`. Each Profile
projection contains its public bio/category fields plus the visible discovery content for that same
account, so eligibility is not decided from a generic bio alone.

- Never classify a failed or absent Profile. The finalizer records that acquisition failure
  separately: an automatic discovery choice can be replaced by a frozen reserve; a requested
  account cannot.
- Do not generate `eligible`, per-account reasons or content references; eligibility is derived
  from the relationship and the downstream selector does not consume those fields. Leave out
  `schema_version`, `request_id` and any empty `*_refs` list; `content` fills them.
- Classify using the account-selection constraints this Request actually stated — audience, offer
  or content form, and any other attribute the user named — plus sustained topical focus. Direct is
  the fullest match to that stated set, not to a global category and not to a preset checklist:
  do not enumerate possible filters, and do not invent a constraint the Request did not state. An
  account that matches the core comparable axis but misses one or more stated constraints is
  `adjacent`, not `direct` and not `excluded`. A research window, report language or discovery
  budget is not an account-selection constraint.
- A buyer of this subject would evaluate a direct account in the same purchase; a viewer would keep
  watching a direct creator for the same work. Do not change `locale_fit` weights to compensate.
- When `subject.kind` is `product`, the official account of the platform or channel the product
  runs on is not a purchase alternative: put it in `media_refs`, not `direct_refs`, unless public
  evidence shows buyers compare purchases against that official offering. Pure education and
  pure-affiliate accounts follow the same default. A rival product's own official account remains
  eligible as `direct`.
- Visible discovery content may disambiguate what the account actually publishes, but one popular
  post alone never proves competitor status.
- The subject account, regional accounts of the same brand, employees, customers, affiliates and
  UGC collaborators are excluded.
- If fewer than three direct/adjacent accounts remain and a frozen reserve is still unused,
  `content` resolves that one reserve, prints its projection and stops: classify only that newly
  added handle, leave every other entry as it is, and run `content` again without `--placed`: the
  reserve's calls were already placed. There is no second reserve round.

## What `content` does with eligibility

`content` runs the finalizer, which chooses at most six content accounts with direct accounts
first, adjacent accounts second and at most one media reference. The finalizer follows the
evidence-backed eligibility roles only; a handle or an "official" name never overrides them. On a
product question the platform or channel the product runs on is a `media` reference by the rule
above, so the one-account media cap keeps it from taking a slot a buyer alternative should have.
Explicitly requested eligible accounts are pinned and may exceed the automatic six-account or media
quota, up to the Request limit.

Only `competitor-account-selection.json.included_handles` may proceed to content retrieval. Do not
hand-write that file; the Composer re-runs finalize and fails if the roster does not match. Keep
Candidate, discovery selection, eligibility, final account selection and profile artifacts as audit
artifacts; never pass them to the public-content Snapshot Assembler. `content` then retrieves, in one
`collect_account_history` call, the Reels history of each included account that still owes pages
within the page budget, assembles and analyzes the
public-content Snapshot once, and prints the synthesis schema first, then the topic coverage and
the synthesis evidence.
