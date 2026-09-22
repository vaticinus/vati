const examples = {
  conditional: { kind: 'conditional', question: 'Will the project finish by 2035-12-31?', resolution_date: '2035-12-31', dated_metric: 'Completion entered in the project register by the deadline.', partition: 'Crisis or no crisis', branches: [{ condition: 'Crisis', weight: 0.3, p_yes: 0.6 }, { condition: 'No crisis', weight: 0.7, p_yes: 0.1 }] },
  bayes: { kind: 'bayes', question: 'Is the screened device defective, given a positive result?', prior: 0.01, observation: 'One positive screening result', likelihood_yes: 0.8, likelihood_no: 0.1 },
  normal: { kind: 'normal', question: 'Will final capacity reach at least 110 MW?', mean: 100, sd: 10, threshold: 110, threshold_dir: '>=', ci_unit: 'MW' },
  binary: { kind: 'binary', question: 'Will your precisely defined event occur?', p_yes: 0.35 },
};
const get = (id) => document.getElementById(id);
let snapshot = null;
let revision = 0;
function invalidate() {
  revision += 1;
  snapshot = null;
  get('result').hidden = true;
  get('error').hidden = true;
  get('empty').hidden = false;
}
function reset() {
  get('spec').value = JSON.stringify(examples[get('example').value], null, 2);
  invalidate();
}
get('example').addEventListener('change', reset);
get('reset').addEventListener('click', reset);
get('spec').addEventListener('input', invalidate);
get('model-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  invalidate();
  const requestRevision = revision;
  get('compute').disabled = true;
  get('compute').textContent = 'Computing…';
  document.querySelector('.result-panel').setAttribute('aria-busy', 'true');
  try {
    const spec = JSON.parse(get('spec').value);
    const response = await fetch('/api/compute', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(spec) });
    const data = await response.json();
    if (requestRevision !== revision) return;
    if (!response.ok) throw new Error(data.error || 'Computation failed');
    snapshot = data.snapshot;
    get('probability').textContent = new Intl.NumberFormat('en', { style: 'percent', maximumFractionDigits: 3 }).format(data.result.probability);
    get('question').textContent = typeof spec.question === 'string' ? spec.question : 'Probability of the stated event';
    get('method').textContent = data.result.method;
    get('details').textContent = JSON.stringify(data.result, null, 2);
    get('result').hidden = false;
    get('empty').hidden = true;
  } catch (error) {
    if (requestRevision !== revision) return;
    get('error').textContent = error.message;
    get('error').hidden = false;
    get('empty').hidden = true;
  } finally {
    get('compute').disabled = false;
    get('compute').textContent = 'Compute probability →';
    document.querySelector('.result-panel').setAttribute('aria-busy', 'false');
  }
});
get('download').addEventListener('click', () => {
  if (!snapshot) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot, null, 2) + '\n'], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'forecast-snapshot.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
reset();
