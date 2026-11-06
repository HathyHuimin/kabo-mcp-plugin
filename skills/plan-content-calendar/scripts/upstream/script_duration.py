#!/usr/bin/env python3
"""Choose an auditable draft duration and estimate whether the written script fills it."""
import argparse
import copy
import json
import math
import re
import statistics
from datetime import date, datetime, timezone
from pathlib import Path


def seconds(value, label, minimum=5, maximum=7200):
    """Require a finite, bounded duration instead of accepting booleans or NaN."""
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be {minimum}..{maximum} seconds')
    return value


def request_format(request):
    """Reject malformed request/format containers before accessing duration fields."""
    if not isinstance(request, dict):
        raise ValueError('Duration request must be an object')
    value = request.get('content_format')
    if not isinstance(value, dict):
        raise ValueError('Duration content_format must be an object')
    return value


def recommend(request, context):
    """Prioritize explicit intent, then comparable channel evidence and content workload."""
    content_format = request_format(request)
    if not isinstance(context, dict):
        raise ValueError('Duration context must be an object')
    samples = context.get('samples', [])
    if not isinstance(samples, list):
        raise ValueError('Duration samples must be an array')
    kind = content_format.get('kind')
    platform = request.get('platform')
    if kind not in ('short_form', 'long_form') or not platform:
        raise ValueError('Clarify platform and short/long form before recommending duration')
    explicit = content_format.get('target_duration_seconds')
    if explicit is not None and type(explicit) is not int:
        raise ValueError('Explicit target must be an integer number of seconds')
    prior = context.get('prior_target_seconds') if context.get('prior_applies') is True else None
    complexity = context.get('complexity', 'medium')
    if complexity not in ('light', 'medium', 'heavy'):
        raise ValueError('complexity must be light, medium or heavy')
    steps = context.get('steps', 1)
    if type(steps) is not int or not 0 <= steps <= 100:
        raise ValueError('steps must be an integer from 0 to 100')
    demo = seconds(context.get('demonstration_seconds', 0), 'demonstration_seconds', 0)
    content_estimate = (15 if kind == 'short_form' else 90) + steps * (10 if kind == 'short_form' else 35)
    content_estimate = content_estimate * {'light': 0.8, 'medium': 1, 'heavy': 1.5}[complexity] + demo
    rows, excluded, urls = [], [], set()
    for index, row in enumerate(samples):
        if not isinstance(row, dict):
            excluded.append({'index': index, 'reason': 'invalid_sample_record'})
            continue
        reason = None
        if not context.get('channel_id') or row.get('channel_id') != context['channel_id']:
            reason = 'different_or_unknown_channel'
        elif row.get('platform') != platform or row.get('format') != kind:
            reason = 'different_platform_or_format'
        elif row.get('topic_relevant') is not True:
            reason = 'not_comparable_content'
        elif not isinstance(row.get('url'), str) or not isinstance(row.get('published_at'), str) or not row['url'] or not row['published_at']:
            reason = 'missing_source_or_date'
        elif not re.fullmatch(r'https://[^/ ]+/[^ ]*', str(row['url'])):
            reason = 'invalid_source_url'
        elif row['url'] in urls:
            reason = 'duplicate_url'
        else:
            try:
                seconds(row.get('duration_seconds'), 'sample_duration')
                published = row['published_at']
                if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', published):
                    parsed = datetime.fromisoformat(published)
                elif re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?', published):
                    parsed = datetime.fromisoformat(published.replace('Z', '+00:00'))
                else:
                    raise ValueError('Invalid publication date or timestamp')
            except ValueError:
                reason = 'invalid_duration_or_date'
        if reason:
            excluded.append({'index': index, 'reason': reason})
        else:
            parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
            rows.append({**row, '_published_utc': parsed}); urls.add(row['url'])
    if len(rows) >= 5:
        median = statistics.median(row['duration_seconds'] for row in rows)
        kept = []
        for row in rows:
            if median / 4 <= row['duration_seconds'] <= median * 4:
                kept.append(row)
            else:
                excluded.append({'url': row['url'], 'reason': 'duration_outlier_over4x_median'})
        rows = kept
    stats = None
    if rows:
        durations = [row['duration_seconds'] for row in rows]
        stats = {'n': len(rows), 'mean_seconds': round(statistics.mean(durations), 2),
                 'median_seconds': statistics.median(durations), 'min_seconds': min(durations), 'max_seconds': max(durations),
                 'window_start': min(rows, key=lambda row: row['_published_utc'])['published_at'], 'window_end': max(rows, key=lambda row: row['_published_utc'])['published_at'],
                 'source_urls': [row['url'] for row in rows]}
    if explicit is not None:
        target, basis = seconds(explicit, 'explicit target'), 'explicit_current_request'
    elif prior is not None:
        target, basis = seconds(prior, 'applicable prior target'), 'applicable_prior_request'
    elif stats:
        # A channel distribution informs the recommendation; it never overrides an explicit target.
        target, basis = max(stats['median_seconds'], content_estimate), 'comparable_channel_and_content_estimate'
    else:
        target, basis = content_estimate, 'platform_form_and_content_estimate'
    if target > 7200:
        raise ValueError('Content workload exceeds the supported duration; split or clarify the scope')
    target = max(5, round(target))
    return {'target_seconds': target, 'range_seconds': [max(5, round(target*0.8)), min(7200, round(target*1.2))],
            'basis': basis, 'platform': platform, 'format': kind, 'channel_id': context.get('channel_id'),
            'statistics': stats, 'excluded_samples': excluded, 'complexity': complexity, 'steps': steps,
            'demonstration_seconds': demo, 'content_estimate_seconds': round(content_estimate),
            'research_status': context.get('research_status', 'not_available'),
            'assumption': 'Draft planning estimate, not a measured platform average or actual read-through. Adjust after rehearsal.'}


