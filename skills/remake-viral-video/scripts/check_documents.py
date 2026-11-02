#!/usr/bin/env python3
"""Check the four script-gate documents before render_remake_video; no network or model loop.

  check        validate script/*.json against the saved brief and print errors and warnings
  copy-drafts  approved-script jobs: write script/*.json from the runner's drafts unchanged, then check
  render-args  run the same check and, when it passes, write render/render_remake_video.json
               (voice-over: --preview writes the preview call, --beat-review the picture review)

These are structural checks only. The server runs the full script, opening and decisions checks
again before anything is generated or charged, and its message is the one that counts.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
DOCUMENTS = {"script": "script/script.json", "opening_review": "script/opening_review.json",
             "decisions": "script/decisions.json", "route_b_jobs": "script/route_b_jobs.json"}
MAX_DOCUMENT_BYTES = 256 * 1024
SLOTS = ("opening", "author_claim", "summary", "cta")
FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'd|i'll|me|my|mine|we|we're|we've|us|our|ours)\b", re.IGNORECASE)
SUMMARY_BASIS = ("creator_quote", "persona")
CTA_BASIS = ("creator_habit", "platform_generic", "user_given")
CITED_BASIS = ("creator_quote", "creator_habit")
UNCITED_BASIS = ("persona", "platform_generic")
# 依据名与 inferred 不是 ref；ref 只能是下列取值或简报里的素材条目 id。
REF_KINDS = ("viral_line", "viral_frame", "scene", "inference")
NOT_REFS = ("persona", "platform_generic", "creator_quote", "creator_habit", "user_given", "inferred")
HEDGE = re.compile(r"^\s*(i think|my guess|probably|maybe)\b", re.I)
DROP_SLOTS = ("author_claim", "opening", "cta")
INHERIT_ROLES = ("hook", "payoff", "cutaway", "setup_change", "on_screen_text", "scene_object")
INHERIT_DECISIONS = ("keep", "adapt", "drop")
GRAPHIC_FORMS = ("emoji_icon", "image", "text", "mixed")
CHECK_STATUSES = ("pass", "fail", "unavailable", "not_applicable")
VERDICTS = ("ready", "revise", "limited")
REQUIRED_CHOICES = ("summary_source", "cta", "author_claims")
CHOICE_BY = ("default", "agent", "user")
QUESTION_KINDS = ("ambiguous_goal", "user_fact", "value_tradeoff", "spend")
# 与 runner 拒收的写法一致（prompts.py）：发型须按身份参考图保持，不得美颜，不得换成虚构的素色衣服。
HAIR_HOLD = "not straightened, not smoothed, not tidied"
BEAUTIFYING = ("softly lit skin", "soft skin", "smooth skin", "flawless", "glowing skin", "porcelain",
               "airbrushed", "retouched skin", "glamour", "beauty lighting", "salon")
INVENTED_OUTFIT = ("unbranded", "free of lettering and illustrations", "plain solid", "solid, unbranded")
UNSEEN_HAIR = re.compile(r"\b(wavy|curly|curls|bangs|fringe)\b", re.I)
FRAMING_WORDS = re.compile(r"\b(close-up|closeup|medium shot|wide shot|waist-up|chest-up|knees-up|full-body|"
                           r"shot size|head and shoulders)\b", re.I)
SIZE_AT_SPOT = re.compile(r"(right|correct|matching) (size|scale) (and|&) perspective|size and perspective for", re.I)
BEAT = re.compile(r"\bb(\d+)(?:\s*[-–]\s*b?(\d+))?", re.I)
# 旁白（runner V2-r63 decisions.check_pictures 同口径）
MAKE_KINDS = ("composition", "generated", "motion_graphic")
COMPOSITION_MAX_CHARS = 24_000
IMAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}\.png$")


def text(value):
    return isinstance(value, str) and bool(value.strip())


def todo(value):
    return isinstance(value, str) and value.strip().upper().startswith("TODO")


def load(path, errors, label):
    try:
        raw = path.read_bytes()
    except OSError:
        errors.append(f"{label}: missing file {path}")
        return None
    if label in DOCUMENTS and len(raw) > MAX_DOCUMENT_BYTES:
        errors.append(f"{label}: {len(raw)} bytes, over the 256 KiB limit of render_remake_video")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        errors.append(f"{label}: not valid JSON ({error})")
        return None


def strings(value, where=""):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, f"{where}.{key}" if where else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from strings(item, f"{where}[{index}]")


def no_todo(label, document, errors):
    for where, value in strings(document):
        if todo(value):
            errors.append(f"{label}.{where} still holds the draft's TODO")


def no_escaped_breaks(label, document, errors):
    # A backslash followed by n in the decoded text is drawn literally on screen.
    for where, value in strings(document):
        if "\\n" in value:
            errors.append(f"{label}.{where} contains a backslash followed by n; write a real line break (\\n inside the JSON string), not an escaped one (\\\\n)")


def words_of(sentence):
    return re.findall(r"[a-z0-9']+", sentence.lower())


def dropped_words(original, fixed):
    # 校对只改听错的词：原句里的词在改正句里找不到相近的词（含相邻两词拼起来，如 selfcommunicate -> sell, communicate）就算被删了。
    have = words_of(fixed)
    near = have + [a + b for a, b in zip(have, have[1:])]
    return [w for w in words_of(original)
            if not any(difflib.SequenceMatcher(None, w, n).ratio() >= 0.75 for n in near)]


def beats_of(role):
    out = set()
    for match in BEAT.finditer(str(role or "")):
        start = int(match[1])
        end = int(match[2]) if match[2] else start
        out.update(range(min(start, end), max(start, end) + 1))
    return out


def normal(value):
    return " ".join(re.sub(r"[^\w\s]", " ", str(value).lower()).split())


def check_claim(prefix, line, claim, errors):
    if not isinstance(claim, dict) or not text(claim.get("phrase")):
        errors.append(f"{prefix}: each claim needs a non-empty phrase")
        return
    if claim["phrase"] not in str(line.get("text", "")):
        errors.append(f"{prefix}: phrase {claim['phrase']!r} is not a substring of the line; cut it from the text")
    has_ref, has_src = "ref" in claim, "src" in claim
    if has_ref == has_src:
        errors.append(f"{prefix}: a claim is {{phrase, ref}} or {{phrase, src}}, exactly one of the two")
    if has_ref:
        ref = claim["ref"]
        if not text(ref):
            errors.append(f"{prefix}: ref must not be null or empty")
        elif ref in NOT_REFS:
            errors.append(f"{prefix}: {ref!r} is a basis, not a ref; a persona or platform_generic line has claims []")
        elif ref == "inference" and not (line.get("origin") == "inferred" and HEDGE.match(str(line.get("text", "")))):
            errors.append(f"{prefix}: ref 'inference' needs origin 'inferred' and a line starting with "
                          "'I think', 'My guess', 'Probably' or 'Maybe'")
    if has_src:
        src = claim["src"]
        if (not isinstance(src, dict) or not text(src.get("video")) or src.get("field") != "transcript" or
                not isinstance(src.get("t0"), (int, float)) or not isinstance(src.get("t1"), (int, float)) or
                not text(src.get("quote"))):
            errors.append(f"{prefix}: src must be {{video, field: 'transcript', t0, t1, quote}} from the creator lines in the brief")


def cites_creator(line):
    return any(isinstance(c, dict) and isinstance(c.get("src"), dict) for c in line.get("claims") or [])


def check_script(script, brief, errors, warnings):
    if not isinstance(script, dict):
        errors.append("script must be a JSON object")
        return
    english = str(script.get("language") or "en").lower().startswith("en")
    no_todo("script", script, errors)
    no_escaped_breaks("script", script, errors)
    reference = brief.get("reference") if isinstance(brief, dict) else {}
    reference = reference if isinstance(reference, dict) else {}
    sentences = reference.get("sentences") if isinstance(reference.get("sentences"), list) else []
    # 读音校对：顶层 proofread，fixes 的键是 reference.sentences 的下标，值是整句改正后的文字。
    proofread = script.get("proofread")
    if not isinstance(proofread, dict) or not isinstance(proofread.get("fixes"), dict) or not text(proofread.get("note")):
        errors.append("script.proofread must be {\"fixes\": {...}, \"note\": \"...\"}; use empty fixes when nothing was misheard")
    else:
        for key, value in proofread["fixes"].items():
            if not str(key).isdigit() or not int(key) < len(sentences):
                errors.append(f"script.proofread.fixes key {key!r} is not an index into reference.sentences")
            if not text(value):
                errors.append(f"script.proofread.fixes[{key}] must be the whole corrected sentence")
            elif str(key).isdigit() and int(key) < len(sentences):
                original = sentences[int(key)]
                original = original.get("text") if isinstance(original, dict) else original
                for word in dropped_words(str(original or ""), value):
                    warnings.append(f"script.proofread.fixes[{key}] drops {word!r}; a proofread fix only corrects misheard "
                                    "words, so keep every other word of the reference sentence")
    lines = script.get("lines")
    if not isinstance(lines, list) or not lines:
        errors.append("script.lines must list the lines")
        return
    kept, slots = [], {slot: [] for slot in SLOTS}
    for index, line in enumerate(lines):
        prefix = f"script.lines[{index}]"
        if not isinstance(line, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not text(line.get("text")):
            errors.append(f"{prefix}: text is empty; delete the line instead of emptying it")
        if not text(line.get("viral_role")) or not beats_of(line.get("viral_role")):
            errors.append(f"{prefix}: viral_role must name the beat(s) it carries, for example 'b3' or 'b3-b5'")
        claims = line.get("claims", [])
        if not isinstance(claims, list):
            errors.append(f"{prefix}: claims must be a list")
            claims = []
        for k, claim in enumerate(claims):
            check_claim(f"{prefix}.claims[{k}]", line, claim, errors)
        rewrite = line.get("rewrite")
        if rewrite is None:
            kept.append(index)
            if not any(isinstance(c, dict) and c.get("ref") == "viral_line" and c.get("phrase") == line.get("text")
                       for c in claims):
                errors.append(f"{prefix}: a line without rewrite is the reference's wording and carries "
                              "the claim {\"phrase\": <the whole line>, \"ref\": \"viral_line\"}")
            continue
        if not isinstance(rewrite, dict) or rewrite.get("slot") not in SLOTS:
            errors.append(f"{prefix}: rewrite.slot must be one of {', '.join(SLOTS)}")
            continue
        slot = rewrite["slot"]
        slots[slot].append(index)
        if not text(rewrite.get("why")):
            errors.append(f"{prefix}: rewrite.why is required")
        if slot != "summary" and not text(rewrite.get("replaces")):
            errors.append(f"{prefix}: rewrite.replaces must quote the reference words this {slot} line takes the place of")
        elif slot == "author_claim" and english and not FIRST_PERSON.search(rewrite["replaces"]):
            warnings.append(f"{prefix}: the replaced words have no I/me/my/we/our; author_claim covers only the original "
                            "author speaking about themselves, so keep any other reference line word for word")
        basis = rewrite.get("basis")
        if not text(basis):
            errors.append(f"{prefix}: rewrite.basis is required")
        elif slot == "summary" and basis not in SUMMARY_BASIS:
            errors.append(f"{prefix}: a summary's basis is creator_quote or persona")
        elif slot == "cta" and basis not in CTA_BASIS:
            errors.append(f"{prefix}: a cta's basis is creator_habit, platform_generic or user_given")
        if any(isinstance(c, dict) and c.get("ref") == "viral_line" for c in claims):
            errors.append(f"{prefix}: a rewritten line is not tagged viral_line")
        if (basis in CITED_BASIS or slot == "author_claim") and not cites_creator(line):
            errors.append(f"{prefix}: a {basis if basis in CITED_BASIS else 'author_claim'} line cites the creator's "
                          "own words with a claim carrying src; if nothing can be cited, use persona or "
                          "platform_generic, or move the author's line to route_b.dropped")
        if basis in UNCITED_BASIS and claims:
            errors.append(f"{prefix}: a line resting on {basis} has \"claims\": []")
    others = [i for i in range(len(lines)) if i not in slots["opening"]]
    if slots["opening"] and others and max(slots["opening"]) > min(others):
        errors.append("script: the opening line(s) come first")
    if len(slots["summary"]) > 1:
        errors.append("script: one summary at most")
    last_kept = max(kept) if kept else -1
    for slot in ("summary", "cta"):
        if any(i < last_kept for i in slots[slot]):
            errors.append(f"script: the {slot} comes after every kept reference line")
    if slots["summary"] and slots["cta"] and slots["summary"][0] > min(slots["cta"]):
        errors.append("script: the summary comes before the cta")
    route_b = script.get("route_b")
    if not isinstance(route_b, dict):
        errors.append("script.route_b must stay an object (it switches the route-B rules on)")
        return
    dropped = route_b.get("dropped", [])
    dropped_beats = set()
    if not isinstance(dropped, list):
        errors.append("script.route_b.dropped must be a list")
        dropped = []
    for k, row in enumerate(dropped):
        if (not isinstance(row, dict) or not text(row.get("text")) or row.get("slot") not in DROP_SLOTS or
                not text(row.get("why"))):
            errors.append(f"script.route_b.dropped[{k}] must be {{text, slot: author_claim|opening|cta, why}}")
        elif isinstance(row.get("beat"), int) and not isinstance(row.get("beat"), bool):
            dropped_beats.add(row["beat"])
    beats = reference.get("beats") if isinstance(reference.get("beats"), list) else []
    covered = set().union(*(beats_of(line.get("viral_role")) for line in lines if isinstance(line, dict)))
    for beat in beats:
        index = beat.get("i") if isinstance(beat, dict) else None
        if isinstance(index, int) and index not in covered and index not in dropped_beats:
            errors.append(f"script: beat b{index} has no line; add it to a neighbouring line's viral_role, "
                          "or list the deleted line in route_b.dropped with its beat")
    check_inherit(route_b.get("inherit"), reference, errors)
    check_overlays(script.get("overlays", []), len(lines), errors)
    # 保留行须能在参考转写里按原文找到；精确的顺序与 ASR 容差由服务端检查。
    fixes = proofread.get("fixes") if isinstance(proofread, dict) and isinstance(proofread.get("fixes"), dict) else {}
    corrected = [str(fixes.get(str(i), s.get("text") if isinstance(s, dict) else s)) for i, s in enumerate(sentences)]
    transcript = normal(" ".join(corrected))
    for index in kept:
        if transcript and normal(lines[index].get("text", "")) not in transcript:
            warnings.append(f"script.lines[{index}] is kept as the reference's wording but was not found word for word in "
                            "reference.sentences; if you changed it, give it a rewrite")


def check_inherit(inherit, reference, errors):
    if not isinstance(inherit, list):
        errors.append("script.route_b.inherit must list the inherited elements")
        return
    shots, hook = set(), False
    for k, row in enumerate(inherit):
        prefix = f"script.route_b.inherit[{k}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not text(row.get("element")) or row.get("role") not in INHERIT_ROLES or not isinstance(row.get("ref"), dict):
            errors.append(f"{prefix} needs element, role ({', '.join(INHERIT_ROLES)}) and ref {{beat|shot}}")
        if row.get("decision") not in INHERIT_DECISIONS:
            errors.append(f"{prefix}: decision must be keep, adapt or drop")
        elif row["decision"] != "keep" and not text(row.get("why")):
            errors.append(f"{prefix}: an {row['decision']} needs a why")
        if row.get("role") == "on_screen_text" and (row.get("ref_form") not in GRAPHIC_FORMS or not text(row.get("layout"))):
            errors.append(f"{prefix}: on_screen_text records ref_form ({', '.join(GRAPHIC_FORMS)}) and layout")
        hook = hook or row.get("role") == "hook"
        ref = row.get("ref") if isinstance(row.get("ref"), dict) else {}
        for key in ("shot", "shots"):
            value = ref.get(key)
            shots.update(x for x in (value if isinstance(value, list) else [value]) if isinstance(x, int))
    if not hook:
        errors.append("script.route_b.inherit must record the opening's hook (role hook)")
    for shot in reference.get("shots") or []:
        if isinstance(shot, dict) and shot.get("kind") not in (None, "talking") and shot.get("i") not in shots:
            errors.append(f"script.route_b.inherit has no row for reference shot {shot.get('i')} ({shot.get('kind')})")


def check_overlays(overlays, count, errors):
    if not isinstance(overlays, list):
        errors.append("script.overlays must be a list")
        return
    for k, overlay in enumerate(overlays):
        prefix = f"script.overlays[{k}]"
        if not isinstance(overlay, dict):
            errors.append(f"{prefix} must be an object")
        elif overlay.get("kind") == "title" and not text(overlay.get("text")):
            errors.append(f"{prefix}: the title overlay carries the reference's title text, lines joined with \\n")
        elif overlay.get("kind") == "list":
            items = overlay.get("items")
            if not isinstance(items, list) or not items:
                errors.append(f"{prefix}: a list overlay needs its items")
                continue
            for n, item in enumerate(items):
                where = f"{prefix}.items[{n}]"
                if not isinstance(item, dict):
                    errors.append(f"{where} must be an object")
                    continue
                if "icon" in item and not text(item["icon"]):
                    errors.append(f"{where}: icon must be one emoji that names the spoken item")
                line = item.get("line")
                if isinstance(line, bool) or not isinstance(line, int) or not 0 <= line < count:
                    errors.append(f"{where}: line must be the index in lines of the line that starts this item")


def check_opening(review, errors):
    if not isinstance(review, dict):
        errors.append("opening_review must be a JSON object")
        return
    no_todo("opening_review", review, errors)
    if review.get("stage") != "script" or str(review.get("route", "")).upper() != "B":
        errors.append("opening_review: stage must be \"script\" and route \"B\"")
    for key in ("intent", "picture", "sound", "relationship", "creator_fit", "conditions"):
        if not text(review.get(key)):
            errors.append(f"opening_review.{key} must state your judgment")
    evidence = review.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(
            isinstance(row, dict) and text(row.get("source")) and text(row.get("observation")) for row in evidence):
        errors.append("opening_review.evidence must list rows with source and observation")
    checks = review.get("checks")
    if not isinstance(checks, dict) or not checks:
        errors.append("opening_review.checks must record at least one item by name")
        checks = {}
    for key, row in checks.items():
        if not isinstance(row, dict) or row.get("status") not in CHECK_STATUSES or not text(row.get("basis")):
            errors.append(f"opening_review.checks.{key} needs status ({', '.join(CHECK_STATUSES)}) and basis")
    candidates = review.get("candidates")
    if not isinstance(candidates, list) or not 2 <= len(candidates) <= 3:
        errors.append("opening_review.candidates must list 2-3 opening candidates")
        candidates = []
    for k, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or not text(candidate.get("first_line")) or not text(candidate.get("assessment")):
            errors.append(f"opening_review.candidates[{k}] needs first_line and assessment")
    chosen = review.get("chosen")
    if isinstance(chosen, bool) or not isinstance(chosen, int) or not 0 <= chosen < max(len(candidates), 1):
        errors.append("opening_review.chosen must be the index of the candidate you picked")
    if not text(review.get("choice_why")):
        errors.append("opening_review.choice_why must say why")
    if review.get("verdict") not in VERDICTS:
        errors.append("opening_review.verdict must be ready, revise or limited")
    elif review["verdict"] == "ready" and any(isinstance(row, dict) and row.get("status") in ("fail", "unavailable")
                                             for row in checks.values()):
        errors.append("opening_review: a ready verdict cannot have a fail or unavailable check; use revise or limited")


def check_decisions(decisions, draft, errors):
    if not isinstance(decisions, dict):
        errors.append("decisions must be a JSON object")
        return
    no_todo("decisions", decisions, errors)
    for key in (draft or {}):
        if key not in decisions:
            errors.append(f"decisions: keep the draft's field {key!r}")
    if decisions.get("status") != "pending":
        errors.append("decisions.status stays \"pending\"; the runner closes it")
    for key in ("summary", "rights"):
        if not text(decisions.get(key)):
            errors.append(f"decisions.{key} must be written")
    choices = decisions.get("choices")
    if not isinstance(choices, list):
        errors.append("decisions.choices must be a list")
        choices = []
    keys = set()
    for k, choice in enumerate(choices):
        if (not isinstance(choice, dict) or not text(choice.get("key")) or not text(choice.get("applied")) or
                choice.get("by") not in CHOICE_BY or not text(choice.get("why"))):
            errors.append(f"decisions.choices[{k}] needs key, applied, by ({', '.join(CHOICE_BY)}) and why")
        else:
            keys.add(choice["key"])
    for key in REQUIRED_CHOICES:
        if key not in keys:
            errors.append(f"decisions.choices must record {key}")
    questions = decisions.get("questions", [])
    if not isinstance(questions, list) or len(questions) > 3:
        errors.append("decisions.questions is a list of at most 3, usually empty")
    else:
        for k, question in enumerate(questions):
            if (not isinstance(question, dict) or question.get("kind") not in QUESTION_KINDS or
                    not all(text(question.get(f)) for f in ("question", "recommended", "why"))):
                errors.append(f"decisions.questions[{k}] needs kind ({', '.join(QUESTION_KINDS)}), question, recommended and why")


def check_pictures(decisions, script, errors):
    """Voice-over: the picture plan in decisions (picture_system and one make per row)."""
    if not text(decisions.get("picture_system")):
        errors.append("decisions.picture_system must be written (narration.md, the picture plan)")
    rows = decisions.get("pictures")
    if not isinstance(rows, list) or not rows:
        errors.append("decisions.pictures must list every line's picture")
        return
    count = len(script.get("lines") or []) if isinstance(script, dict) else 0
    covered = set()
    for k, row in enumerate(rows):
        prefix = f"decisions.pictures[{k}]"
        lines = row.get("lines") if isinstance(row, dict) else None
        if not isinstance(lines, list) or not lines or not all(isinstance(x, int) and not isinstance(x, bool) for x in lines):
            errors.append(f"{prefix}.lines must list the script line numbers it covers")
            continue
        covered.update(lines)
        make = row.get("make")
        if not isinstance(make, dict) or make.get("kind") not in MAKE_KINDS:
            errors.append(f"{prefix}.make.kind must be one of {', '.join(MAKE_KINDS)}")
        elif make["kind"] == "generated" and not text(make.get("prompt")):
            errors.append(f"{prefix}.make (generated) needs prompt")
        elif make["kind"] == "composition":
            markup = str(make.get("markup") or "")
            size = sum(len(str(make.get(f) or "")) for f in ("markup", "style", "script"))
            if 'class="clip' not in markup and "clip\"" not in markup:
                errors.append(f"{prefix}.make (composition) needs markup whose visible elements have class=\"clip\"")
            if "data:" in markup or "data:" in str(make.get("style") or ""):
                errors.append(f"{prefix}.make (composition) pastes an image as data:; name it in make.images instead")
            if size > COMPOSITION_MAX_CHARS:
                errors.append(f"{prefix}.make (composition) is {size} characters; keep it under {COMPOSITION_MAX_CHARS}")
            images = make.get("images") or {}
            if (not isinstance(images, dict) or len(images) > 6 or
                    any(not IMAGE_NAME.match(str(n)) or not isinstance(v, dict) or not text(v.get("prompt"))
                        for n, v in images.items())):
                errors.append(f"{prefix}.make.images maps lowercase name.png to {{prompt}}, at most 6")
    missing = [i for i in range(count) if i not in covered]
    if missing:
        errors.append(f"decisions.pictures has no row for script lines {missing}")


def narration_brief(brief):
    return isinstance(brief, dict) and (brief.get("reference") or {}).get("type") == "narration"


def script_input(brief):
    """Approved-script input: the runner's drafts already hold the creator's exact words."""
    return isinstance(brief, dict) and brief.get("input_kind") == "script"


DRAFTS = {"script": "brief/work/script.draft.json", "opening_review": "brief/work/opening_review.draft.json",
          "decisions": "brief/work/decisions.draft.json", "route_b_jobs": "brief/work/route_b/jobs.template.json"}


def spoken(script):
    lines = script.get("lines") if isinstance(script, dict) else None
    if not isinstance(lines, list):
        return None
    return " ".join(" ".join(str(line.get("text", "")).split()) for line in lines if isinstance(line, dict)).strip()


def check_script_input(run, documents, errors):
    # 稿件模式：四份文件就是 runner 的草稿；只许重新分行（与每行的表演提示），字一个不改。
    drafts = {name: load(run / path, errors, name + " draft") for name, path in DRAFTS.items()}
    for name in ("opening_review", "decisions", "route_b_jobs"):
        if documents[name] is not None and drafts[name] is not None and documents[name] != drafts[name]:
            errors.append(f"{name} must be the runner's draft unchanged in script mode; run copy-drafts again")
    script, draft = documents["script"], drafts["script"]
    if script is None or draft is None:
        return
    if not isinstance(script, dict) or {k: v for k, v in script.items() if k != "lines"} != \
            {k: v for k, v in draft.items() if k != "lines"}:
        errors.append("script: only lines may change in script mode; keep every other field of the draft")
    words, expected = spoken(script), spoken(draft)
    if words is None or words != expected:
        errors.append("script: the lines must say the approved script word for word (only where lines break may "
                      "change); do not proofread, rewrite, translate, add or drop anything")


def copy_drafts(run):
    """Write the four documents from the runner's drafts (script mode), without changing a byte."""
    errors = []
    brief = load(run / "brief/work/brief_input.json", errors, "brief_input")
    if errors or not script_input(brief):
        raise ValueError("copy-drafts is only for an approved-script job; fetch its waiting_script brief first")
    (run / "script").mkdir(exist_ok=True)
    for name, path in DRAFTS.items():
        source = run / path
        if not source.is_file():
            raise ValueError(f"The brief has no {path}; fetch the waiting_script result again")
        (run / DOCUMENTS[name]).write_bytes(source.read_bytes())
    return check(run)


