"""Offline analysis only: python score.py. Never makes provider calls."""
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(name):
    return json.loads((ROOT / name).read_text())


def digest(name):
    return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


def mean(values):
    return sum(values) / len(values) if values else None


def log_loss(p, y):
    p = min(1 - 1e-6, max(1e-6, p))
    return -math.log(p if y else 1 - p)


def summarize():
    manifest, protocol, issued, outcomes = [load(n) for n in ('manifest.json', 'protocol.json', 'issued.json', 'outcomes.json')]
    assert issued['manifest_sha256'] == protocol['manifest_sha256'] == digest('manifest.json')
    assert issued['protocol_sha256'] == digest('protocol.json')
    cases = {c['id']: c for c in manifest['cases']}
    labels = {r['id']: r['outcome'] for r in outcomes['rows'] if r['outcome'] in (0, 1)}
    assert len(outcomes['rows']) == len(cases) and {r['id'] for r in outcomes['rows']} == set(cases)
    rows = {(r['id'], r['arm']): r for r in issued['rows']}
    assert len(rows) == len(issued['rows']) == len(cases) * len(manifest['arms'])
    assert set(rows) == {(i, a) for i in cases for a in manifest['arms']}
    n = len(cases)
    report = {'study': manifest['study'], 'manifest_sha256': digest('manifest.json'), 'issued_sha256': digest('issued.json'),
              'outcomes_sha256': digest('outcomes.json'), 'planned_events': n, 'resolved_events': len(labels),
              'release_months': len({c['cluster'] for c in cases.values()}), 'arms': {}}
    losses, logs = {}, {}
    for (i, arm), row in rows.items():
        if row['status'] == 'issued':
            p = row['p_yes']
            assert isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and 0 <= p <= 1
            if i in labels:
                losses[i, arm] = (p - labels[i]) ** 2
                logs[i, arm] = log_loss(p, labels[i])
    for arm in manifest['arms']:
        ar = [rows[i, arm] for i in cases]
        values = [losses[i, arm] for i in cases if (i, arm) in losses]
        total = sum(values)
        traces = [t for r in ar for t in r.get('traces', [])]
        report['arms'][arm] = {'planned': n, 'issued': sum(r['status'] == 'issued' for r in ar), 'scored': len(values),
            'coverage': sum(r['status'] == 'issued' for r in ar) / n, 'brier': mean(values),
            'log_loss': mean([logs[i, arm] for i in cases if (i, arm) in logs]),
            'all_case_brier_bounds': [total / n, (total + n - len(values)) / n],
            'accounted_usd': sum(r['accounted_usd'] for r in ar), 'calls': len(traces),
            'input_tokens': sum(t.get('usage', {}).get('prompt_tokens', 0) for t in traces),
            'output_tokens': sum(t.get('usage', {}).get('completion_tokens', 0) for t in traces),
            'total_elapsed_ms': sum(r.get('elapsed_ms', 0) for r in ar), 'status_counts': dict(Counter(r['status'] for r in ar))}
    report['constant_half_brier'] = 0.25 if labels else None
    report['accounted_usd'] = sum(r['accounted_usd'] for r in issued['rows'])
    assert abs(report['accounted_usd'] - issued['accounted_usd']) < 1e-8
    report['comparisons'] = {}
    for control in ('recency', 'recency_long'):
        paired = [i for i in cases if (i, 'selected') in losses and (i, control) in losses]
        delta = {i: losses[i, 'selected'] - losses[i, control] for i in paired}
        blocks = defaultdict(list)
        for i, d in delta.items():
            blocks[cases[i]['cluster']].append(d)
        keys = sorted(blocks)
        rng = random.Random(protocol['seed'])
        samples = []
        if keys:
            for _ in range(10000):
                drawn = [rng.choice(keys) for _ in keys]
                samples.append(sum(sum(blocks[k]) for k in drawn) / sum(len(blocks[k]) for k in drawn))
            samples.sort()
        lower, upper = 0.0, 0.0
        for i in cases:
            a, b = losses.get((i, 'selected')), losses.get((i, control))
            lower += (a if a is not None else 0) - (b if b is not None else 1)
            upper += (a if a is not None else 1) - (b if b is not None else 0)
        months = sorted({c['cluster'] for c in cases.values()})
        report['comparisons'][control] = {'paired_events': len(paired), 'paired_months': len(keys),
            'brier_delta_selected_minus_control': mean(list(delta.values())),
            'exploratory_month_bootstrap_ci95': [samples[249], samples[9749]] if samples else None,
            'all_case_delta_bounds': [lower / n, upper / n],
            'log_loss_delta': mean([logs[i, 'selected'] - logs[i, control] for i in paired]),
            'early_delta': mean([delta[i] for i in paired if cases[i]['cluster'] in months[:3]]),
            'late_delta': mean([delta[i] for i in paired if cases[i]['cluster'] in months[3:]]),
            'by_metric': {m: mean([delta[i] for i in paired if cases[i]['metric'] == m]) for m in ('payroll', 'unemployment', 'earnings')},
            'by_month': {k: mean(blocks[k]) for k in keys},
            'leave_one_month_out': {k: mean([delta[i] for i in paired if cases[i]['cluster'] != k]) for k in keys}}
    selected_rows = [rows[i, 'selected'] for i in cases if 'selected_ids' in rows[i, 'selected']]
    report['selection'] = {'completed_selections': len(selected_rows),
        'changed_packet_sets': sum(set(r['selected_ids']) != set(cases[r['id']]['fixed_ids']) for r in selected_rows),
        'document_kind_counts': dict(Counter(s.split('-')[0] for r in selected_rows for s in r['selected_ids']))}
    comps = report['comparisons'].values()
    report['numerical_replication_gate_passed'] = (len(labels) == n and all(a['coverage'] == 1 for a in report['arms'].values())
        and all(c['paired_events'] == n and c['brier_delta_selected_minus_control'] <= -0.002
                and c['exploratory_month_bootstrap_ci95'][1] < 0 and c['log_loss_delta'] <= 0 for c in comps)
        and report['comparisons']['recency']['early_delta'] < 0 and report['comparisons']['recency']['late_delta'] < 0)
    report['production_promotion'] = False
    report['interpretation_limits'] = ['Six consecutive months in one economy, not 18 independent macro shocks.',
        'Provider served weights and pre-issue evidence captures are not independently attested.',
        'Equal spending ceilings, unequal realized input/output compute and cost.',
        'Sparse BLS-only catalog and short-horizon nowcasts; no broad-domain or frontier-model inference.',
        'Retrospective AI-assisted design, not an independently administered or external-scoreboard evaluation.']
    return report


if __name__ == '__main__':
    result = summarize()
    text = json.dumps(result, indent=2, allow_nan=False) + '\n'
    (ROOT / 'scores.json').write_text(text)
    print(text)