def prepare(request, context):
    """Freeze the original explicit target and the evidence used for a reproducible recommendation."""
    request_format(request)
    result = copy.deepcopy(request)
    if result.get('mode') == 'packaging_only':
        return result
    original_target = result['content_format'].get('target_duration_seconds')
    plan = recommend(result, context)
    result['content_format']['target_duration_seconds'] = plan['target_seconds']
    result['duration_plan'] = {'requested_target_seconds': original_target, 'context': copy.deepcopy(context), 'decision': plan}
    return result


def validate_plan(request):
    """Reject a duration decision changed after its source inputs were frozen."""
    plan = request.get('duration_plan')
    if plan is None:
        return
    if not isinstance(plan, dict) or set(plan) != {'requested_target_seconds', 'context', 'decision'}:
        raise ValueError('Duration plan requires requested_target_seconds, context and decision only')
    if not isinstance(plan['context'], dict) or not isinstance(plan['decision'], dict):
        raise ValueError('Duration plan context and decision must be objects')
    request_format(request)
    original = copy.deepcopy(request)
    original['content_format']['target_duration_seconds'] = plan['requested_target_seconds']
    expected = recommend(original, plan['context'])
    if expected != plan['decision'] or request['content_format']['target_duration_seconds'] != expected['target_seconds']:
        raise ValueError('Duration plan does not match its original target/evidence context')


