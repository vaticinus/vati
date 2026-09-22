// A well-formed forecast is a candidate for manual resolution. Source prose is not a
// resolver, and no automatic resolver is registered for generic chat cards. Keep the
// candidate checks aligned with engine/experimental_p/ingest_cards.py.
export type NeedleCardLike = {
  kind?: string;
  resolution_date?: string | null;
  dated_metric?: string | null;
  threshold?: number | null;
  threshold_dir?: string | null;
  probability?: number | null;
};

export type NeedleVerdict = {
  // Reserved for a server-verified outcome/resolver contract. Never inferred from model text.
  scorable: false;
  structured: boolean;
  reason: string;
};

const VAGUE_METRIC = /^(n\/?a|tbd|to be determined|unknown|various|multiple( sources)?|the market|market data|news|reports?|analysts?|sources?|public data|official data)\.?$/i;

export function validResolutionDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value;
}

/** Assess structure only. A future date, source URL, resolver name or claimed outcome supplied
 * by the model cannot make a card automatically scorable. Manual review remains available. */
export function needleVerdict(card: NeedleCardLike, now: Date = new Date()): NeedleVerdict {
  const no = (reason: string): NeedleVerdict => ({scorable: false, structured: false, reason});
  if (!card || typeof card !== 'object') return no('no forecast');
  const rd = typeof card.resolution_date === 'string' ? card.resolution_date.trim() : '';
  if (!validResolutionDate(rd)) return no('no valid dated resolution');
  if (rd <= now.toISOString().slice(0, 10)) return no('resolution date is not in the future');
  const metric = typeof card.dated_metric === 'string' ? card.dated_metric.trim() : '';
  if (metric.length < 8 || VAGUE_METRIC.test(metric)) return no('no proposed resolution source');
  if (card.kind !== 'binary' && card.kind !== 'conditional' && card.kind !== 'bayes') {
    if (typeof card.threshold !== 'number' || !Number.isFinite(card.threshold)) return no('no numeric threshold');
    if (!['>=', '>', '<=', '<'].includes(card.threshold_dir ?? '')) return no('no threshold direction');
  }
  if (typeof card.probability !== 'number' || !Number.isFinite(card.probability) || card.probability < 0 || card.probability > 1) return no('probability must be between 0 and 1');
  return {scorable: false, structured: true, reason: 'manual resolution required; source and outcome are unverified'};
}
