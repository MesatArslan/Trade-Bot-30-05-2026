#!/usr/bin/env python3
"""TREND SCALP SHORT ALARM — Bybit.
   server.py'deki trend_board() SHORT mantiginin bagimsiz (stdlib-only) surumu.
   SU AN dususte kosan (fiyat < EMA9 < EMA21, son 15dk negatif, asiri uzamamis)
   coinleri yakalar ve macOS bildirimi (sag ust kose) atar.
   launchd ile her ~5 dk calisir; durumu .trend_alarm_state.json'da tutar,
   ayni coin icin COOLDOWN suresince tekrar bildirim atmaz."""
import urllib.request, json, time, os, subprocess
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_F = os.path.join(HERE, ".trend_alarm_state.json")
LOG_F   = os.path.join(HERE, "trend_alarm.log")

MIN_TURN   = 5_000_000   # $5M+ 24s hacim (likidite)
UNIVERSE   = 150         # hacme gore ilk N coini kline ile tara
MIN_M15    = 0.004       # son 15dk <= -%0.4 (yonlu, aktif dusus)
MAX_EXT    = 0.05        # fiyat EMA21'den en fazla %5 uzakta (kovalama degil)
COOLDOWN   = 25*60       # ayni coin icin 25 dk icinde tekrar bildirim yok
MAX_ALERTS = 6           # tek seferde en fazla kac coin bildirilsin

BASE = "https://api.bybit.com"


def get(path, params, retries=4):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    for i in range(retries):
        try:
            req = urllib.request.Request(f"{BASE}{path}?{q}",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def notify(title, msg):
    t = json.dumps(title, ensure_ascii=False)
    m = json.dumps(msg, ensure_ascii=False)
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification {m} with title {t} sound name "Ping"'],
                       check=False)
    except Exception:
        pass


def log(line):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_F, "a") as f:
        f.write(f"{ts}  {line}\n")


def load_state():
    try:
        return json.load(open(STATE_F))
    except Exception:
        return {}


def _ema(xs, n):
    if not xs:
        return None
    k = 2.0 / (n + 1)
    e = xs[0]
    for v in xs[1:]:
        e = v * k + e * (1 - k)
    return e


def _kl(sym, interval, limit):
    d = get("/v5/market/kline",
            {"category": "linear", "symbol": sym, "interval": interval, "limit": limit})
    return sorted(d["result"]["list"], key=lambda x: int(x[0]))  # [ts,o,h,l,c,vol,turn]


def scan_short(sym, tov):
    """Bir coin SHORT trend adayi mi? Evetse plan dict, degilse None."""
    try:
        k = _kl(sym, "5", 60)
        hi = [float(x[2]) for x in k]
        lo = [float(x[3]) for x in k]
        cl = [float(x[4]) for x in k]
    except Exception:
        return None
    n = len(cl)
    if n < 30:
        return None
    price = cl[-1]
    ema9 = _ema(cl[-30:], 9)
    ema21 = _ema(cl[-40:], 21) if n >= 40 else _ema(cl, 21)
    ema9_prev = _ema(cl[-33:-3], 9) if n >= 33 else ema9
    if ema9 is None or ema21 is None or ema9_prev is None:
        return None
    m15 = price / cl[-4] - 1 if n >= 4 else 0
    m30 = price / cl[-7] - 1 if n >= 7 else 0
    lo1h = min(lo[-12:]) if n >= 12 else min(lo)
    if not (price < ema9 < ema21 and ema9 < ema9_prev
            and m15 <= -MIN_M15 and m30 < 0):
        return None
    ext = 1 - price / ema21
    near_lo = price <= lo1h * 1.015
    if not (ext <= MAX_EXT and near_lo):
        return None
    trs = [hi[i] - lo[i] for i in range(n - 14, n)]
    atr_abs = sum(trs) / 14
    entry = price
    stop_pct = min(max((atr_abs / entry) if entry else 0, 0.005), 0.02)
    stop = entry * (1 + stop_pct)
    sd = stop - entry
    tp1 = entry - 1.2 * sd
    trend_sep = ema21 / ema9 - 1
    score = min(-m15 * 300, 4) + min(trend_sep * 60, 2)
    return {"symbol": sym, "m15": m15, "entry": entry, "stop": stop,
            "tp1": tp1, "score": round(score, 2), "vol24": int(tov)}


def main():
    state = load_state()
    now = time.time()
    try:
        d = get("/v5/market/tickers", {"category": "linear"})
    except Exception as e:
        log(f"API hata: {e}"); return

    liq = []
    for t in d["result"]["list"]:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t.get("turnover24h") or 0)
        except Exception:
            continue
        if tov < MIN_TURN:
            continue
        liq.append((s, tov))
    liq.sort(key=lambda x: x[1], reverse=True)

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(lambda it: scan_short(*it), liq[:UNIVERSE]))
    found = [r for r in res if r]
    found.sort(key=lambda r: r["score"], reverse=True)

    alerts = []
    new_state = {}
    for r in found:
        s = r["symbol"]
        prev_ts = state.get(s, {}).get("alert_ts", 0)
        if now - prev_ts >= COOLDOWN:
            alerts.append(r)
            new_state[s] = {"alert_ts": now}
        else:
            new_state[s] = {"alert_ts": prev_ts}
    # cooldown bilgisi bir sonraki tur icin: hala esikte olmayanlarin ts'i dusmesin diye
    for s, v in state.items():
        if s not in new_state and (now - v.get("alert_ts", 0)) < COOLDOWN:
            new_state[s] = v
    json.dump(new_state, open(STATE_F, "w"))

    if not alerts:
        log(f"tarama ok — {len(found)} short adayi, yeni bildirim yok")
        return

    shown = alerts[:MAX_ALERTS]
    for r in shown:
        log(f"SHORT  {r['symbol']} 15dk{r['m15']*100:+.2f}% "
            f"giris={r['entry']:.8g} stop={r['stop']:.8g} tp1={r['tp1']:.8g} skor={r['score']}")
    title = f"🔴 Trend SHORT — {len(alerts)} coin"
    msg = " | ".join(f"{r['symbol'].replace('USDT','')} {r['m15']*100:+.1f}%" for r in shown)
    notify(title, msg)
    if len(alerts) > MAX_ALERTS:
        log(f"(+{len(alerts)-MAX_ALERTS} coin daha short esikte)")


if __name__ == "__main__":
    main()