def estimate(package):
    """Estimate spoken time plus explicitly described non-speaking segments; never claim measurement."""
    script = package.get('script')
    if not script:
        return None
    scenes = script.get('scenes', [])
    pieces = [scene.get('spoken_text', '') for scene in scenes]
    joined = '\n'.join(pieces)
    extras = [script.get('cta', {}).get('spoken_text', '')]
    extras += [hook.get('spoken_line', '') for hook in package.get('hook_options', []) if hook.get('hook_id') == script.get('selected_hook_id')]
    for text in extras:
        if text and text not in joined:
            joined += '\n' + text
    cjk = len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', joined))
    words = len(re.findall(r"\b\w+(?:['’-]\w+)*\b", re.sub(r'[\u3400-\u4dbf\u4e00-\u9fff]', ' ', joined)))
    language = script.get('language', '').lower().split('-')[0]
    character_pattern = {'ja': r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]',
                         'ko': r'[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\u3400-\u4dbf\u4e00-\u9fff]'}.get(language)
    language_characters = len(re.findall(character_pattern, joined)) if character_pattern else 0
    characters_per_minute = {'ja': 300, 'ko': 300}.get(language)
    if character_pattern:
        words = len(re.findall(r"\b\w+(?:['’-]\w+)*\b", re.sub(character_pattern, ' ', joined)))
    words_per_minute = 130 if language == 'ar' else 160
    timing = script.get('timing', {})
    pace = timing.get('pace', 'normal')
    if pace not in ('normal', 'deliberate', 'brisk'):
        raise ValueError('Unsupported speech pace')
    multiplier = {'normal': 1, 'deliberate': 1.2, 'brisk': 0.85}[pace]
    character_minutes = language_characters / characters_per_minute if characters_per_minute else cjk / 240
    spoken = (character_minutes + words / words_per_minute) * 60 * multiplier
    silent, seen = 0, set()
    for segment in timing.get('non_speaking_segments', []):
        order = segment.get('scene_order')
        if type(order) is not int or order not in [scene.get('order') for scene in scenes] or order in seen:
            raise ValueError('Non-speaking segments need unique existing scene orders')
        if not isinstance(segment.get('purpose'), str) or not segment['purpose'].strip():
            raise ValueError('A non-speaking segment needs a concrete demonstration/action purpose')
        silent += seconds(segment.get('seconds'), 'non-speaking segment', 0)
        seen.add(order)
    total = spoken + silent
    return {'estimated_seconds': round(total, 1), 'spoken_seconds': round(spoken, 1), 'non_speaking_seconds': silent,
            'word_count': words, 'cjk_characters': cjk, 'words_per_minute_assumption': words_per_minute,
            'cjk_characters_per_minute_assumption': 240 if not characters_per_minute else None,
            'language_characters': language_characters, 'characters_per_minute_assumption': characters_per_minute, 'pace': pace, 'method': 'heuristic_not_measured_readthrough'}


def validate_fit(package, request):
    """Catch a tiny draft labeled as a long video while allowing genuine visual demonstrations."""
    result = estimate(package)
    if result is None:
        return result
    target = request['content_format']['target_duration_seconds']
    if request.get('duration_plan') is not None and package['script'].get('timing') is None:
        raise ValueError('A prepared duration plan requires structured script timing')
    if result['estimated_seconds'] < max(0, target * 0.45 - 10):
        raise ValueError('Script is substantially too short for its target; expand meaningful content or revise the target')
    if result['estimated_seconds'] > max(target * 1.8, target + 25):
        raise ValueError('Script is substantially too long for its target; revise delivery or target')
    # Structured timing opts into a tighter production check; legacy timecode display remains compatible.
    if package['script'].get('timing') is not None:
        end = 0.0
        for scene in package['script']['scenes']:
            match = re.fullmatch(r'(\d+):(\d{2})(?:\.(\d+))?\s*[-–]\s*(\d+):(\d{2})(?:\.(\d+))?', scene['timecode'])
            if not match:
                raise ValueError('Timed scenes require MM:SS–MM:SS ranges')
            a,b,c,d,e,f = match.groups()
            start = int(a)*60+int(b)+float('0.'+(c or '0'))
            stop = int(d)*60+int(e)+float('0.'+(f or '0'))
            if int(b)>=60 or int(e)>=60 or start != end or stop <= start:
                raise ValueError('Scene timeline must be contiguous, increasing and non-overlapping')
            segment = next((x for x in package['script']['timing'].get('non_speaking_segments', []) if x['scene_order']==scene['order']), None)
            if segment and segment['seconds'] > stop-start:
                raise ValueError('Non-speaking demonstration exceeds its scene duration')
            scene_result = estimate({'script': {**package['script'], 'scenes': [scene], 'cta': {}, 'timing': {'pace': package['script']['timing']['pace'], 'non_speaking_segments': [segment] if segment else []}}, 'hook_options': []})
            if scene_result['estimated_seconds'] > (stop-start)*1.6+3:
                raise ValueError('Scene spoken copy and demonstration exceed its timeline slot')
            end = stop
        if abs(end-target) > max(10,target*.2) or abs(end-result['estimated_seconds']) > max(15,target*.35):
            raise ValueError('Scene timeline, spoken copy and demonstration estimate do not agree')
    return result


def main():
    """Prepare a request from local context; the host acquires missing evidence beforehand."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text()); context = json.loads(args.context.read_text())
    args.output.write_text(json.dumps(prepare(request, context), ensure_ascii=False, indent=2)+'\n')


if __name__ == "__main__":
    main()
