const get = id => document.getElementById(id);
const percent = value => new Intl.NumberFormat('en', { style: 'percent', maximumFractionDigits: 1 }).format(value);
const dollars = value => `$${Number(value).toFixed(4)}`;
const deadline = days => { const date = new Date(); date.setUTCDate(date.getUTCDate() + days); return date.toISOString().slice(0, 10); };
const templates = {
  conditional: {
    title: 'Compare two scenarios', description: "Set each scenario's chance and the event's probability within it. Scenario B takes the remaining weight.",
    spec: { kind: 'conditional', question: 'Will the project finish by the deadline?', resolution_date: deadline(90), dated_metric: 'Completion entered in the project register by the deadline.', partition: 'Scenario A or scenario B', branches: [{ condition: 'Scenario A', weight: 0.3, p_yes: 0.6 }, { condition: 'Scenario B', weight: 0.7, p_yes: 0.1 }] },
    fields: [['weight', 'Chance of scenario A (%)', 30, 0, 100, 1], ['yesA', 'Chance of completion in A (%)', 60, 0, 100, 1], ['yesB', 'Chance of completion in B (%)', 10, 0, 100, 1]],
  },
  bayes: {
    title: 'Update an estimate with new evidence', description: 'Start with the base rate. Then enter how often a positive signal appears in defective and working devices.',
    spec: { kind: 'bayes', question: 'Is the screened device defective, given a positive result?', prior: 0.01, observation: 'One positive screening result', likelihood_yes: 0.8, likelihood_no: 0.1 },
    fields: [['prior', 'Base rate of defects (%)', 1, 0, 100, 0.1], ['signalYes', 'Positive signal when defective (%)', 80, 0, 100, 1], ['signalNo', 'Positive signal when not defective (%)', 10, 0, 100, 1]],
  },
  normal: {
    title: 'Estimate the chance of reaching a target', description: 'Enter the expected outcome and its spread. This assumes a normal distribution; the spread describes possible outcomes, not confidence in the estimate.',
    spec: { kind: 'normal', question: 'Will final capacity reach at least 110 MW?', mean: 100, sd: 10, threshold: 110, threshold_dir: '>=', ci_unit: 'MW' },
    fields: [['mean', 'Expected capacity (MW)', 100, -1000000, 1000000, 1], ['sd', 'Standard deviation (MW)', 10, 0.001, 1000000, 0.1], ['threshold', 'Target capacity (MW)', 110, -1000000, 1000000, 1]],
  },
  binary: {
    title: 'Record your own estimate', description: 'Enter the probability you would assign to the event. The calculation preserves your judgment as supplied.',
    spec: { kind: 'binary', question: 'Will your precisely defined event occur?', p_yes: 0.35 },
    fields: [['judgment', 'Your probability (%)', 35, 0, 100, 1]],
  },
};
let record = null;
let probability = null;
let revision = 0;
let active = null;
let availableModels = [];
let mode = 'forecast';
function panels(show) {
  for (const name of ['empty', 'loading', 'result', 'error-panel']) get(name).hidden = name !== show;
  document.querySelector('.result-panel').setAttribute('aria-busy', String(show === 'loading'));
}
function invalidate() {
  revision += 1; record = null; probability = null;
  if (active) active.abort();
  panels('empty'); get('copy-status').textContent = ''; get('result-label').textContent = 'Ready when you are';
}
function setMode(next) {
  if (mode !== next) invalidate();
  mode = next;
  for (const name of ['forecast', 'lab']) {
    get(`${name}-tab`).setAttribute('aria-selected', String(name === next));
    get(`${name}-tab`).tabIndex = name === next ? 0 : -1;
    get(`${name}-panel`).hidden = name !== next;
  }
  get('mode-caption').textContent = next === 'forecast' ? 'Uses your OpenRouter key and credits.' : 'No key or model credits needed.';
}
for (const name of ['forecast', 'lab']) {
  get(`${name}-tab`).addEventListener('click', () => setMode(name));
  get(`${name}-tab`).addEventListener('keydown', event => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); const next = event.key === 'Home' ? 'forecast' : event.key === 'End' ? 'lab' : name === 'forecast' ? 'lab' : 'forecast';
      setMode(next); get(`${next}-tab`).focus();
    }
  });
}
function metadata(entries) {
  get('result-meta').replaceChildren();
  for (const [label, value] of entries) {
    const term = document.createElement('dt'), detail = document.createElement('dd');
    term.textContent = label; detail.textContent = value; get('result-meta').append(term, detail);
  }
}
function list(id, section, values) {
  const items = Array.isArray(values) ? values.slice(0, 12) : [];
  get(id).replaceChildren(); get(section).hidden = items.length === 0;
  for (const value of items) { const li = document.createElement('li'); li.textContent = typeof value === 'string' ? value : JSON.stringify(value); get(id).append(li); }
}
function payoff() {
  const gain = Number(get('upside').value), loss = Number(get('downside').value);
  if (probability === null || !get('upside').value || !get('downside').value || !Number.isFinite(gain + loss) || gain < 0 || loss < 0 || gain + loss === 0) {
    get('decision-output').textContent = 'Enter nonnegative payoffs, with at least one above zero.'; return;
  }
  const expected = probability * gain - (1 - probability) * loss;
  get('decision-output').textContent = `Break-even probability: ${percent(loss / (gain + loss))}. Under this estimate, expected net payoff is ${expected.toLocaleString('en', { maximumFractionDigits: 2 })} in your chosen units, before other costs.`;
}
get('upside').addEventListener('input', payoff); get('downside').addEventListener('input', payoff);
function showResult(p, question, method, label) {
  probability = p; get('probability').textContent = percent(p); get('question').textContent = question;
  get('method').textContent = method; get('result-label').textContent = label; panels('result'); payoff();
}
function showError(message, cost = '') {
  get('error').textContent = message; get('error-cost').textContent = cost;
  get('download-failure').hidden = !record; get('result-label').textContent = 'No probability substituted'; panels('error-panel');
}
function download() {
  if (!record) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2) + '\n'], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'vaticinus-forecast-record.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
