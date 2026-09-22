import { test } from 'node:test';
import assert from 'node:assert/strict';
import { finalizeForecastAnswer, parseForecastSpec, validateForecastCandidate, type ForecastContract } from '../src/lib/coherence.ts';
import { readForecastSnapshot } from '../src/lib/forecastSnapshot.ts';

const contract: ForecastContract = {
  event_type:'quantity_threshold',
  request:'Will the first official August 2035 release report at least 3%? Later revisions do not count.',
  question:'Will the first official August 2035 release report at least 3%?',
  resolution_date:'2035-09-30', dated_metric:'Initial official August 2035 release; later revisions excluded.',
  numeric_clause:{threshold:3, threshold_dir:'>=', ci_unit:'percent'},
  conditions:['first release only'], cruxes:[], queries:[],
};
const spec = {...contract, kind:'normal', mean:2.6, sd:.5, threshold:3, threshold_dir:'>=', ci_unit:'percent'};
const fence = (s: unknown) => 'A draft answer.\n```vaticinus-forecast\n' + JSON.stringify(s) + '\n```';

test('contract validation rejects proxy questions, changed deadlines, revision rules and thresholds', () => {
  for (const change of [
    {question:'Will the agency publish a favorable forecast?'}, {resolution_date:'2036-09-30'},
    {dated_metric:'Use the latest revised series.'}, {threshold:2.9}, {threshold_dir:'>'}, {ci_unit:'fraction'},
  ]) assert.ok(validateForecastCandidate({...spec, ...change}, contract).issues.length > 0);
  assert.deepEqual(validateForecastCandidate(spec, contract).issues, []);
});

test('an extreme legitimate forecast is not changed or rejected as already decided', () => {
  const check = validateForecastCandidate({...spec, mean:8, sd:.01}, contract);
  assert.deepEqual(check.issues, []);
  assert.ok(check.result!.probability > .99999);
});

test('overlapping scenario mass cannot override the model probability', () => {
  const check = validateForecastCandidate({...spec, scenarios:[
    {outcome:'At least 3', p:.8, resolves:'yes'}, {outcome:'Below 3', p:.2, resolves:'no'},
  ]}, contract);
  assert.ok(check.issues.some(s => s.includes('YES mass')));
});

test('a permissive model reviewer cannot bypass a changed numeric contract', async () => {
  const result = await finalizeForecastAnswer(fence({...spec, threshold:2}), {
    request:contract.request, contract, complete:async () => JSON.stringify({valid:true, explanation:'Approve the wrong event.'}),
  });
  assert.equal(result.spec, null);
  assert.equal(parseForecastSpec(result.text), null);
});

test('an unavailable semantic review fails closed rather than publishing the unreviewed number', async () => {
  const result = await finalizeForecastAnswer(fence(spec), {request:contract.request, contract, complete:async () => null});
  assert.equal(result.spec, null);
  assert.equal(parseForecastSpec(result.text), null);
});

test('unprovided citations cannot pass an otherwise permissive review', async () => {
  const result = await finalizeForecastAnswer(fence({...spec, evidence:[{fact:'Invented', source_url:'https://fabricated.example/report'}]}), {
    request:contract.request, contract, grounding:['The source packet contains no such report.'],
    complete:async () => JSON.stringify({valid:true, explanation:'Approved.'}),
  });
  assert.equal(result.spec, null);
});

test('a validated answer and its serialized card retain one result without inventing a binary interval', async () => {
  const binary = {...spec, kind:'binary', p_yes:.007};
  const final = await finalizeForecastAnswer(fence(binary), {request:contract.request, contract,
    complete:async () => JSON.stringify({valid:true, issues:[], explanation:'This is a rare event under the stated assumptions. New official evidence would update the judgment.'})});
  assert.ok(final.spec);
  const saved = readForecastSnapshot(JSON.parse(JSON.stringify(final.spec))).result!;
  assert.equal(saved.probability, .007);
  assert.equal(saved.ci_low, undefined);
  assert.equal(final.spec!.question, contract.question);
  assert.equal(parseForecastSpec(final.text)!.resolution_date, contract.resolution_date);
});