def check_jobs(jobs, template, errors, warnings):
    if not isinstance(jobs, dict) or not isinstance(jobs.get("jobs"), list):
        errors.append("route_b_jobs must be the template object with its jobs list")
        return
    expected = (template or {}).get("jobs") if isinstance(template, dict) else None
    expected = expected if isinstance(expected, list) else []
    if [j.get("tag") if isinstance(j, dict) else None for j in jobs["jobs"]] != \
            [j.get("tag") if isinstance(j, dict) else None for j in expected]:
        errors.append("route_b_jobs: keep every job of the template, with the same tags in the same order")
    for job, original in zip(jobs["jobs"], expected):
        if not isinstance(job, dict) or not isinstance(original, dict):
            continue
        tag = job.get("tag")
        for key in set(job) | set(original):
            if key != "prompt" and job.get(key) != original.get(key):
                errors.append(f"route_b_jobs[{tag}].{key} must stay exactly as in the template (only prompt is yours)")
        prompt = job.get("prompt")
        if not text(prompt) or todo(prompt):
            errors.append(f"route_b_jobs[{tag}].prompt must be written")
            continue
        low = prompt.lower()
        if SIZE_AT_SPOT.search(prompt):
            errors.append(f"route_b_jobs[{tag}]: never ask for the person at the size and perspective of a place in the "
                          "plate (it pushes them far away and cancels the measured framing)")
        if not job.get("person"):
            continue
        if "hair" not in low or HAIR_HOLD not in low:
            errors.append(f"route_b_jobs[{tag}]: write the hair as 'hair exactly as in the identity references: the same "
                          f"hairstyle and the way it is worn, the same length and colour; {HAIR_HOLD}'")
        if "own outfit" not in low:
            errors.append(f"route_b_jobs[{tag}]: write the clothing as 'own outfit from the identity references'")
        for phrase in BEAUTIFYING:
            if phrase in low:
                errors.append(f"route_b_jobs[{tag}]: {phrase!r} pulls the face toward a retouched look; write 'natural unretouched look'")
        for phrase in INVENTED_OUTFIT:
            if phrase in low:
                errors.append(f"route_b_jobs[{tag}]: {phrase!r} replaces the creator's own outfit with an invented one")
        hair = sorted({m.lower() for m in UNSEEN_HAIR.findall(prompt)})
        if hair:
            errors.append(f"route_b_jobs[{tag}]: do not name hair features you have not seen ({', '.join(hair)})")
        framing = sorted({m.lower() for m in FRAMING_WORDS.findall(prompt)})
        if framing:
            warnings.append(f"route_b_jobs[{tag}]: the runner adds the measured framing; leave out {', '.join(framing)}")


