#!/usr/bin/env python3
"""Bind supplementary product facts to captured sources without selecting an idea."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse


def source_route(url, source_type, tools):
    """Choose only a ready tool whose declared source and host scopes both match."""
    host = urlparse(url).hostname
    if not host or urlparse(url).scheme != 'https':
        raise ValueError('A source must have an HTTPS URL')
    eligible = [tool['id'] for tool in tools if tool.get('ready') is True
                and source_type in tool.get('source_types', [])
                and (tool.get('hosts') == ['*'] or host in tool.get('hosts', []))]
    return {'status': 'ready' if eligible else 'missing_matching_capability', 'tools': eligible,
            'source_type': source_type, 'url': url}


def audiovisual_scope(evidence, viewed_frame_ids, expected_content_id, transcript_reviewed=False):
    """Limit inference to inspected frames and reject an inconsistent speech timeline."""
    if not expected_content_id or evidence.get('content_ref', {}).get('content_id') != expected_content_id:
        raise ValueError('Audiovisual source identity mismatch')
    frames = evidence.get('frames', {}).get('items', [])
    available = {frame.get('frame_id') for frame in frames}
    if not set(viewed_frame_ids) <= available:
        raise ValueError('Visual observation names a frame absent from source evidence')
    duration = evidence.get('media', {}).get('duration_s')
    duration_valid = type(duration) in (int, float) and math.isfinite(duration) and duration > 0
    def in_bounds(value):
        """Accept only finite timestamps within the same media duration."""
        return duration_valid and type(value) in (int, float) and math.isfinite(value) and 0 <= value <= duration + 0.25
    if any(not in_bounds(frame.get('t_seconds')) for frame in frames if frame.get('frame_id') in viewed_frame_ids):
        raise ValueError('Viewed frame timestamp is outside the source media timeline')
    cues = evidence.get('transcript', {}).get('cues', [])
    consistent = duration_valid and bool(cues)
    previous_end = -1
    for cue in cues:
        start, end = cue.get('start_s'), cue.get('end_s')
        if not in_bounds(start) or not in_bounds(end) or end < start or start < previous_end:
            consistent = False
            break
        previous_end = end
    return {'visual_scope': 'inspected_frames_only' if viewed_frame_ids else 'unassessed',
            'viewed_frame_ids': list(viewed_frame_ids), 'account_wide_style_established': False,
            'speech_usable': bool(transcript_reviewed and consistent),
            'speech_gap': None if transcript_reviewed and consistent else 'unreviewed_or_inconsistent_transcript'}


def build(source):
    """Project exact captured product facts; brand efficacy claims remain unverified."""
    if not isinstance(source, dict) or source.get('schema_version') != 'creative-evidence-input.v1':
        raise ValueError('Expected creative-evidence-input.v1')
    def object_array(owner, key):
        """Require an explicit object array before accessing its entries."""
        value = owner.get(key)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f'{key} must be an explicit array of objects')
        return value
    expected = object_array(source, 'expected_products')
    pages = object_array(source, 'pages')
    if not expected:
        raise ValueError('Expected product URLs must be nonempty and unique')
    if not isinstance(source.get('evidence_id'), str) or not source['evidence_id'].strip():
        raise ValueError('Evidence ID must be nonempty')
    for product in expected:
        if any(not isinstance(product.get(k), str) or not product[k].strip() for k in ('url', 'brand', 'product_id')):
            raise ValueError('Expected product identity needs URL, brand and product ID')
        source_route(product['url'], 'brand_product_page', [])
    if len({p['url'] for p in expected}) != len(expected):
        raise ValueError('Expected product URLs must be nonempty and unique')
    expected_by_url = {p['url']: p for p in expected}
    facts, seen = [], set()
    for page in pages:
        page_facts = object_array(page, 'facts')
        identity = expected_by_url.get(page.get('url'))
        if not identity or any(page.get(key) != identity.get(key) for key in ('brand', 'product_id')):
            raise ValueError('Product identity does not match the requested brand/product/URL')
        if page.get('source_type') != 'brand_product_page' or not page.get('retrieved_at'):
            raise ValueError('Captured page needs a source type and retrieval time')
        datetime.fromisoformat(page['retrieved_at'].replace('Z', '+00:00'))
        source_route(page['url'], page['source_type'], [])
        text = page.get('text')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Captured page text is missing')
        for fact in page_facts:
            if fact.get('kind') not in {'identity', 'components', 'brand_claim'}:
                raise ValueError('Brand pages cannot establish clinical efficacy or sales outcomes')
            if not fact.get('excerpt') or fact['excerpt'] not in text:
                raise ValueError('Fact excerpt must occur verbatim in the captured page')
            if not isinstance(fact.get('report_text'), str) or not fact['report_text'].strip():
                raise ValueError('Fact needs a reviewed report-language projection')
            key = fact.get('id')
            if not isinstance(key, str) or not key or key in seen:
                raise ValueError('Fact IDs must be unique and nonempty')
            seen.add(key)
            facts.append({**fact, 'url': page['url'], 'retrieved_at': page['retrieved_at'],
                          'source_type': page['source_type'], 'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
                          'publishable_as_verified': fact['kind'] in {'identity', 'components'}})
    return {'schema_version': 'creative-evidence.v1', 'evidence_id': source['evidence_id'],
            'facts': facts, 'scope': 'product_identity_and_attributed_brand_statements_only',
            'missing_efficacy_blocks_product_display': False}


def bounded_path(value, base, run_root):
    """Resolve a relative evidence path inside the allowed run before reading it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Supplementary evidence path must be nonempty')
    path = Path(value)
    if path.is_absolute():
        raise ValueError('Supplementary evidence path must be relative to its artifact')
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(run_root.resolve()):
        raise ValueError('Supplementary evidence path escapes run_root')
    return resolved


def load_verified(reference, request_path, run_root=None):
    """Check both path boundaries, then rebuild evidence from its hashed captured input."""
    request_path = Path(request_path).resolve()
    run_root = Path(run_root).resolve() if run_root is not None else request_path.parent
    path = bounded_path(reference['path'], request_path.parent, run_root)
    raw = path.read_bytes()
    if reference.get('sha256') != hashlib.sha256(raw).hexdigest():
        raise ValueError('Supplementary evidence hash mismatch')
    evidence = json.loads(raw)
    ref = evidence['input']
    source_path = bounded_path(ref['path'], path.parent, run_root)
    source_raw = source_path.read_bytes()
    if hashlib.sha256(source_raw).hexdigest() != ref['sha256']:
        raise ValueError('Captured product input hash mismatch')
    expected = build(json.loads(source_raw))
    actual = {k: v for k, v in evidence.items() if k != 'input'}
    if actual != expected or reference.get('artifact_id') != evidence['evidence_id'] or reference.get('schema_version') != evidence['schema_version']:
        raise ValueError('Supplementary evidence differs from deterministic source projection')
    return evidence


def main():
    """Build a native evidence artifact from already acquired, reviewed source excerpts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    result = build(json.loads(raw))
    import os
    result['input'] = {'path': os.path.relpath(args.input.resolve(), args.output.resolve().parent),
                       'sha256': hashlib.sha256(raw).hexdigest()}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')


if __name__ == "__main__":
    main()
