"""Zero-cost mechanical replay; no provider calls, downloads or policy refitting on evaluation."""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, localcontext
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
from forecast_stack.eval import harness
from forecast_stack.model import market


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def arithmetic_cases():
    """Decimal.from_float makes the oracle refer to the actual IEEE-754 inputs."""
    probabilities = [0., 5e-324, 1e-323, 1e-300, 1e-100, 1e-10, .01, .5, .99, 1 - 2**-53, 1.]
    rows = []
    with localcontext() as context:
        context.prec = 1200
        for prior in probabilities:
            for yes in probabilities:
                for no in probabilities:
                    p, ly, ln = map(Decimal.from_float, (prior, yes, no))
                    denominator = p * ly + (1 - p) * ln
                    if denominator:
                        rows.append({'spec': {'kind': 'bayes', 'prior': prior, 'likelihood_yes': yes,
                                              'likelihood_no': no, 'observation': 'Synthetic numeric stress case'},
                                     'expected': float(p * ly / denominator)})
        for prior in probabilities:
            for ratio in [0., 5e-324, 1e-300, .5, 1., 2., 1e100, 1e300, sys.float_info.max]:
                p, lr = map(Decimal.from_float, (prior, ratio))
                denominator = p * lr + 1 - p
                if denominator:
                    rows.append({'spec': {'kind': 'bayes', 'prior': prior, 'likelihood_ratio': ratio,
                                          'observation': 'Synthetic numeric stress case'},
                                 'expected': float(p * lr / denominator)})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--arithmetic-cases', action='store_true')
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit('Refusing to overwrite an existing result')
    if args.arithmetic_cases:
        result = {'synthetic': True, 'oracle': '1200-digit Decimal arithmetic on exact binary float inputs',
                  'tolerance': 1e-12, 'cases': arithmetic_cases()}
    else:
        protocol = json.loads((ROOT / 'protocol.json').read_text())
        data = json.loads((ROOT / 'inputs.json').read_text())
        development = json.loads((ROOT / 'development.json').read_text())
        assert data['protocol_sha256'] == digest(ROOT / 'protocol.json')
        train = [SimpleNamespace(source=r['source'], prior=r['probability'], outcome=r['outcome']) for r in development['train']]
        offsets = harness._fit_source_offsets(train, 1, 0, lambda r: market.calibrate_market_probability(r.source, r.prior))
        assert offsets == protocol['candidate_offsets'], 'Frozen candidate cannot silently change'
        excluded = set(data['excluded_source_question_hashes'])
        keys = set()
        rows = data['rows']
        assert rows, 'No eligible resolved evaluation rows'
        for row in rows:
            identity = json.dumps([row['source'], row['id']], separators=(',', ':'))
            assert hashlib.sha256(identity.encode()).hexdigest() not in excluded
            key = (row['round'], row['source'], row['id'], row['resolution_date'])
            assert key not in keys
            keys.add(key)
            assert row['round'] in protocol['evaluation_rounds']
            assert type(row['outcome']) is int and row['outcome'] in (0, 1)
            assert not isinstance(row['probability'], bool) and math.isfinite(row['probability']) and 0 <= row['probability'] <= 1

        def predict(row, method):
            p = row['probability']
            if method == 'raw':
                return p
            p = market.calibrate_market_probability(row['source'], p)
            if method == 'incumbent':
                return p
            return harness._platt(p, 1, 0, offsets.get(row['source'], 0))

        methods = ['raw', 'incumbent', 'candidate']
        def scores(selected):
            result = {}
            for method in methods:
                ps = [predict(r, method) for r in selected]
                losses = [(p - r['outcome'])**2 for p, r in zip(ps, selected)]
                likelihoods = [p if r['outcome'] else 1 - p for p, r in zip(ps, selected)]
                result[method] = {'brier': statistics.fmean(losses),
                                  'log_loss': statistics.fmean(-math.log(p) for p in likelihoods) if min(likelihoods) > 0 else 'infinite'}
            return result

        def interval(challenger, reference):
            groups = defaultdict(list)
            for row in rows:
                groups[(row['source'], row['id'])].append((predict(row, challenger) - row['outcome'])**2 - (predict(row, reference) - row['outcome'])**2)
            totals = [(sum(v), len(v)) for v in groups.values()]
            rng = random.Random(protocol['promotion']['seed'])
            samples = []
            for _ in range(protocol['promotion']['bootstrap_draws']):
                total = count = 0
                for _ in totals:
                    value, n = totals[rng.randrange(len(totals))]
                    total += value
                    count += n
                samples.append(total / count)
            samples.sort()
            return [samples[int(.025 * len(samples))], samples[int(.975 * len(samples)) - 1]]

        overall = scores(rows)
        rounds = {due: {'n': len(rs), 'scores': scores(rs)} for due in protocol['evaluation_rounds'] if (rs := [r for r in rows if r['round'] == due])}
        delta = overall['candidate']['brier'] - overall['incumbent']['brier']
        ci = interval('candidate', 'incumbent')
        gates = {'effect': delta <= -protocol['promotion']['minimum_brier_improvement'], 'interval': ci[1] < 0,
                 'round_signs': len(rounds) == len(protocol['evaluation_rounds']) and all(v['scores']['candidate']['brier'] <= v['scores']['incumbent']['brier'] for v in rounds.values()),
                 'log_loss': overall['candidate']['log_loss'] <= overall['incumbent']['log_loss'],
                 'fresh_replication': False}
        result = {'protocol_sha256': digest(ROOT / 'protocol.json'), 'inputs_sha256': digest(ROOT / 'inputs.json'),
                  'development_sha256': digest(ROOT / 'development.json'), 'replay_sha256': digest(Path(__file__)),
                  'market_source_sha256': digest(Path(market.__file__)), 'cost_usd': 0, 'model_calls': 0,
                  'rows': len(rows), 'events': len({(r['source'], r['id']) for r in rows}), 'scores': overall, 'by_round': rounds,
                  'by_source': {source: {'n': len(rs), 'scores': scores(rs)} for source in sorted({r['source'] for r in rows}) if (rs := [r for r in rows if r['source'] == source])},
                  'candidate_minus_incumbent_brier': delta, 'cluster_bootstrap_95': ci,
                  'raw_minus_incumbent_brier': overall['raw']['brier'] - overall['incumbent']['brier'], 'raw_minus_incumbent_interval': interval('raw', 'incumbent'),
                  'promotion_gates': gates, 'promote': all(gates.values()),
                  'interpretation': 'Mechanical crowd-aware retrospective evaluation, not new LLM judgment, official leaderboard scoring, or prospective timing proof. Raw is a reference, not a post-hoc promoted policy.'}
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'output': args.out.name, 'cases': len(result.get('cases', [])), 'rows': result.get('rows'), 'scores': result.get('scores'), 'promote': result.get('promote')}))


if __name__ == '__main__':
    main()
