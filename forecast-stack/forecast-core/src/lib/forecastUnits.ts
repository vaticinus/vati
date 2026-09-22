/** Explicit multiplicative units. Unknown labels can only convert to the same label;
 * currency conversion and ambiguous units (for example "tons") are never guessed. */
export type Unit = { dimension: string; scale: number; kind: 'count' | 'fraction' | 'positive' };
const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, ' ');
const atom = (dimension: string, scale = 1, kind: Unit['kind'] = 'positive'): Unit => ({dimension, scale, kind});
const known: Record<string, Unit> = {
  w: atom('power'), kw: atom('power', 1e3), mw: atom('power', 1e6), gw: atom('power', 1e9), tw: atom('power', 1e12),
  watt: atom('power'), watts: atom('power'), kilowatts: atom('power', 1e3), megawatts: atom('power', 1e6), gigawatts: atom('power', 1e9),
  wh: atom('energy'), kwh: atom('energy', 1e3), mwh: atom('energy', 1e6), gwh: atom('energy', 1e9), twh: atom('energy', 1e12),
  mg: atom('mass', 1e-6), g: atom('mass', .001), kg: atom('mass'), kilogram: atom('mass'), kilograms: atom('mass'),
  tonne: atom('mass', 1e3), tonnes: atom('mass', 1e3), 'metric ton': atom('mass', 1e3), 'metric tons': atom('mass', 1e3),
  '%': atom('fraction', .01, 'fraction'), percent: atom('fraction', .01, 'fraction'), percentage: atom('fraction', .01, 'fraction'),
  fraction: atom('fraction', 1, 'fraction'), proportion: atom('fraction', 1, 'fraction'), share: atom('fraction', 1, 'fraction'), probability: atom('fraction', 1, 'fraction'),
  usd: atom('currency:usd'), '$': atom('currency:usd'), eur: atom('currency:eur'), '€': atom('currency:eur'), gbp: atom('currency:gbp'), '£': atom('currency:gbp'),
  second: atom('time'), seconds: atom('time'), minute: atom('time', 60), minutes: atom('time', 60),
  hour: atom('time', 3600), hours: atom('time', 3600), day: atom('time', 86400), days: atom('time', 86400), weeks: atom('time', 604800),
};
const counts: Record<string, string> = {count:'count', counts:'count', number:'count', unit:'unit', units:'unit', person:'person', people:'person', user:'user', users:'user', seat:'seat', seats:'seat', award:'award', awards:'award', paper:'paper', papers:'paper', patent:'patent', patents:'patent', shipment:'shipment', shipments:'shipment', install:'install', installs:'install', deployment:'deployment', deployments:'deployment', robot:'robot', robots:'robot'};

export function forecastUnit(label: string): Unit {
  // SI symbol case matters: mW is a milliwatt, MW is a megawatt.
  const symbol = label.trim();
  if (symbol === 'mW') return atom('power', .001);
  if (symbol === 'mWh') return atom('energy', .001);
  if (symbol === 'Mg') return atom('mass', 1e3);
  const key = norm(label);
  if (known[key]) return known[key];
  if (counts[key]) return atom(`count:${counts[key]}`, 1, 'count');
  const scaled = /^(thousand|million|billion) (.+)$/i.exec(symbol);
  if (scaled) {
    const u = forecastUnit(scaled[2]);
    const multipliers: Record<string, number> = {thousand: 1e3, million: 1e6, billion: 1e9};
    return {...u, scale: u.scale * multipliers[scaled[1].toLowerCase()]};
  }
  const ratio = symbol.split(/\s*\/\s*|\s+per\s+/i);
  if (ratio.length === 2 && ratio.every(Boolean)) {
    const a = forecastUnit(ratio[0]), b = forecastUnit(ratio[1]);
    return atom(`${a.dimension}/${b.dimension}`, a.scale / b.scale);
  }
  return atom(`custom:${key}`);
}

export function convertForecastValue(value: number, from: string, to: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('unit conversion requires a finite number');
  const a = forecastUnit(from), b = forecastUnit(to);
  if (a.dimension !== b.dimension) throw new Error(`incompatible units: ${from || '(unspecified)'} and ${to || '(unspecified)'}`);
  const result = value * (a.scale / b.scale);
  if (!Number.isFinite(result)) throw new Error('unit conversion overflow');
  return result;
}