def run_dir(raw):
    run = Path(raw or os.environ.get("KABO_STATIC_RUN_DIR") or Path.cwd()).resolve()
    if not (run / ".static-run.json").is_file():
        raise ValueError("Pass --run-dir (or run from inside the reserved run directory)")
    return run


def check(run):
    errors, warnings = [], []
    brief = load(run / "brief/work/brief_input.json", errors, "brief_input")
    draft = load(run / "brief/work/decisions.draft.json", errors, "decisions.draft")
    narration = narration_brief(brief)
    template_path = run / "brief/work/route_b/jobs.template.json"
    # 旁白没有口播首帧任务：route_b_jobs 交 {"jobs": []}
    template = ({"jobs": []} if narration and not template_path.exists()
                else load(template_path, errors, "jobs.template"))
    if errors:
        errors.insert(0, "the brief is not saved in this run; run static_host.py fetch on the waiting_script result first")
        return {"status": "errors", "errors": errors, "warnings": warnings}
    documents = {name: load(run / path, errors, name) for name, path in DOCUMENTS.items()}
    if script_input(brief):
        check_script_input(run, documents, errors)
        return {"status": "ready" if not errors else "errors", "errors": errors, "warnings": warnings,
                "kind": "script"}
    if documents["script"] is not None:
        check_script(documents["script"], brief, errors, warnings)
    if documents["opening_review"] is not None:
        check_opening(documents["opening_review"], errors)
    if documents["decisions"] is not None:
        check_decisions(documents["decisions"], draft, errors)
        if narration and isinstance(documents["decisions"], dict):
            check_pictures(documents["decisions"], documents["script"], errors)
    if documents["route_b_jobs"] is not None:
        check_jobs(documents["route_b_jobs"], template, errors, warnings)
    return {"status": "ready" if not errors else "errors", "errors": errors, "warnings": warnings,
            "kind": "narration" if narration else "talking"}


