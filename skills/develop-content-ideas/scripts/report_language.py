"""Report UI locale, separate from verbatim creator text and content languages."""
import json
import re
from pathlib import Path

CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'references/report-locales.json').read_text(encoding='utf-8'))
LEGACY_LANGUAGES = {'zh', 'en', 'fr', 'ar'}


def report_language(data):
    """Validate a locale tag; new languages use authored UI labels, not new catalogs."""
    value = data.get('report_language')
    if value is None and 'report_language' not in data:
        targets = data.get('language_targets')
        legacy_request = (data.get('schema_version') == 'idea-request.v3-candidate2'
                          and isinstance(targets, list) and bool(targets)
                          and all(isinstance(t, str) and t.lower().replace('_', '-').split('-')[0] == 'zh' for t in targets))
        # Old IdeaSets carry no language metadata. Recognize only an already
        # Chinese report headline plus Chinese explanatory copy, never a bare
        # missing field or an English headline containing a quoted Chinese title.
        overview = data.get('overview') or {}
        texts = [overview.get('headline', ''), overview.get('missing_context_effect', '')]
        texts.extend(item.get('why_for_creator', '') for item in data.get('ideas', []))
        def chinese_copy(text):
            """Recognize predominantly Chinese copy after removing URLs from the legacy locale check."""
            text = re.sub(r'https?://\S+', '', str(text))
            letters = [c for c in text if c.isalpha()]
            return bool(letters) and sum('\u4e00' <= c <= '\u9fff' for c in letters) > len(letters) / 2
        legacy_set = (data.get('schema_version') == 'idea-set.v3-candidate1'
                      and all(chinese_copy(text) for text in texts if text)
                      and chinese_copy(overview.get('headline', '')))
        if not (legacy_request or legacy_set):
            raise ValueError('Missing report_language: only identifiable legacy Chinese inputs may omit it')
        value = 'zh'
    if not isinstance(value, str) or not value.strip():
        raise ValueError('report_language must be a nonempty language tag')
    # Validate the entire language-tag grammar before choosing a UI catalog.
    grammar = (r"(?:[a-z]{2,3}(?:-[a-z]{3}){0,3}|[a-z]{4}|[a-z]{5,8})"
               r"(?:-[a-z]{4})?(?:-(?:[a-z]{2}|[0-9]{3}))?"
               r"(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3}))*"
               r"(?:-[0-9a-wy-z](?:-[a-z0-9]{2,8})+)*"
               r"(?:-x(?:-[a-z0-9]{1,8})+)?")
    if not re.fullmatch(grammar, value, re.I | re.ASCII):
        raise ValueError(f'Invalid report_language tag: {value}')
    parts = value.lower().split('-')
    singletons = [p for p in parts[1:parts.index('x') if 'x' in parts else len(parts)] if len(p) == 1]
    core = parts[1:next((i for i, part in enumerate(parts) if len(part) == 1), len(parts))]
    variants = [part for part in core if len(part) >= 5 or len(part) == 4 and part[0].isdigit()]
    if len(variants) != len(set(variants)):
        raise ValueError(f'Invalid duplicate language variant: {value}')
    if len(singletons) != len(set(singletons)):
        raise ValueError(f'Invalid duplicate language extension: {value}')
    return parts[0]


def ui_label_sources(data):
    """One English source vocabulary; existing catalogs remain for old consumers."""
    return set() if report_language(data) in LEGACY_LANGUAGES else {entry['en'] for entry in CATALOG.values()}


def translator(data):
    """Return a translator for owned report strings in the validated UI locale."""
    language = report_language(data)
    labels = presentation_translations(data) if language not in LEGACY_LANGUAGES else {}
    def translate(value):
        """Translate catalog entries and preserve creator text that is absent from the catalog."""
        if language == 'zh':
            return value
        if value not in CATALOG:
            return value
        return CATALOG[value][language] if language in LEGACY_LANGUAGES else labels[CATALOG[value]['en']]
    return translate