get('download').addEventListener('click', download); get('download-failure').addEventListener('click', download);
get('copy-result').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(`${get('question').textContent}\nProbability: ${get('probability').textContent}\n${get('method').textContent}\nMade with Vaticinus: https://vaticinus.com`); get('copy-status').textContent = 'Summary copied.'; }
  catch { get('copy-status').textContent = 'Clipboard unavailable. Use Download record instead.'; }
});
function resetLab() {
  const template = templates[get('example').value];
  get('lab-title').textContent = template.title; get('lab-description').textContent = template.description;
  get('lab-fields').replaceChildren();
  for (const [id, label, value, min, max, step] of template.fields) {
    const wrapper = document.createElement('div'), caption = document.createElement('label'), input = document.createElement('input');
    caption.htmlFor = `lab-${id}`; caption.textContent = label;
    Object.assign(input, { id: `lab-${id}`, type: 'number', value: String(value), min: String(min), max: String(max), step: String(step), required: true });
    input.addEventListener('input', updateLab); wrapper.append(caption, input); get('lab-fields').append(wrapper);
  }
  get('spec').value = JSON.stringify(template.spec, null, 2); invalidate();
}
function updateLab() {
  const kind = get('example').value, spec = structuredClone(templates[kind].spec);
  const value = id => Number(get(`lab-${id}`).value);
  if (kind === 'conditional') { spec.branches[0].weight = value('weight') / 100; spec.branches[1].weight = 1 - spec.branches[0].weight; spec.branches[0].p_yes = value('yesA') / 100; spec.branches[1].p_yes = value('yesB') / 100; }
  if (kind === 'bayes') { spec.prior = value('prior') / 100; spec.likelihood_yes = value('signalYes') / 100; spec.likelihood_no = value('signalNo') / 100; }
  if (kind === 'normal') { spec.mean = value('mean'); spec.sd = value('sd'); spec.threshold = value('threshold'); spec.question = `Will final capacity reach at least ${spec.threshold} MW?`; }
  if (kind === 'binary') spec.p_yes = value('judgment') / 100;
  get('spec').value = JSON.stringify(spec, null, 2); invalidate();
}
get('example').addEventListener('change', resetLab); get('reset').addEventListener('click', resetLab);
get('spec').addEventListener('input', invalidate);
async function calculate(event) {
  event?.preventDefault(); invalidate(); const current = revision;
  get('compute').disabled = true;
  try {
    const spec = JSON.parse(get('spec').value);
    const response = await fetch('/api/compute', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(spec), signal: AbortSignal.timeout(20000) });
    const data = await response.json(); if (current !== revision) return;
    if (!response.ok) throw new Error(data.error || 'The model could not be computed.');
    if (!data.result.ok || typeof data.result.probability !== 'number') throw new Error(data.result.reason || data.result.error || 'These assumptions do not identify a valid probability.');
    record = data.snapshot; showResult(data.result.probability, spec.question, data.result.method, 'Synthetic scenario');
    metadata([['Model', spec.kind], ['Calculated', new Date(data.snapshot.computed_forecast.computed_at).toLocaleString()], ['Model cost', '$0.00']]);
    list('assumptions', 'assumptions-section', spec.kind === 'conditional' ? spec.branches.map(branch => `${branch.condition}: ${percent(branch.weight)} weight × ${percent(branch.p_yes)} event chance`) : spec.assumptions);
    list('cruxes', 'cruxes-section', []); get('explanation-section').hidden = true;
    get('details').textContent = JSON.stringify(data.snapshot, null, 2);
  } catch (error) { if (current === revision) showError(error.message); }
  finally { get('compute').disabled = false; }
}
get('model-form').addEventListener('submit', calculate);
get('try-example').addEventListener('click', () => { setMode('lab'); get('example').value = 'conditional'; resetLab(); calculate(); });
function pricing() {
  const model = availableModels.find(item => item.id === get('model').value);
  get('model-pricing').textContent = model ? `Current token caps: $${model.rates.input}/M input · $${model.rates.output}/M output. Uncertain charges stay reserved. Set an account limit in OpenRouter too.` : 'No supported model available. The free scenario lab still works.';
}
async function loadModels() {
  get('model').disabled = true; get('forecast-submit').disabled = true; get('reload-models').hidden = true;
  try {
    const response = await fetch('/api/models', { signal: AbortSignal.timeout(25000) });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Model discovery unavailable.');
    availableModels = data.models; get('model').replaceChildren();
    for (const model of availableModels) { const option = document.createElement('option'); option.value = model.id; option.textContent = model.name; get('model').append(option); }
    if (!availableModels.length) throw new Error('No supported model is currently available.');
    get('model').disabled = false; get('forecast-submit').disabled = false; pricing();
  } catch (error) { get('model-pricing').textContent = `${error.message} You can still use the free scenario lab.`; get('reload-models').hidden = false; }
}
get('reload-models').addEventListener('click', loadModels); get('model').addEventListener('change', pricing);
get('forget-key').addEventListener('click', () => { get('api-key').value = ''; invalidate(); get('api-key').focus(); });
window.addEventListener('pagehide', () => { get('api-key').value = ''; active?.abort(); });
get('resolution-date').min = deadline(1);
for (const field of get('forecast-form').querySelectorAll('input, textarea, select')) field.addEventListener('input', invalidate);
const starters = {
  launch: ['Will our product reach 1,000 paying customers by the deadline?', 'YES if the billing system records at least 1,000 active paying customers at 23:59 UTC on the resolution date. Exclude free trials and cancelled accounts.'],
  policy: ['Will the central bank announce a lower policy rate by the deadline?', 'Name the central bank, current comparison rate and official release. Specify whether any cut by the deadline or only a particular meeting counts.'],
  research: ['Will the research milestone pass external validation by the deadline?', 'Name the milestone, independent evaluator and published acceptance criteria. Specify how delays or unavailable results resolve.'],
};
for (const button of document.querySelectorAll('[data-prompt]')) button.addEventListener('click', () => {
  const [question, rule] = starters[button.dataset.prompt]; get('forecast-question').value = question; get('resolution-rule').value = rule; get('resolution-date').value = deadline(90); invalidate(); get('evidence').focus();
});
get('cancel').addEventListener('click', () => active?.abort());
get('forecast-form').addEventListener('submit', async event => {
  event.preventDefault(); invalidate(); const current = revision, controller = new AbortController(); active = controller;
  const started = Date.now(); let timer;
  get('forecast-submit').disabled = true; get('cancel').hidden = false; panels('loading');
  const arm = get('forecast-arm').value;
  const status = () => { get('loading-status').textContent = `${Math.floor((Date.now() - started) / 1000)}s elapsed. ${arm === 'harness' ? 'Generating and reviewing with your selected model.' : 'Waiting for one bounded model response.'}`; };
  status(); timer = setInterval(status, 1000);
  try {
    const response = await fetch('/api/forecast', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
      body: JSON.stringify({ question: get('forecast-question').value, resolutionDate: get('resolution-date').value, resolutionRule: get('resolution-rule').value,
        evidence: get('evidence').value, apiKey: get('api-key').value.trim(), model: get('model').value, arm, maxCost: Number(get('max-cost').value) }) });
    const data = await response.json(); if (current !== revision) return;
    if (!response.ok) throw new Error(data.error || 'Forecast request failed.');
    record = data; const { run, cost } = data;
    const costText = `${dollars(cost.accounted_usd)} accounted across ${cost.calls} model call(s). ${cost.unknown_charge_calls ? 'Some charges are uncertain and remain reserved.' : 'Provider usage received.'}`;
    if (run.status !== 'issued' || typeof run.probability !== 'number') { showError(run.issues.join('\n') || `This attempt was ${run.status}. No forecast probability was substituted.`, costText); return; }
    showResult(run.probability, run.question.question, 'A model estimate from your supplied context. Check the evidence and assumptions before using it.', arm === 'harness' ? 'AI estimate · model-reviewed' : 'AI estimate · direct');
    metadata([['Settles', run.question.resolution_date], ['Model', run.model], ['Accounted cost', dollars(cost.accounted_usd)], ['Evidence', 'User-supplied; not independently verified']]);
    list('assumptions', 'assumptions-section', run.spec?.assumptions);
    list('cruxes', 'cruxes-section', run.spec?.kill_criteria);
    get('explanation').textContent = run.answer; get('explanation-section').hidden = false;
    get('details').textContent = JSON.stringify({ spec: run.spec, resolution_rule: run.question.dated_metric, cost }, null, 2);
  } catch (error) {
    if (current === revision) showError(error.name === 'AbortError' ? 'Request stopped. Work already processed may still be charged. Check your OpenRouter activity before running again.' : error.message, 'If the connection failed after processing began, the final charge may be unknown. No automatic retry was made.');
  } finally {
    clearInterval(timer);
    if (active === controller) { active = null; get('cancel').hidden = true; get('forecast-submit').disabled = availableModels.length === 0; document.querySelector('.result-panel').setAttribute('aria-busy', 'false'); }
  }
});
resetLab(); loadModels();
