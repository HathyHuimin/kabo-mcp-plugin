#!/usr/bin/env python3
"""Turn a validated rule check into the Creator-facing report.

The shape is deliberate. Each rumour keeps the creator's own wording, then gets the platform's
words underneath it with a link and a date, so the reader can check the claim instead of trusting
the reader. Anything with no published rule goes in its own section at the end, once, under a
heading that says so - never interleaved with quotes, because a mechanism explanation that sits
next to a quotation borrows its authority.

What does not appear: source ids, registry mechanics, file names, run ids, retrieval methods. A
creator asking whether a rumour is true is owed the answer and the link, not the plumbing.
"""

from __future__ import annotations

import argparse
import json
import sys

# Fixed labels are English for every delivery language; the model-written strings (statement,
# scope, inference, gloss, reasons) stay in the creator's language.
EN = {
    "title": "{platform} rule check",
    "checked": "Checked {date}, against the {region} version of these pages",
    "checked_unknown": ("Checked {date}. Region was not supplied. This check is limited to the "
                        "pages and versions actually read; regional applicability was not verified."),
    "findings": "What each claim turned out to be",
    "verdict": {
        "confirmed": "true",
        "refuted": "not true",
        "partly_true": "half right",
        "no_official_text": "not found in the pages read",
        "not_checked": "could not be checked",
    },
    "official_says": "In the platform's words",
    "scope": "Specifically: ",
    "not_checked_section": "What this check could not reach",
    "not_checked_lead": ("No page that would answer the following could be read this time - not "
                         "read and found silent, but not read at all. So these are neither "
                         "confirmed nor refuted. What follows is a read of the mechanism, not a "
                         "platform statement, and it is not evidence that no such rule exists."),
    "no_text_section": "What the pages read did not cover",
    "no_text_lead": ("The official pages read in this check did not directly answer the following; "
                     "this does not prove that the platform has never published a relevant rule. "
                     "What follows is a tentative mechanism interpretation, not a platform statement "
                     "or an established basis for action."),
    "gloss": "Translation: ",
    "unreachable": "Pages this check could not read",
    "unreachable_lead": ("These were meant to be read and were not. That is not evidence they are "
                         "unchanged; it only means nobody looked:"),
    "closing_unsourced": ("This check quoted no official text supporting or refuting these claims. "
                          "That does not establish a platform rule or imply that every page was unread. "
                          "Any page-access failures are listed separately above."),
    "closing": ("Quoted evidence above carries its source and the date it was read. Platforms change "
                "rules without changing the page, and not always the page you would expect, so "
                "this is worth re-running rather than treating as settled."),
    "authority": {
        "official_help_page": "official help page",
        "official_secondary": "official announcement or account",
        "creator_reported": "reported by creators",
        "unofficial": "unofficial",
    },
    "effective": "in force from {date}",
}

PLATFORM = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok", "other": "", "bilibili": "Bilibili", "twitch": "Twitch"}