def rendered_report_values(data):
    """The finite sources of canonical display values, not arbitrary creative copy."""
    originals = {data[key] for key in ('status', 'task_intent', 'source_mode') if key in data}
    for idea in data.get('ideas') or []:
        if idea.get('primary_evidence_ref'):
            originals.add(idea['primary_evidence_ref'])
        feasibility = idea.get('production_feasibility') or {}
        effort = feasibility.get('effort', 'unknown')
        if not feasibility.get('estimated_minutes') and effort != 'unknown':
            originals.add(effort)
    return originals


def presentation_translations(data):
    """Validate one existing-schema array for the union of gaps and display values.

    This is a presentation projection, not translation or semantic verification.
    Source gaps, canonical values and evidence lineage remain unchanged. Sharing
    the existing array preserves the IdeaSet schema accepted by downstream skills.
    """
    report_language(data)
    gaps = (data.get('evidence_summary') or {}).get('data_gaps') or []
    values = rendered_report_values(data)
    labels = ui_label_sources(data)
    allowed = set(gaps) | values | labels
    translations = data.get('evidence_gap_translations', [])
    if not isinstance(translations, list):
        raise ValueError('evidence_gap_translations must be an array')
    mapped = {}
    for item in translations:
        if not isinstance(item, dict) or set(item) != {'source_text', 'report_text'}:
            raise ValueError('Each presentation translation requires source_text and report_text')
        source, report = item['source_text'], item['report_text']
        if not isinstance(source, str) or source not in allowed or source in mapped:
            reason = 'repeats an earlier entry' if isinstance(source, str) and source in mapped else 'is neither an evidence_summary.data_gaps string nor a rendered canonical value or required UI label'
            raise ValueError(f'Presentation translation must name one unique original gap or rendered canonical value: source_text {source!r} {reason}; '
                             f'remove it. Rendered canonical values: {sorted(values, key=repr)}; data_gaps strings are matched exactly')
        if not isinstance(report, str) or not report.strip():
            raise ValueError('Presentation translation must preserve a nonempty report_text')
        if source in labels:
            prefix = re.match(r'^[#>*` \-]+', source)
            suffix = re.search(r'[*` ]+$', source)
            if '\n' in report or '\r' in report or (prefix and not report.startswith(prefix[0])) or (suffix and not report.endswith(suffix[0])):
                raise ValueError(f'UI label translation must preserve Markdown markers and boundary spacing: {source!r}')
        mapped[source] = report
    if 'report_language' in data:
        if any(value not in mapped for value in gaps):
            missing = [value for value in gaps if value not in mapped]
            raise ValueError(f'Explicit report languages require evidence_gap_translations for every original gap; missing: {missing}')
        if values - mapped.keys():
            raise ValueError(f'Explicit report languages require evidence_gap_translations for every rendered canonical value; missing: {sorted(values - mapped.keys(), key=repr)}')
        if labels - mapped.keys():
            raise ValueError(f'Missing UI label translations for report_language {data["report_language"]}; do not substitute another language. '
                             f'Use report_language.py with --labels for the English source vocabulary; missing: {sorted(labels - mapped.keys())}')
    return mapped


def evidence_gaps(data):
    """Project only real source gaps, in their original order, never enum labels."""
    mapped = presentation_translations(data)
    originals = (data.get('evidence_summary') or {}).get('data_gaps') or []
    return [mapped.get(value, value) for value in originals]


def report_value_translations(data):
    """Internal API: only rendered canonical values from the same bound union map."""
    mapped = presentation_translations(data)
    return {value: mapped[value] for value in rendered_report_values(data) if value in mapped}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('language')
    parser.add_argument('--labels', action='store_true', help='list required English UI label sources for this report language')
    args = parser.parse_args()
    data = {'report_language': args.language}
    print(json.dumps(sorted(ui_label_sources(data)), ensure_ascii=False) if args.labels else report_language(data))