test('a reviewer cannot introduce an unreviewed reversal of the resolution rule', async () => {
  const approvedProse = 'Only the initial official release counts; later revisions cannot change the resolution.';
  const draft = approvedProse + '\n```vaticinus-forecast\n' + JSON.stringify(spec) + '\n```';
  const result = await finalizeForecastAnswer(draft, {request:contract.request, contract,
    complete:async () => JSON.stringify({valid:true, issues:[], explanation:'A later downward revision makes the initial YES resolve NO.'})});
  assert.ok(result.spec);
  assert.ok(result.text.includes(approvedProse));
  assert.ok(!result.text.includes('later downward revision'));
});

test('a supplied citation cannot smuggle a future observation past the issue date', async () => {
  const source_url = 'https://example.org/dataset';
  for (const as_of of ['2026-09-21', '2026-09-22']) {
    const candidate = {...spec, evidence:[{fact:'Official observed reading', source_url, as_of}]};
    const result = await finalizeForecastAnswer(fence(candidate), {
      request:contract.request, contract, now:new Date('2026-09-21T12:00:00Z'),
      grounding:[`${source_url} Observation dated ${as_of}.`],
      complete:async (_system, _user, stage) => JSON.stringify(stage === 'forecast_correct'
        ? {answer:'The cited observation supports this forecast.', spec:candidate}
        : {valid:true, issues:[]}),
    });
    assert.equal(result.spec !== null, as_of === '2026-09-21');
  }
});

test('citation admission rejects URL prefixes and unsupported prose citations', async () => {
  for (const source_url of ['https://agency.example/release', 'https://agency.example']) {
    const candidate = {...spec, evidence:[{fact:'Claim', source_url}]};
    const result = await finalizeForecastAnswer(fence(candidate), {
      request:contract.request, contract,
      grounding:['Supplied source: https://agency.example/release-with-different-findings'],
      complete:async () => JSON.stringify({valid:true, issues:[]}),
    });
    assert.equal(result.spec, null);
  }
  const result = await finalizeForecastAnswer(
    'The official source confirms this [release](https://invented.example/report).\n' + fence(spec),
    {request:contract.request, contract, complete:async () => JSON.stringify({valid:true, issues:[]})},
  );
  assert.equal(result.spec, null);
});

test('exact supplied citations survive markdown punctuation and URL normalization', async () => {
  const source_url = 'https://agency.example/report?year=2035&series=A';
  const result = await finalizeForecastAnswer(
    `See [the source](${source_url}).\n` + fence({...spec, evidence:[{fact:'Claim', source_url}]}),
    {request:contract.request, contract,
      grounding:['[Source](https://AGENCY.example:443/report?year=2035&series=A).'],
      complete:async () => JSON.stringify({valid:true, issues:[]})},
  );
  assert.ok(result.spec);
});

test('URL-labelled Markdown links admit the supplied URL but not a concealed destination', async () => {
  const source = 'https://agency.example/report_(2035)';
  for (const destination of [source, 'https://unprovided.example/report']) {
    const result = await finalizeForecastAnswer(`See [${source}](${destination}).\n` + fence(spec), {
      request:contract.request, contract, grounding:[source],
      complete:async (_system, _user, stage) => stage === 'forecast_review'
        ? JSON.stringify({valid:true, issues:[]}) : null,
    });
    assert.equal(result.spec !== null, destination === source);
  }
});

test('a reviewed identified range is a complete answer without a fabricated point card', async () => {
  const answer = 'The marginals identify only a 50–70% range. A conditional probability is needed for a point estimate.';
  const result = await finalizeForecastAnswer(answer, {
    request:contract.request, contract,
    complete:async () => JSON.stringify({valid:true, issues:[], no_point:true}),
  });
  assert.equal(result.spec, null);
  assert.equal(result.text, answer);
  assert.deepEqual(result.issues, []);
});

