# Reusing a finished standard review

Read this only when a finished `review-creator-account` review of this same platform, handle and
original question may exist. Without one, use SKILL.md step 1's `fetch_catalog.py` path in this
same run instead.

## Which review counts

Start from the finished `account_analysis.json` **and the complete validated report** that
`review-creator-account` produced: `<standard run>/account_analysis.json`,
`<standard run>/report/ACCOUNT_REVIEW.md` (`output/` for a review that wrote there) and the
original question in `<standard run>/question.txt`. `<standard run>` is that review's run
directory.

Use only a standard run whose final step printed `creator_report: <path>` in this conversation
(`drive.py report` when it said the report is final, otherwise `drive.py verify-report`): the
report file exists before its validators run, so its presence alone proves nothing. An earlier
session, a host transcript or session log, or the creator saying the review is done does not
count, and you must not read host records to find one. If you cannot see that line in this
conversation, or the review is for a different account or platform, there is no standard review
to reuse: fetch the catalog with `fetch_catalog.py` in this run. Never run `review-creator-account`
or its scripts from this Skill.

## How to reuse it

- Use those exact files as data; do not copy, rebuild or search other runs for them. In every
  later command, `<analysis>` is `<standard run>/account_analysis.json`.
- Read the complete report before expanding its findings, freeze the original question, and keep
  the standard report's evidence limits and source window.
- Reused files are reuse of validated inputs, not a fresh fetch or watch. Do not also run
  `fetch_catalog.py` in this run.
