// 保护真实行为边界：年轻帖可被描述，但不能作为成熟表现模式；报告必须保留观察限制和原数值。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const scripts = fileURLToPath(new URL("../skills/review-creator-account/scripts", import.meta.url));
test("account review preserves age evidence and rejects unsupported declared comparisons", () => {
  const result = spawnSync("python3", ["-c", String.raw`
import copy, sys, unittest
sys.path.insert(0, sys.argv[1])
from evidence_context import baseline_maturity_context, content_maturity
from build_synthesis_input import build
from compose_review import compose
from synthesis_authoring import validate_authoring, AuthoringError
from validate_artifacts import validate

def fixture():
    rows = [dict(content_id=cid, title=cid, url='https://example.com/'+cid,
                 published_at=date, format=fmt, metrics={'views':views}, rank_by_views=i+1,
                 view_multiple_vs_format_median=views/100)
            for i,(cid,date,fmt,views) in enumerate([
                ('young','2026-10-31T12:00:00Z','video',20),
                ('mature','2026-10-19T14:00:59Z','video',200),
                ('other','2026-10-01T00:00:00Z','image',300)])]
    baselines=[dict(format=f,maturity_days=14,sample_size=5,metrics={'views':{'median':100,'mean':100,'available_n':5}}) for f in ['video','image']]
    return dict(schema_version='account-analysis.v3',analysis_id='analysis-test',snapshot_id='snapshot-test',
        rule_version='test',platform='tiktok',source_mode='public',
        scope={'account':'test','observed_at':'2026-11-01T14:00:59Z','window':{'start':'2026-10-01','end':'2026-11-01T14:00:59Z'}},
        coverage={'content_total':3,'formats':{'video':2,'image':1},'metric_available_counts':{'views':3},'source_coverage':{},'missing':[]},
        baselines=baselines,rankings=rows,outliers=[dict(content_id=r['content_id'],format=r['format'],views=r['metrics']['views'],view_multiple=r['view_multiple_vs_format_median'],baseline_median_views=100,comparable_n=5) for r in rows],
        pillar_performance=[],funnel_signals=[],claim_boundary={'not_allowed':['conversion','causal_attribution']})

def synthesis(ids, kind='public_observation'):
    return dict(schema_version='account-review-synthesis.v3',snapshot_id='snapshot-test',analysis_id='analysis-test',
        output_language='en',status='complete',headline='Public wording needs testing',diagnosis='Sales remain unknown.',
        what_works=[],what_hurts=[dict(pattern='Direct wording',explanation='A wording observation, not an explanation of sales.',content_ids=ids,claim_type=kind)],
        recommendations={'continue':[],'stop':[],'test':[{'action':'Measure linked inquiries and orders.'}]},profile={},data_gaps=['No orders supplied.'])

class EvidenceTests(unittest.TestCase):
    def test_cutoff_and_timezone(self):
        a=fixture();p=baseline_maturity_context(a)['by_format'][0]
        self.assertEqual(p['cutoff_at'],'2026-10-19T14:00:59+00:00')
        for date, expected in [('2026-10-19T14:00:58Z','mature'),('2026-10-19T16:00:59+02:00','mature'),('2026-10-19T14:01:00Z','too_recent')]:
            self.assertEqual(content_maturity(date,p),expected)
        a['scope']['window']['end']='2026-11-01'
        self.assertEqual(baseline_maturity_context(a)['by_format'][0]['cutoff_at'],'2026-10-19T23:59:59+00:00')
        a['scope']['window']={}
        self.assertEqual(baseline_maturity_context(a)['anchor_source'],'scope.observed_at')
    def test_projection_preserves_metrics_and_durations(self):
        a=fixture();before=copy.deepcopy(a)
        result=build(a,snapshot={'snapshot_id':'snapshot-test','items':[{'content_id':'young','duration_seconds':20}]})
        self.assertEqual(a,before)
        for source, projected in zip(a['rankings'],result['content']):
            for key in ['content_id','metrics','rank_by_views','view_multiple_vs_format_median']:
                self.assertEqual(source[key],projected[key])
        self.assertEqual(result['content'][0]['baseline_maturity'],'too_recent')
        self.assertEqual(result['content'][0]['duration_seconds'],20)
        self.assertEqual(result['scope']['highest_view_multiple']['published_at'],'2026-10-01T00:00:00Z')
    def test_young_observation_and_hypothesis_deliver_with_limit(self):
        a=fixture()
        for kind in ['public_observation','hypothesis']:
            s=synthesis(['young'],kind);report,profile,markdown=compose(a,s)
            validate(a,s,report,profile)
            self.assertIn('younger than the baseline cutoff',markdown)
            self.assertEqual(report['what_hurts'][0]['examples'][0]['metrics'],{'views':20})
            self.assertEqual(report['what_hurts'][0]['examples'][0]['baseline_maturity'],'too_recent')
    def test_young_comparison_rejected_and_mature_allowed(self):
        a=fixture()
        with self.assertRaises(AuthoringError): validate_authoring(a,synthesis(['young'],'performance_association'))
        validate_authoring(a,synthesis(['mature'],'performance_association'))
        with self.assertRaises(AuthoringError): validate_authoring(a,synthesis(['mature','other'],'performance_association'))
    def test_no_format_baseline_is_unknown(self):
        a=fixture();a['baselines']=[]
        for r in a['rankings']:r['view_multiple_vs_format_median']=None
        for r in a['outliers']:r.update(view_multiple=None,baseline_median_views=None,comparable_n=0)
        self.assertEqual(build(a)['content'][0]['baseline_maturity'],'unavailable')
        report,profile,markdown=compose(a,synthesis(['young']))
        self.assertIn('no comparable age baseline',markdown)
        with self.assertRaises(AuthoringError): validate_authoring(a,synthesis(['mature'],'performance_association'))
    def test_unknown_ids_or_missing_claim_type_fail(self):
        a=fixture()
        with self.assertRaises(AuthoringError):validate_authoring(a,synthesis(['missing']))
        s=synthesis(['young']);del s['what_hurts'][0]['claim_type']
        with self.assertRaises(AuthoringError):validate_authoring(a,s)
    def test_changed_age_rejected_in_report(self):
        a=fixture();s=synthesis(['young']);r,p,_=compose(a,s)
        r['what_hurts'][0]['examples'][0]['baseline_maturity']='mature'
        with self.assertRaisesRegex(ValueError,'altered evidence age'):validate(a,s,r,p)

unittest.main(argv=['test'],verbosity=2)
`, scripts], { encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