def offered_models(path):
    from_file = json.loads(Path(path).read_bytes())
    structured = from_file.get("structuredContent", from_file) if isinstance(from_file, dict) else {}
    if isinstance(structured, dict) and "content" in structured and "status" not in structured:
        structured = json.loads(structured["content"][0]["text"])
    # all_models 时服务端给全部档位（talking_model_choices，第一档是默认）；旧服务端只有 talking_models。
    models = None
    if isinstance(structured, dict):
        models = structured.get("talking_model_choices") or structured.get("talking_models")
    if not isinstance(models, list) or not models:
        raise ValueError("That result lists no talking models; use the waiting_script result of get_remake_video")
    return [m.get("id") for m in models if isinstance(m, dict)], structured.get("job_id")


def beat_review_text(path, script):
    """The per-line review: one row per script line, each with ok true or false (and a note when
    not ok); the MCP schema takes it as an opaque string, so it is checked here."""
    review = json.loads(Path(path).read_bytes())
    rows = review.get("lines") if isinstance(review, dict) else None
    count = len((script or {}).get("lines") or []) if isinstance(script, dict) else 0
    if not isinstance(rows, list) or not rows:
        raise ValueError("beat_review must be the template with its lines list")
    seen = set()
    for k, row in enumerate(rows):
        line = row.get("line") if isinstance(row, dict) else None
        if not isinstance(line, int) or isinstance(line, bool) or not 0 <= line < count or line in seen:
            raise ValueError(f"beat_review.lines[{k}].line must be a script line number (0..{count - 1}), each once")
        seen.add(line)
        if not isinstance(row.get("ok"), bool):
            raise ValueError(f"beat_review.lines[{k}].ok must be true or false")
        if row["ok"] is False and not text(row.get("note")):
            raise ValueError(f"beat_review.lines[{k}] is not ok: give a short note")
    missing = [i for i in range(count) if i not in seen]
    if missing:
        raise ValueError(f"beat_review has no row for script lines {missing}; review every line")
    return json.dumps(review, ensure_ascii=False)


