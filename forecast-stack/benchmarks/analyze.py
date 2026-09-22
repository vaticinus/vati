"""Offline analysis of frozen exact-world runs; never a real-world Brier leaderboard.

Usage: python benchmarks/analyze.py report.json ... --out scores.json
Or replay the compact numerical archive: python benchmarks/analyze.py inputs.json --out scores.json
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import random
import statistics


def wilson(successes, n):
    if not n:
        return None
    z = 1.959963984540054
    p = successes / n
    scale = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / scale
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / scale
    return [max(0, centre - radius), min(1, centre + radius)]


def regret(row):
    p, target = row['probability'], row['oracle']
    if target is None:
        return None
    return max(target ** 2, (1 - target) ** 2) if p is None else (p - target) ** 2


def clustered_interval(pairs):
    clusters = {}
    for family, delta in pairs:
        clusters.setdefault(family, []).append(delta)
    if not clusters:
        return None
    values = list(clusters.values())
    rng = random.Random(20260922)
    means = []
    for _ in range(5000):
        sample = [rng.choice(values) for _ in values]
        means.append(sum(sum(v) for v in sample) / sum(len(v) for v in sample))
    means.sort()
    return {'mean': statistics.fmean(d for _, d in pairs), 'ci95': [means[125], means[4875]],
            'n_pairs': len(pairs), 'n_mechanism_clusters': len(values),
            'limitation': 'Selected synthetic mechanisms; observed zero variance is not evidence about unseen failure modes.'}


# Expected losses from the publicly stipulated payoff tables, in millions.
# No model is used to grade its own decisions. Robust-choice regret is bounded,
# not an expected value under an invented distribution over unknown probabilities.
DECISION_LOSSES = {
    'net-loss': {'do_nothing': .9, 'prevent': 2},
    'forecast-versus-action': {'expand': 4.8, 'wait': 8},
    'insurance-deductible': {'insure': 1.4, 'uninsured': 4},
    'value-of-perfect-information': {'contract': 2, 'wait': 3},
    'incremental-cashflows': {'continue': -1, 'abort': 0},
}


def check_equal(actual, expected, tolerance):
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(check_equal(a, b, tolerance) for a, b in zip(actual, expected)))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and math.isfinite(actual) and abs(actual - expected) <= tolerance)
    return isinstance(expected, str) and actual == expected


def analyze(reports):
    summaries = []
    comparisons = []
    for report in reports:
        if not report.get('complete'):
            raise ValueError(f"Incomplete run cannot enter the completed-run scoreboard: {report.get('run')}")
        rows = report['rows']
        seen = set()
        for row in rows:
            identity = (row['id'], row['arm'])
            if identity in seen:
                raise ValueError(f'Duplicate experimental row: {identity}')
            seen.add(identity)
            for key in ('probability', 'oracle'):
                value = row[key]
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                          or not math.isfinite(value) or not 0 <= value <= 1):
                    raise ValueError(f'Invalid {key}')
            if row['oracle'] is not None:
                calculated = (row['probability'] is not None
                              and abs(row['probability'] - row['oracle']) <= row['tolerance'])
                if row['correct'] is not calculated:
                    raise ValueError(f'Inconsistent numerical grade: {identity}')
            if row.get('checks'):
                values = row.get('evaluation')
                values = values if isinstance(values, dict) else {}
                calculated_checks = {key: check_equal(values.get(key), expected, row['tolerance'])
                                     for key, expected in row['checks'].items()}
                if row.get('check_results') != calculated_checks:
                    raise ValueError(f'Inconsistent substantive checks: {identity}')
                if row.get('substantive_correct') is not (row['correct'] and all(calculated_checks.values())):
                    raise ValueError(f'Inconsistent substantive grade: {identity}')
        arms = sorted({row['arm'] for row in rows})
        for arm in arms:
            selected = [r for r in rows if r['arm'] == arm]
            passed = sum(bool(r['correct']) for r in selected)
            checked = [r for r in selected if r.get('checks')]
            losses = [regret(r) for r in selected if r['oracle'] is not None]
            decisions = []
            for row in selected:
                evaluation = row.get('evaluation')
                action = evaluation.get('action') if isinstance(evaluation, dict) else None
                if not isinstance(action, str):
                    action = None
                matrix = DECISION_LOSSES.get(row['id'])
                if matrix:
                    chosen = matrix.get(action)
                    decisions.append({'id': row['id'], 'action': action,
                                      'identified': chosen is not None,
                                      'expected_regret_million': None if chosen is None else chosen - min(matrix.values()),
                                      'missing_penalized_regret_million': (max(matrix.values()) if chosen is None else chosen) - min(matrix.values())})
                elif row['id'] == 'robust-choice':
                    decisions.append({'id': row['id'], 'action': action,
                                      'regret_bounds_million': [0, 0] if action == 'reserve' else [1, 5] if action == 'wait' else None})
            summaries.append({
                'run': report['run'], 'model': report['model'], 'arm': arm,
                'n': len(selected), 'headline_passes': passed,
                'headline_score_out_of_ten': 10 * passed / len(selected),
                'headline_wilson_95': wilson(passed, len(selected)),
                'checked_substantive_n': len(checked),
                'checked_substantive_passes': sum(bool(r.get('substantive_correct')) for r in checked),
                'execution_errors': sum(bool(r.get('error')) for r in selected),
                'withheld': sum(bool(r.get('issues')) for r in selected),
                'mean_missing_penalized_expected_excess_brier': statistics.fmean(losses) if losses else None,
                'mean_latency_ms': statistics.fmean(r['ms'] for r in selected),
                'cost_upper_usd': report.get('summary', {}).get(arm, {}).get('cost_upper_usd'),
                'decisions': decisions,
                'misses': [{'id':r['id'], 'p':r['probability'], 'oracle':r['oracle'],
                            'error':r.get('error'), 'check_results':r.get('check_results')}
                           for r in selected if not r['correct'] or (r.get('checks') and not r.get('substantive_correct'))],
            })
        harness = {r['id']: r for r in rows if r['arm'] == 'harness'}
        for arm in ('direct_thinking', 'direct_budget'):
            baseline = {r['id']: r for r in rows if r['arm'] == arm}
            paired = [(r['family'], regret(harness[cid]) - regret(r)) for cid, r in baseline.items()
                      if cid in harness and r['oracle'] is not None]
            if paired:
                comparisons.append({'run':report['run'], 'model':report['model'], 'baseline':arm,
                                    'metric':'harness minus baseline expected excess Brier; negative favours harness',
                                    **clustered_interval(paired)})
    return {'scope':'Exact-world diagnostics, not empirical forecasting skill or independent semantic review. Numerical grades and explicit checks are recomputed; no-point response acceptance is retained from the runner. Decision regret uses the requested structured action, with missing/malformed actions penalized separately from wrong actions.',
            'summaries':summaries, 'paired_comparisons':comparisons}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='+', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('refusing to overwrite an existing analysis')
    reports = []
    for path in args.inputs:
        doc = json.loads(path.read_text())
        if 'reports' in doc:
            reports.extend(doc['reports'])
        else:
            reports.append({**doc, 'run':path.stem})
    result = analyze(reports)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'reports':len(reports), 'arm_summaries':len(result['summaries']), 'output':str(args.out)}))


if __name__ == '__main__':
    main()
