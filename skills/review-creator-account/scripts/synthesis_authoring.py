"""Strict author-shape/source-reference checks; never repair or infer authored values."""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from typing import Any
sys.dont_write_bytecode = True
from profile_contract import synthesis_schema
from evidence_context import maturity_by_format, content_maturity

class AuthoringError(ValueError):
    """Only closed-schema author fields and unknown copied IDs can be corrected in-place."""
    def __init__(self, issues: list[dict[str, str]]):
        self.issues = issues
        super().__init__('; '.join(f"{x['path']}: {x['message']}" for x in issues))

def shape_issues(value: Any, root: dict[str, Any], label: str) -> list[dict[str, str]]:
    """Interpret only the closed vocabulary actually used by these bundled schemas."""
    issues = []
    supported = {'$schema','$id','title','description','$defs','$ref','type','additionalProperties',
                 'required','properties','items','uniqueItems','minLength','maxLength','minItems',
                 'maxItems','const','enum','pattern'}
    def fail(path: str, message: str) -> None:
        issues.append({'path':path,'message':message})
    def check(value: Any, schema: dict[str, Any], path: str) -> None:
        unknown = set(schema)-supported
        if unknown:
            raise ValueError(f'unsupported synthesis schema constraints: {sorted(unknown)}')
        if '$ref' in schema:
            ref=schema['$ref'];prefix='#/$defs/'
            if set(schema)!={'$ref'} or not ref.startswith(prefix) or ref[len(prefix):]not in root['$defs']:
                raise ValueError('unsupported synthesis schema reference')
            return check(value,root['$defs'][ref[len(prefix):]],path)
        expected=schema.get('type');types={'object':dict,'array':list,'string':str}
        if expected is not None and (expected not in types or not isinstance(value,types[expected])):
            fail(path,f'must be {expected}');return
        if 'const'in schema and value!=schema['const']:fail(path,'must match the declared schema value')
        if 'enum'in schema and value not in schema['enum']:fail(path,'must be one of the declared values')
        if expected=='object':
            properties=schema.get('properties',{})
            for k in sorted(set(schema.get('required',[]))-set(value)):fail(path+'.'+k,'is required')
            for k in value:
                if k not in properties:
                    if schema.get('additionalProperties')is False:fail(path+'.'+k,f'contains unsupported fields: {k}')
                else:check(value[k],properties[k],path+'.'+k)
        elif expected=='array':
            if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',float('inf')):fail(path,'has invalid item count')
            if schema.get('uniqueItems')and any(v in value[:i]for i,v in enumerate(value)):fail(path,'contains duplicate items')
            for i,v in enumerate(value):check(v,schema['items'],f'{path}[{i}]')
        elif expected=='string':
            if len(value)<schema.get('minLength',0):fail(path,'has invalid string length')
            if len(value)>schema.get('maxLength',float('inf')):fail(path,f"must be at most {schema['maxLength']} characters")
            if 'pattern'in schema and re.fullmatch(schema['pattern'],value)is None:fail(path,'does not match the declared string format')
    check(value,root,label)
    return issues


def pattern_evidence_issues(content: list[dict[str, Any]], policies: dict[str, dict[str, Any]],
                           synthesis: dict[str, Any]) -> list[dict[str, str]]:
    """Check declared pattern claims against source rows after schema validation."""
    issues = []
    def fail(path: str, message: str) -> None:
        issues.append({'path':path,'message':message})
    rankings = {row['content_id']: row for row in content}
    for kind in ('what_works','what_hurts'):
        for n,row in enumerate(synthesis[kind]):
            for i,content_id in enumerate(row['content_ids']):
                if content_id not in rankings:
                    fail(f'synthesis.{kind}[{n}].content_ids[{i}]',f'synthesis references unknown content_id: {content_id}; copy an exact ID from synthesis_input.content')
            if row['claim_type'] == 'performance_association':
                cited = [rankings[cid] for cid in row['content_ids'] if cid in rankings]
                if len({item['format'] for item in cited}) > 1:
                    fail(f'synthesis.{kind}[{n}]', 'performance comparisons need one comparable format; split observations by format')
                for item in cited:
                    if content_maturity(item['published_at'], policies.get(item['format'])) != 'mature' or item.get('view_multiple_vs_format_median') is None:
                        fail(f'synthesis.{kind}[{n}]', f"{item['content_id']} cannot establish a baseline performance pattern: its age or comparable baseline is unavailable; use a bounded public observation or hypothesis instead")
    return issues


def validate_authoring(analysis: dict[str, Any], synthesis: Any) -> None:
    # Bad deterministic inputs/provenance are fatal, never a recoverable writing mistake.
    path = Path(__file__).resolve().parent.parent / 'references/account_analysis.schema.json'
    source_errors = shape_issues(analysis, json.loads(path.read_text(encoding='utf-8')), 'analysis')
    if source_errors:
        raise ValueError(f'deterministic analysis schema/binding is invalid: {source_errors}')
    ids = []
    for row in analysis['rankings']:
        if (not isinstance(row.get('content_id'),str) or not row['content_id']
                or not isinstance(row.get('title'),str) or not isinstance(row.get('format'),str)
                or not isinstance(row.get('metrics'),dict)):
            raise ValueError('deterministic analysis rankings contain an invalid evidence row')
        ids.append(row['content_id'])
    if len(ids) != len(set(ids)):
        raise ValueError('deterministic analysis rankings contain duplicate content_id values')
    if not isinstance(synthesis,dict) or synthesis.get('schema_version') != 'account-review-synthesis.v3':
        raise ValueError('synthesis schema_version does not match the declared contract')
    for field in ('analysis_id','snapshot_id'):
        if synthesis.get(field) != analysis[field]:
            raise ValueError(f'synthesis {field} does not match analysis')
    issues=shape_issues(synthesis,synthesis_schema(),'synthesis')
    if issues:raise AuthoringError(issues)
    issues = pattern_evidence_issues(analysis['rankings'], maturity_by_format(analysis), synthesis)
    if issues:raise AuthoringError(issues)


if __name__ == "__main__":
    raise SystemExit("Use drive.py report for the bound authoring stage.")
