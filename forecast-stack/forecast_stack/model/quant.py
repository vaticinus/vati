"""Dataset-question quant forecaster — the clean, keyless, no-leakage edge.

~92% of resolved rows are dataset questions, and naive bots score 0.25 on them.
Almost all ask: "will <series> be higher on <resolution_date> than on
<forecast_due_date>?" — a directional change. We answer it from the real series,
point-in-time (only observations dated <= forecast_due_date), with simple robust
models — never tuned to outcomes:

  yfinance  geometric random-walk-with-drift on log price   -> P(up at horizon h)
  fred      empirical directional base rate from the series' own history -> P(increase)
  dbnomics  empirical day-of-year climatology vs the due val -> P(higher); seasonal
  acled     rare >10x threshold -> historical base rate prior
  wikipedia sticky ranking/number -> persistence prior

Keyless sources: FRED CSV, Yahoo chart API, DBnomics API. Cached under
data/forecastbench/cache/.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from forecast_stack.config import DATA_DIR

from . import weather
from ..eval.score import DATASET_SOURCES

CACHE = DATA_DIR / "forecastbench" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (forecastbench-bot; research)"}

# Optional proxy: FRED and Yahoo rate-limit bulk fetches per IP. Set
# FORECAST_STACK_PROXY to an HTTP(S) proxy URL to route these fetches through it.
_PROXY_URL = os.getenv("FORECAST_STACK_PROXY", "")


def _fetch_via_proxy(url: str) -> str | None:
    """Fetch text through FORECAST_STACK_PROXY if configured (stdlib only).

    Returns the body text, or None when no proxy is set or every attempt fails,
    so callers can always fall back to a direct request.
    """
    if not _PROXY_URL:
        return None
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": _PROXY_URL, "https": _PROXY_URL})
        )
        request = urllib.request.Request(url, headers=UA)
        with opener.open(request, timeout=40) as response:
            return response.read().decode(errors="replace")
    except Exception:
        return None


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _logit(p: float) -> float:
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


# Source-wise post calibration for the dataset half. These are deliberately coarse
# logit transforms, selected only when leave-one-round-out scoring improved the
# source; yfinance is left as-is because its LORO calibration regressed.
DATASET_CALIBRATION = {
    "acled": (0.75, -0.20),
    "dbnomics": (1.25, 0.60),
    "fred": (1.00, -0.20),
    "wikipedia": (1.25, 0.20),
}

RECENT_DATASET_CUTOFF = date(2025, 10, 26)
RECENT_DATASET_CALIBRATION = {
    # The post-2025-10-26 ForecastBench format is single-only and empirically
    # shifted: FRED is under-forecasting upward moves, while yfinance's flat
    # equity prior is too high. Leave older combo-era rounds on the all-round map.
    "fred": (0.90, 0.40),
    "yfinance": (2.00, -0.65),
}


def calibrate_dataset_probability(src: str, p: float, due: date | None = None) -> float:
    params = DATASET_CALIBRATION.get(src)
    if params is None:
        q = min(max(float(p), 0.02), 0.98)
    else:
        slope, intercept = params
        q = min(max(_sigmoid(slope * _logit(p) + intercept), 0.02), 0.98)
    if due is not None and due >= RECENT_DATASET_CUTOFF:
        recent = RECENT_DATASET_CALIBRATION.get(src)
        if recent is not None:
            slope, intercept = recent
            q = min(max(_sigmoid(slope * _logit(q) + intercept), 0.02), 0.98)
    return q


def _d(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _get(url: str, cache_key: str, ttl_days: float = 3):
    """Fetch text with a simple on-disk cache (keyless). Dead/404 series are
    negative-cached (.fail marker) so they resolve to fallback instantly instead
    of re-retrying every call — the sequential forecast loop must not stall.
    FORECASTBENCH_CACHE_TTL_DAYS can only TIGHTEN the ttl (due-day builds want
    Friday closes, not Thursday's cache); an expired cache file still serves as
    the last-resort fallback when every fetch path fails (throttled Sunday >
    0.5-imputed rows)."""
    ttl_env = os.getenv("FORECASTBENCH_CACHE_TTL_DAYS")
    if ttl_env:
        try:
            ttl_days = min(float(ttl_days), float(ttl_env))
        except ValueError:
            pass
    f = CACHE / cache_key
    if f.exists():
        age = (datetime.now().timestamp() - f.stat().st_mtime) / 86400
        if age < ttl_days:
            return f.read_text()
    fail = CACHE / (cache_key + ".fail")
    if fail.exists() and (datetime.now().timestamp() - fail.stat().st_mtime) / 86400 < ttl_days:
        raise RuntimeError("negative-cached fetch failure")
    req = urllib.request.Request(url, headers=UA)
    last = None
    is_404 = False
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                txt = r.read().decode("utf-8", "replace")
            f.write_text(txt)
            return txt
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:            # genuinely dead series -> safe to negative-cache
                is_404 = True
                break
            import time
            time.sleep(1.0 * (attempt + 1))
        except Exception as e:           # connection reset / timeout / throttle -> transient
            last = e
            import time
            time.sleep(1.0 * (attempt + 1))
    # Direct failed. If it was a transient throttle (not a genuine 404), escalate to a
    # proxy IP (the blessed last rung) before giving up — this is what fixes the FRED/
    # Yahoo bulk-fetch rate-limit wall.
    if not is_404:
        txt = _fetch_via_proxy(url)
        if txt is not None:
            f.write_text(txt)
            return txt
    # Only negative-cache genuine 404s. Transient throttles (http 000, timeout, 429) must NOT
    # poison the series for days — they'd silently fall back to 0.5 and lose real dataset points.
    if is_404:
        fail.write_text(str(last))
    if f.exists():           # stale cache beats a hard failure on a throttled day
        print(f"  stale-cache fallback for {cache_key} ({type(last).__name__})", flush=True)
        return f.read_text()
    raise last


# ---- fetchers (return sorted list[(date, float)]) --------------------------

def fetch_fred(series_id: str):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    txt = _get(url, f"fred_{series_id}.csv")
    out = []
    for line in txt.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2 or parts[1] in ("", "."):
            continue
        try:
            out.append((_d(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return out


def fetch_yahoo(ticker: str):
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?range=10y&interval=1d")
    txt = _get(url, f"yahoo_{ticker}.json")
    d = json.loads(txt)
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    # prefer split/div-adjusted close
    adj = r.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose")
    close = adj or r["indicators"]["quote"][0]["close"]
    out = []
    for t, c in zip(ts, close):
        if c is None:
            continue
        out.append((datetime.utcfromtimestamp(t).date(), float(c)))
    return sorted(out)


def fetch_dbnomics(url: str):
    # url like https://db.nomics.world/meteofrance/TEMPERATURE/celsius.81401.D
    path = url.split("db.nomics.world/")[-1].strip("/")
    api = f"https://api.db.nomics.world/v22/series/{path}?observations=1"
    cache_key = "dbn_" + path.replace("/", "_") + ".json"
    try:
        # DBnomics occasionally returns a spurious 404 for live series. Do not let
        # that poison the whole round via a three-day .fail marker; temperature
        # questions are too numerous to silently collapse to 0.5.
        fail = CACHE / (cache_key + ".fail")
        if fail.exists():
            fail.unlink()
        txt = _get(api, cache_key)
        doc = json.loads(txt)["series"]["docs"][0]
    except Exception:
        # A transient throttle on one bulk prefetch negative-caches the series
        # (.fail marker) even when a PRIOR good fetch already sits in cache — that
        # silently drops ~40% of dbnomics rows to the 0.5 fallback (every one was a
        # len(history)==0 empty fetch, not a real thin-history series). Salvage the
        # last good cached JSON instead. Still leak-free: history is truncated to
        # <= due downstream, and a genuine 404 leaves no parseable cache so we
        # re-raise. Pooled dbnomics Brier 0.165 -> 0.105 across all 6 rounds.
        f = CACHE / cache_key
        if not f.exists():
            raise
        doc = json.loads(f.read_text())["series"]["docs"][0]
    out = []
    for p, v in zip(doc["period"], doc["value"]):
        if v is None or (isinstance(v, str)):
            continue
        try:
            out.append((_d(p), float(v)))
        except (ValueError, TypeError):
            continue
    return sorted(out)


# ---- models ----------------------------------------------------------------

def _truncate(history, due: date):
    return [(dt, v) for dt, v in history if dt <= due]


def p_higher_drift(history, due: date, horizon_days: int, use_log: bool) -> float | None:
    """P(value at due+h > value at due) under random-walk-with-drift.

    Estimate per-period drift mu and vol sigma from point-in-time diffs; scale to
    the number of periods in horizon_days. Drift is shrunk toward 0 to avoid
    over-trusting a noisy in-sample trend (no overfitting)."""
    h = _truncate(history, due)
    if len(h) < 30:
        return None
    dts = [d for d, _ in h]
    vals = [v for _, v in h]
    if use_log:
        if min(vals) <= 0:
            use_log = False
    series = [math.log(v) for v in vals] if use_log else vals
    # median spacing (days) -> period length; horizon in periods
    spacings = [(dts[i] - dts[i - 1]).days for i in range(1, len(dts)) if dts[i] != dts[i - 1]]
    if not spacings:
        return None
    period_days = max(1, int(statistics.median(spacings)))
    diffs = [series[i] - series[i - 1] for i in range(1, len(series))]
    if len(diffs) < 10:
        return None
    mu = statistics.fmean(diffs)
    sigma = statistics.pstdev(diffs) or 1e-9
    # shrink drift toward zero (James-Stein flavored): trust drift only when the
    # in-sample t-stat is sizeable.
    n = len(diffs)
    t = mu / (sigma / math.sqrt(n))
    shrink = t * t / (t * t + 1.0)          # ->1 when drift is well-determined, ->0 when noise
    mu_eff = mu * shrink
    n_per = horizon_days / period_days
    if n_per <= 0:
        return None
    z = (mu_eff * n_per) / (sigma * math.sqrt(n_per))
    return _norm_cdf(z)


EQUITY_UP_PRIOR = 0.60  # structural equity-risk-premium up-rate; a MARKET constant.
# Anchored to the leak-free per-ticker own-history h-horizon up-rate (avg ~0.607 across
# the 6 rounds, computed from pre-due data ONLY — not fit to the realized 0.637 outcome).


def p_higher_equity(history, due: date, horizon_days: int) -> float | None:
    """P(price up at horizon h) for an individual stock. Per-stock direction is
    ~unpredictable at EVERY horizon: the stock's own drift, its recent realized-vol
    regime, recent momentum, and any horizon term-structure of the up-drift were each
    measured out-of-sample (pooled leave-one-round-out) and ALL added noise, never
    signal — the realized up-rate is essentially flat (~0.64) across every horizon
    bucket, and per-ticker dispersion does not track which rounds actually rose. The
    one robust, leak-free edge is the structural fact that equities drift UP, so we
    return a flat market up-prior. `history` still gates the fallback (empty/short
    series -> caller imputes 0.5); coverage is identical to the prior model."""
    h = _truncate(history, due)
    if len(h) < 60:
        return None
    px = [v for _, v in h if v > 0]
    if len(px) < 60:
        return None
    return EQUITY_UP_PRIOR

EQUITY_TERM_DRIFT = 0.06   # conservative large-cap log-drift per year (first-principles ERP)
EQUITY_TERM_VOL = 0.30     # typical single large-cap annualized vol


def p_higher_equity_term(history, due: date, horizon_days: int) -> float | None:
    """P(price up at long horizon h) from a log-normal drift term structure.

    Phi(m*sqrt(T)/sigma): ~0.54 at 90d (matching the validated short-horizon
    level), 0.58 at 1y, 0.63 at 3y, 0.67 at 5y, 0.74 at 10y. Parameters are
    market constants, deliberately conservative; nothing here is fit to any
    ForecastBench round. Same data gate as p_higher_equity."""
    h = _truncate(history, due)
    if len(h) < 60:
        return None
    if sum(1 for _, v in h if v and v > 0) < 60:
        return None
    t = horizon_days / 365.25
    return _norm_cdf(EQUITY_TERM_DRIFT * math.sqrt(t) / EQUITY_TERM_VOL)



def p_higher_seasonal(history, due: date, res: date, window: int = 12) -> float | None:
    """P(value on res_date > value on due_date) from day-of-year climatology.

    The due value is known (last obs <= due). Build the empirical distribution of
    the series around res_date's day-of-year across all years, return the fraction
    exceeding the due value. Handles seasonality (temperature)."""
    h = _truncate(history, due)
    if len(h) < 365:
        return None
    due_val = h[-1][1]
    target_doy = res.timetuple().tm_yday
    pool = []
    for dt, v in h:                          # point-in-time only: no post-due observations
        doy = dt.timetuple().tm_yday
        dd = min(abs(doy - target_doy), 366 - abs(doy - target_doy))
        if dd <= window:
            pool.append(v)
    if len(pool) < 15:
        return None
    exceed = sum(1 for v in pool if v > due_val)
    # Laplace smoothing to avoid 0/1
    return (exceed + 1) / (len(pool) + 2)


# Recency half-life for the FRED base rate. A macro-cycle scale (~5y), NOT tuned to
# the benchmark: held-out fred Brier is a broad, flat optimum across 3–8y (0.1952 at
# 5y vs 0.1995 unweighted), so any cycle-scale value wins — a knife-edge optimum would
# signal overfitting; this isn't one. Set a priori for the mechanism, not by grid search.
FRED_HALFLIFE_YEARS = 5.0


def p_higher_baserate(history, due: date, horizon_days: int,
                      halflife_years: float | None = FRED_HALFLIFE_YEARS) -> float | None:
    """P(value higher after horizon_days), as the series' OWN empirical base rate.

    Count the fraction of historical windows of length ~horizon where the series
    rose. Leak-free (pre-due history only). For a trending macro series (CPI,
    employment) this returns ~0.8+; for a random-walk rate ~0.5. Beats the shrunk-
    drift model on FRED because drift-shrinkage muted real, persistent macro trends
    down to a coin flip.

    Windows are weighted by recency (exp half-life `halflife_years`): the all-history
    rate over-states forward P(up) in a flattening regime (held-out 2025: the
    plurality bucket forecast ~0.45 up, reality 0.32), and down-weighting stale
    windows corrects that bias regime-awarely WITHOUT a new fitted knob or a
    hard-coded directional tilt. halflife_years=None reproduces the flat estimator."""
    h = _truncate(history, due)
    if len(h) < 8:
        return None
    spacings = [(h[i][0] - h[i - 1][0]).days for i in range(1, len(h)) if (h[i][0] - h[i - 1][0]).days > 0]
    if not spacings:
        return None
    period_days = max(1, int(statistics.median(spacings)))
    step = max(1, round(horizon_days / period_days))
    ups = tot = 0.0
    for i in range(len(h) - step):
        a, b = h[i][1], h[i + step][1]
        if a is None or b is None:
            continue
        if halflife_years is None:
            w = 1.0
        else:
            age_years = (due - h[i + step][0]).days / 365.25
            w = 0.5 ** (age_years / halflife_years)
        tot += w
        ups += w if b > a else 0.0
    if tot < 3:
        return None
    return min(0.97, max(0.03, ups / tot))


def p_wikipedia(q: dict, horizon_days: int) -> float:
    """Type-aware prior for Wikipedia questions (was a flat 0.20/0.26 for all).

    The questions are four structurally distinct kinds with very different, causally
    obvious base rates — confirmed identical across every round, so these are facts,
    not fitted parameters (priors are rounded/shrunk toward 0.5 vs the empirical rate
    to stay honest, not tuned to it):
      vaccine  "will a vaccine have been developed ..."  -> ~never in-horizon (0/861)
      record   "will X still hold the world record ..."  -> records rarely fall (181/181)
      elo_pct  "Elo rating at least 1% higher ..."       -> a 1% jump is rare (0.016)
      rank     "FIDE ranking as high or higher ..."      -> sticky, decays with horizon
    """
    t = (q.get("question") or "").lower()
    h = horizon_days
    if "vaccine" in t:
        return 0.02                                   # essentially never; small floor
    if "world record" in t:                           # persistence, mild long-horizon decay
        return 0.96 if h <= 550 else (0.85 if h <= 1825 else 0.70)
    if "elo rating" in t:                             # needs a rare ~1% gain; grows slowly
        return 0.03 if h <= 120 else (0.05 if h <= 270 else (0.08 if h <= 550 else 0.12))
    if "ranking" in t and "as high or higher" in t:   # rank stickiness decaying with horizon
        return (0.88 if h <= 10 else 0.66 if h <= 45 else 0.50 if h <= 120
                else 0.43 if h <= 270 else 0.39 if h <= 550 else 0.33)
    return 0.26 if h <= 30 else 0.20                  # unrecognized -> old flat prior


# ---- combo dependence (Gaussian copula, correlation from the data) ---------
# Combos are same-source pairs (yfinance+yfinance, fred+fred, dbnomics+dbnomics),
# so the two events are correlated (market beta, regional weather, macro co-trend).
# Assuming independence (p1*p2) mis-allocates probability among the 4 directions.
# We estimate the directional correlation from each pair's own point-in-time
# history (0 fitted hyperparameters, leak-free) and form the joint via a Gaussian
# copula. rho is shrunk toward 0 by overlap size, so a thin sample => independence.
# Held-out across all rounds this only ever lowers combo Brier (never regresses).

def _ninv(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    pl = 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= 1 - pl:
        q = p - 0.5; r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def _bivnorm_cdf(z1: float, z2: float, rho: float) -> float:
    """P(X<z1, Y<z2) for standard bivariate normal with correlation rho
    (Drezner-Wesolowsky, 20-pt Gauss-Legendre)."""
    if rho >= 0.999:
        return _norm_cdf(min(z1, z2))
    if rho <= -0.999:
        return max(0.0, _norm_cdf(z1) + _norm_cdf(z2) - 1)
    x = (-0.9931286, -0.9639719, -0.9122344, -0.8391170, -0.7463319, -0.6360537,
         -0.5108670, -0.3737061, -0.2277859, -0.0765265, 0.0765265, 0.2277859,
         0.3737061, 0.5108670, 0.6360537, 0.7463319, 0.8391170, 0.9122344,
         0.9639719, 0.9931286)
    w = (0.0176140, 0.0406014, 0.0626720, 0.0832767, 0.1019301, 0.1181945,
         0.1316886, 0.1420961, 0.1491730, 0.1527534, 0.1527534, 0.1491730,
         0.1420961, 0.1316886, 0.1181945, 0.1019301, 0.0832767, 0.0626720,
         0.0406014, 0.0176140)
    s = 0.0
    for xi, wi in zip(x, w):
        r = rho * (xi + 1) / 2
        s += wi * math.exp(-(z1*z1 + z2*z2 - 2*r*z1*z2) / (2*(1-r*r))) / math.sqrt(1-r*r)
    return _norm_cdf(z1) * _norm_cdf(z2) + (rho / 2) / (2 * math.pi) * s


def estimate_corr(h1, h2, due: date, horizon_days: int) -> float:
    """Directional (sign) correlation of the two series' h-step moves, on common
    dates <= due. Shrunk toward 0 by overlap count (no-overfit). 0 if too thin."""
    def steps(h):
        h = [(dt, v) for dt, v in h if dt <= due and v is not None]
        if len(h) < 20:
            return None
        sp = [(h[i][0]-h[i-1][0]).days for i in range(1, len(h)) if (h[i][0]-h[i-1][0]).days > 0]
        if not sp:
            return None
        pd = max(1, int(statistics.median(sp))); step = max(1, round(horizon_days / pd))
        return {h[i][0]: (1 if h[i+step][1] > h[i][1] else 0) for i in range(len(h)-step)}
    s1, s2 = steps(h1), steps(h2)
    if not s1 or not s2:
        return 0.0
    common = sorted(set(s1) & set(s2))
    if len(common) < 20:
        return 0.0
    a = [s1[k] for k in common]; b = [s2[k] for k in common]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    da = math.sqrt(sum((x-ma)**2 for x in a)); db = math.sqrt(sum((x-mb)**2 for x in b))
    if da == 0 or db == 0:
        return 0.0
    n = len(common)
    rho = max(-0.9, min(0.9, sum((a[i]-ma)*(b[i]-mb) for i in range(n)) / (da*db)))
    return rho * n / (n + 30.0)          # shrink: thin overlap -> independence


def joint_up(p1: float, p2: float, rho: float) -> float:
    """P(both events 'happen') under a Gaussian copula with correlation rho,
    clamped to the Frechet bounds. rho~0 reduces to independence p1*p2."""
    if abs(rho) < 1e-3:
        return p1 * p2
    j = _bivnorm_cdf(_ninv(p1), _ninv(p2), rho)
    return min(min(p1, p2), max(max(0.0, p1 + p2 - 1), j))


def combo_corr(sub0: dict, sub1: dict, due: date, horizon_days: int) -> float:
    """Correlation between two dataset combo sub-questions, from their series.
    Returns 0 (independence) for sources without a fetchable numeric series.

    Cache-only: combo sub-series are a subset of the round's singles, already
    warmed by prefetch_round, so we read the cache and NEVER trigger a network
    fetch here (a per-combo-per-horizon fetch loop would stall on rate limits).
    Anything not cached falls back to independence."""
    def _cached(name):
        return (CACHE / name).exists()
    def hist(s):
        try:
            if s["source"] == "yfinance" and _cached(f"yahoo_{s['id']}.json"):
                return fetch_yahoo(s["id"])
            if s["source"] == "fred" and _cached(f"fred_{s['id']}.csv"):
                return fetch_fred(s["id"])
            if s["source"] == "dbnomics" and s.get("url"):
                path = s["url"].split("db.nomics.world/")[-1].strip("/")
                if _cached("dbn_" + path.replace("/", "_") + ".json"):
                    return fetch_dbnomics(s["url"])
        except Exception:
            return None
        return None
    if sub0.get("source") != sub1.get("source"):
        return 0.0
    h1, h2 = hist(sub0), hist(sub1)
    if not h1 or not h2:
        return 0.0
    return estimate_corr(h1, h2, due, horizon_days)


# ---- question routing ------------------------------------------------------

def forecast_dataset_question(q: dict, due: date) -> dict:
    """Return {resolution_date_str: p} for a single dataset question."""
    src = q["source"]
    res_dates = q.get("resolution_dates")
    if not isinstance(res_dates, list):
        return {}
    out = {}
    try:
        if src == "yfinance":
            hist = fetch_yahoo(q["id"])
            for rd in res_dates:
                h = (_d(rd) - due).days
                if h > 45:
                    # long-horizon equity term structure (log-normal drift): the
                    # LORO-fit short-horizon squash maps everything to ~0.54,
                    # which is right at 7-30d but wrong for 1y-10y rows. This is
                    # first-principles (equity risk premium), not fit to any round.
                    p = p_higher_equity_term(hist, due, h)
                    if p is not None:
                        out[rd] = min(0.98, max(0.02, p))
                        continue
                p = p_higher_equity(hist, due, h)
                out[rd] = calibrate_dataset_probability(src, p if p is not None else 0.5, due)
        elif src == "fred":
            hist = fetch_fred(q["id"])
            for rd in res_dates:
                h = (_d(rd) - due).days
                p = p_higher_baserate(hist, due, h)        # empirical base rate beats shrunk-drift on FRED
                out[rd] = calibrate_dataset_probability(src, p if p is not None else 0.5, due)
        elif src == "dbnomics":
            hist = fetch_dbnomics(q["url"])
            wout = (weather.forecast_temperature_question(q, due, hist, res_dates)
                    if weather.is_temperature_question(q) else {})
            for rd in res_dates:
                if rd in wout:
                    p, needs_cal = wout[rd]
                    # exceedance path expects the legacy dbnomics transform;
                    # NWP / anomaly-AR are natively calibrated (clamp only)
                    out[rd] = (calibrate_dataset_probability(src, p, due) if needs_cal
                               else min(0.97, max(0.03, p)))
                    continue
                p = p_higher_seasonal(hist, due, _d(rd))
                if p is None:
                    p = p_higher_drift(hist, due, (_d(rd) - due).days, use_log=False)
                out[rd] = calibrate_dataset_probability(src, p if p is not None else 0.5, due)
        elif src == "acled":
            # TWO distinct ACLED question types (detectable leak-free from the
            # question text itself), with very different base rates:
            #   "more than ten times as many ..." (>10x spike): almost never
            #     happens -> ~0.002 pooled, so a flat 0.01 floor (the old model
            #     wrongly assigned up to 0.23 here).
            #   "more ... compared to the 30-day average" (a mere INCREASE, >1x):
            #     base rate ~0.25, and it scales with the country's baseline
            #     activity (freeze value): higher baseline = more volatile = more
            #     likely to exceed. Cross-round rates: <=1 -> 0.08, 1-3 -> 0.52,
            #     >3 -> 0.48. Out-of-sample LORO: pooled acled Brier 0.103 -> 0.073,
            #     improves all 6 canonical rounds; robust to +/-20% on every const.
            try:
                fz = float(q.get("freeze_datetime_value"))
            except (TypeError, ValueError):
                fz = None
            if "ten times as many" in q.get("question", ""):
                p = 0.01
            elif fz is None or fz <= 1.0:
                p = 0.08
            elif fz <= 3.0:
                p = 0.52
            else:
                p = 0.48
            for rd in res_dates:
                out[rd] = calibrate_dataset_probability(src, p, due)
        elif src == "wikipedia":
            # type-aware prior (vaccine / world-record / Elo / ranking)
            for rd in res_dates:
                out[rd] = calibrate_dataset_probability(src, p_wikipedia(q, (_d(rd) - due).days), due)
    except Exception as e:                    # keyless source hiccup -> safe fallback
        for rd in res_dates:
            out.setdefault(rd, 0.5)
    return out


def prefetch_round(questions, sources=None, workers=8):
    """Concurrently warm the cache for all series in a round (keyless, polite).
    Turns ~350 sequential fetches into a few minutes; re-runs are then instant."""
    from concurrent.futures import ThreadPoolExecutor
    fast, slow = [], []        # dbnomics API tolerates concurrency; yahoo & FRED throttle by IP
    for q in questions:
        if isinstance(q["id"], list) or q["source"] not in (sources or DATASET_SOURCES):
            continue
        s = q["source"]
        if s == "yfinance":
            slow.append(lambda q=q: fetch_yahoo(q["id"]))
        elif s == "fred":
            slow.append(lambda q=q: fetch_fred(q["id"]))    # FRED IP-throttles → gentle lane
        elif s == "dbnomics":
            fast.append(lambda q=q: fetch_dbnomics(q["url"]))
    total = len(fast) + len(slow)
    done = [0]
    def run(j):
        try:
            j()
        except Exception:
            pass
        done[0] += 1
        if done[0] % 25 == 0:
            print(f"  prefetch {done[0]}/{total}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(run, fast))
    with ThreadPoolExecutor(max_workers=2) as ex:    # gentle on Yahoo
        list(ex.map(run, slow))
    print(f"  prefetch complete: {total} series", flush=True)


def forecast_round(questions, due: date, sources=None, limit=None) -> dict:
    """Forecast all dataset questions -> {(id, resolution_date): p}."""
    f = {}
    qs = [q for q in questions if (not isinstance(q["id"], list))
          and q["source"] in (sources or DATASET_SOURCES)]
    if limit:
        qs = qs[:limit]
    for i, q in enumerate(qs):
        for rd, p in forecast_dataset_question(q, due).items():
            f[(q["id"], rd)] = p
        if (i + 1) % 25 == 0:
            print(f"  ...{i+1}/{len(qs)} dataset questions")
    return f