test('correction may remove an unjustified scalar rather than replace it with another scalar', async () => {
  const answer = 'No unique point is identified; the justified range is 50–70%.';
  let reviewed = 0;
  const result = await finalizeForecastAnswer(fence({...spec, kind:'binary', p_yes:.6}), {
    request:contract.request, contract,
    complete:async (_system, _user, stage) => {
      if (stage === 'forecast_correct') return JSON.stringify({answer, spec:null});
      reviewed++;
      return JSON.stringify(reviewed === 1
        ? {valid:false, issues:['A midpoint was invented despite unidentified dependence.']}
        : {valid:true, issues:[], no_point:true});
    },
  });
  assert.equal(result.spec, null);
  assert.equal(result.text, answer);
  assert.equal(parseForecastSpec(result.text), null);
});

test('omitting a card does not bypass substantive review or malformed block checks', async () => {
  for (const draft of ['The answer is 99%, trust me.', 'An answer.\n```vaticinus-forecast\n{broken}\n```']) {
    const result = await finalizeForecastAnswer(draft, {
      request:contract.request, contract,
      complete:async () => draft.includes('```') ? JSON.stringify({valid:true, issues:[]}) : null,
    });
    assert.equal(result.spec, null);
    assert.notEqual(result.text, draft);
    assert.ok(result.issues.length > 0);
  }
});

test('malformed model packaging cannot be approved as a card-free answer', async () => {
  const drafts = [
    '25%.\n```json\nvaticinus-forecast\n' + JSON.stringify({ ...contract, model: { kind: 'binary', p_yes: .25 } }) + '\n```',
    'The answer is 0.\n```json\n{"kind":"binary","p_yes":0}\n```\n' + JSON.stringify(contract),
  ];
  for (const draft of drafts) {
    const result = await finalizeForecastAnswer(draft, {
      request: contract.request, contract,
      complete: async () => JSON.stringify({ valid: true, issues: [] }),
    });
    assert.equal(result.spec, null);
    assert.notEqual(result.text, draft);
    assert.ok(result.issues.length > 0);
  }
});

test('a permissive review cannot approve a card-free scalar without a no-point certificate', async () => {
  const draft = 'The identified probability is 0.116, computed from the exhaustive regime mixture.';
  for (const no_point of [undefined, false]) {
    const result = await finalizeForecastAnswer(draft, {
      request: contract.request, contract,
      complete: async (_system, _user, stage) => stage === 'forecast_review'
        ? JSON.stringify({ valid: true, issues: [], no_point }) : null,
    });
    assert.equal(result.spec, null);
    assert.notEqual(result.text, draft);
    assert.ok(result.issues.length > 0);
  }
});

test('a generic JSON forecast is validated and issued through the same review boundary', async () => {
  const result = await finalizeForecastAnswer(fence(spec).replace('vaticinus-forecast', 'json'), {
    request:contract.request, contract,
    complete:async (_system, _user, stage) => stage === 'forecast_review'
      ? JSON.stringify({valid:true, issues:[]}) : null,
  });
  assert.ok(result.spec);
  assert.equal(readForecastSnapshot(result.spec).result!.probability, validateForecastCandidate(spec, contract).result!.probability);
  assert.equal((result.text.match(/```vaticinus-forecast/g) ?? []).length, 1);
  assert.ok(!result.text.includes('```json'));
});

test('a corrected forecast block still requires successful semantic review', async () => {
  for (const approve of [true, false]) {
    const result = await finalizeForecastAnswer(fence({...spec, threshold:2}), {
      request:contract.request, contract,
      complete:async (_system, _user, stage) => stage === 'forecast_correct'
        ? fence(spec).replace('vaticinus-forecast', 'json')
        : JSON.stringify({valid:approve, issues:approve ? [] : ['Unsupported assumption']}),
    });
    assert.equal(result.spec !== null, approve);
    if (result.spec) assert.equal(readForecastSnapshot(result.spec).result!.probability, validateForecastCandidate(spec, contract).result!.probability);
  }
});

test('mixed forecast encodings cannot hide a conflicting second estimate', async () => {
  const result = await finalizeForecastAnswer(fence(spec) + '\n' + fence({...spec, mean:5}).replace('vaticinus-forecast', 'json'), {
    request:contract.request, contract,
    complete:async (_system, _user, stage) => stage === 'forecast_review'
      ? JSON.stringify({valid:true, issues:[]}) : null,
  });
  assert.equal(result.spec, null);
});