def render_args(run, model, models_from, preview=None, beat_review=None):
    result = check(run)
    if result["status"] != "ready":
        return result
    narration = result["kind"] == "narration"
    bundle = json.loads((run / "brief/bundle.json").read_bytes())
    files = {name: (run / path).read_text(encoding="utf-8") for name, path in DOCUMENTS.items()}
    body = {"job_id": bundle["job_id"], "files": files}
    if preview or beat_review:
        if not narration:
            raise ValueError("--preview and --beat-review are for voice-over remakes only")
        if preview and beat_review:
            raise ValueError("Give --preview or --beat-review, not both")
        if preview:
            body["preview"] = preview
            target = run / "render/preview_remake_video.json"
        else:
            body["beat_review"] = beat_review_text(beat_review, json.loads(files["script"]))
            target = run / "render/review_remake_video.json"
    elif narration:
        # 旁白没有口播模型可选
        if model:
            raise ValueError("A voice-over remake has no talking model; leave out --talking-model")
        target = run / "render/render_remake_video.json"
    else:
        if not model or not models_from:
            raise ValueError("--talking-model and --models-from are required for a talking-head remake")
        offered, job_id = offered_models(models_from)
        if model not in offered:
            raise ValueError(f"--talking-model must be one of the offered ids: {', '.join(map(str, offered))}")
        if job_id and bundle.get("job_id") != job_id:
            raise ValueError("The model options come from a different job than the saved brief")
        body["talking_model"] = model
        target = run / "render/render_remake_video.json"
    target.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return {**result, "render_arguments": str(target)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check").add_argument("--run-dir")
    commands.add_parser("copy-drafts").add_argument("--run-dir")
    command = commands.add_parser("render-args")
    command.add_argument("--run-dir")
    command.add_argument("--talking-model")
    command.add_argument("--models-from")
    command.add_argument("--preview", choices=("pictures", "stills"))
    command.add_argument("--beat-review")
    args = parser.parse_args()
    run = run_dir(args.run_dir)
    if args.command == "check":
        result = check(run)
    elif args.command == "copy-drafts":
        result = copy_drafts(run)
    else:
        result = render_args(run, args.talking_model, args.models_from, args.preview, args.beat_review)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if result["status"] != "ready":
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Document check failed: " + str(error), file=sys.stderr)
        sys.exit(1)