def render(value: dict) -> str:
    words = EN
    platform = PLATFORM.get(value["platform"], "") or value["platform"]

    lines = [f"# {words['title'].format(platform=platform)}", ""]
    if value["region"] == "UNKNOWN":
        lines += [words["checked_unknown"].format(date=value["checked_at"][:10]), ""]
    else:
        lines += [words["checked"].format(date=value["checked_at"][:10],
                                          region=value["region"]), ""]

    settled = [c for c in value["claims"]
               if c["verdict"] not in ("no_official_text", "not_checked")]
    unwritten = [c for c in value["claims"] if c["verdict"] == "no_official_text"]
    unreached = [c for c in value["claims"] if c["verdict"] == "not_checked"]

    if settled:
        lines += [f"## {words['findings']}", ""]
        for claim in settled:
            mark = words["verdict"][claim["verdict"]]
            lines += [f"### {claim['statement']}", "", f"**{mark}**", ""]
            # A half-right rumour used to render as a verdict word and a block of the platform's
            # own language, leaving the reader to work out which half applied to them.
            if claim.get("scope"):
                lines += [f"{words['scope']}{claim['scope'].strip()}", ""]
            lines.append(f"{words['official_says']}:")
            lines.append("")
            for item in claim["evidence"]:
                authority = words["authority"].get(item["source_authority"],
                                                   item["source_authority"])
                effective = ""
                if item.get("effective_from"):
                    effective = ", " + words["effective"].format(
                        date=item["effective_from"])
                # Thresholds are published as bulleted lists. Prefixing every line keeps the
                # quote a real substring of the page; flattening it to one line produced text that
                # no longer appeared anywhere in the document being cited.
                for row in item["quote"].strip().splitlines():
                    lines.append(f"> {row}".rstrip())
                # The platform writes in its own language and the quote has to stay in it - that is
                # what makes the line checkable against the page. The translation goes underneath,
                # visibly this Skill's rather than the platform's, so a reader who cannot read the
                # original still gets the substance without being handed a paraphrase as evidence.
                if item.get("gloss"):
                    lines.append(">")
                    # The gloss needs the same per-line treatment as the quote above it, and for a
                    # reason this Skill's own rules guarantee: thresholds are published as lists,
                    # quotes keep their line breaks, and a report in another language than the page
                    # owes every quote a gloss. So multi-line glosses are the common case, not the edge one - and a
                    # single append dropped every continuation line outside the blockquote.
                    gloss_lines = item["gloss"].strip().splitlines()
                    # A quote that opens with a list marker would otherwise render as
                    # "> Translation: - first item", gluing the label onto the bullet. Give the
                    # label its own line whenever the translation is itself a list or spans lines.
                    if len(gloss_lines) > 1 or gloss_lines[0].lstrip().startswith(("-", "*", "1.")):
                        lines.append(f"> {words['gloss'].rstrip()}".rstrip())
                        for row in gloss_lines:
                            lines.append(f"> {row}".rstrip())
                    else:
                        lines.append(f"> {words['gloss']}{gloss_lines[0]}".rstrip())
                lines.append("")
                lines.append(f"— [{item['url']}]({item['url']}) · {authority} · "
                             f"{item['retrieved_at'][:10]}{effective}")
                lines.append("")

    if unwritten:
        lines += [f"## {words['no_text_section']}", "", words["no_text_lead"], ""]
        for claim in unwritten:
            lines += [f"### {claim['statement']}", "", claim["inference"].strip(), ""]

    # Kept apart from "nothing official exists" on purpose. Printing a run that could not look
    # under a heading that says the platform published nothing is the one sentence this Skill must
    # never put in front of a creator - it is a fetch failure promoted to a finding.
    if unreached:
        lines += [f"## {words['not_checked_section']}", "", words["not_checked_lead"], ""]
        for claim in unreached:
            lines += [f"### {claim['statement']}", "", claim["inference"].strip(), ""]

    unreachable = value.get("unreachable_sources") or []
    if unreachable:
        lines += [f"## {words['unreachable']}", "", words["unreachable_lead"], ""]
        for item in unreachable:
            where = f" — {item['url']}" if item.get("url") else ""
            lines.append(f"- {item['reason']}{where}")
        lines.append("")

    # The closing line used to promise a source and a date on every line above it, and printed
    # unchanged on a report whose claims carried neither.
    quoted_anything = any(c.get("evidence") for c in value["claims"])
    lines += [words["closing"] if quoted_anything else words["closing_unsourced"], ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Render a rule check as Creator-facing Markdown.")
    parser.add_argument("document")
    # Optional and informational: any BCP-47 tag is accepted and the fixed labels stay English.
    parser.add_argument("--language")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv[1:])

    with open(args.document, encoding="utf-8") as handle:
        value = json.load(handle)
    text = render(value)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(json.dumps({"status": "ok", "path": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
