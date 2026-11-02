#!/usr/bin/env python3
"""Build native rule claims from a bounded fixed-topic explanation.

Each explanation and each optional operational step becomes a separately evidenced
rule-check.v1 claim. Both Standard rendering and Light presentation consume this
same artifact. Quotation matching verifies source text, not semantic entailment;
the author must still ensure each assertion follows from its quoted passage.
"""
import json
import re
from pathlib import Path
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_rule_check import check_evidence, load_registry, page_identity, validate as validate_document
SKILL = Path(__file__).resolve().parents[1]

def operational_quote_errors(statement, quote):
    """Reject heading-only citations and invented UI locations for this English atom.

    This is a conservative structural check, not a general entailment classifier.
    The analyst still verifies that the cited instruction supports the complete action.
    """
    if not isinstance(quote, str) or not quote.strip():
        return ['action_quote_missing']
    normalized = quote.strip().casefold()
    errors = []
    if normalized.endswith((':', '?')):
        errors.append('action_quote_is_heading')
    if not re.search(r'\b(tap|turn|choose|toggle|select|enable|disable|set|allow|edit|click|switch|add|label|use|using)\b', normalized):
        errors.append('action_quote_has_no_instruction')
    for location in ('privacy', 'more options', 'reuse of content'):
        if location in statement.casefold() and location not in normalized:
            errors.append('action_location_not_in_quote:' + location)
    if 'settings' in statement.casefold() and not any(term in normalized for term in ('settings', 'privacy', 'more options', 'reuse of content')):
        errors.append('action_location_not_in_quote:settings')
    return errors


def validate(payload):
    registry = load_registry(str(SKILL / 'references/sources'))
    platform = payload['platform']
    pages = {page['source_id']: page for page in payload['pages']}
    result = []
    claims = []
    projections = []
    for index, claim in enumerate(payload['claims']):
        errors = []
        source_id = claim.get('source_id')
        page = pages.get(source_id)
        registered = registry.get(source_id)
        if not page or not registered:
            result.append({'valid': False, 'errors': ['unknown_or_unread_source']})
            continue
        # A same-host redirect to another FAQ is not the registered article either.
        if page_identity(page['url']) != page_identity(registered['url']) or \
                page_identity(page['fetched_url']) != page_identity(registered['url']):
            errors.append('page_identity_mismatch')
        evidence = {
            'source_id': source_id, 'url': page['url'], 'fetched_url': page['fetched_url'],
            'quote': claim.get('quote'), 'quote_source': 'raw_document',
            'retrieved_at': page['retrieved_at'], 'source_authority': registered['source_authority'],
        }
        page_texts = {page_identity(page['url']): [page['text']]}
        check_evidence(evidence, f'claims[{index}]', registry, 'confirmed', 'en', platform, errors, page_texts)
        result.append({'valid': not errors, 'errors': errors})
        if not errors and isinstance(claim.get('conclusion'), str):
            conclusion_id = f'claim-{index + 1}'
            claims.append({'claim_id': conclusion_id, 'statement': claim['conclusion'].strip(), 'verdict': 'confirmed', 'evidence': [evidence]})
            projection = {'conclusion_claim_id': conclusion_id, 'action_claim_id': None}
            # An operational instruction is a separate assertion, not free-standing UI advice.
            # It must have its own quotation, and passes exactly the Standard evidence checks.
            if claim.get('action'):
                action_errors = operational_quote_errors(claim['action'], claim.get('action_quote'))
                action_evidence = {**evidence, 'quote': claim.get('action_quote')}
                check_evidence(action_evidence, f'claims[{index}].action', registry,
                               'confirmed', 'en', platform, action_errors, page_texts)
                result[-1]['action_valid'] = not action_errors
                result[-1]['action_errors'] = action_errors
                if not action_errors:
                    action_id = f'action-{index + 1}'
                    claims.append({'claim_id': action_id, 'statement': claim['action'].strip(),
                                   'verdict': 'confirmed', 'evidence': [action_evidence]})
                    projection['action_claim_id'] = action_id
            projections.append(projection)
    artifact = None
    if claims:
        artifact = {'schema_version': 'rule-check.v1', 'platform': platform,
                    'region': 'UNKNOWN', 'region_source': 'unknown', 'delivery_language': 'en',
                    'checked_at': max(p['retrieved_at'] for p in pages.values()), 'claims': claims}
        if payload.get('unreachable_sources'):
            artifact['unreachable_sources'] = payload['unreachable_sources']
        page_texts = {page_identity(p['url']): [p['text']] for p in pages.values()}
        errors = validate_document(artifact, registry, page_texts)
        if errors:
            raise ValueError('Native rule-check contract failed: ' + '; '.join(errors))
    return {'checks': result, 'artifact': artifact, 'projections': projections}


if __name__ == "__main__":
    try:
        print(json.dumps(validate(json.load(sys.stdin)), ensure_ascii=False))
    except (ValueError, KeyError, TypeError) as error:
        print(json.dumps({'error': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
