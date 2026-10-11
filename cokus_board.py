"""
⚡ Çöküş Sıçraması — tek doğrulanmış 5dk gün içi kuralı (bkz. data/simdi/RAPOR.md, 2026-09-30):

    5dk mum KAPANIŞINDA: son 20dk (4 mum) değişim <= -2%  VE  RSI14(5dk) < 25
    → sonraki mum açılışında LONG, 90 dk sonra kapat (hedef/stop yok, süreye bağlı çıkış)

Backtest (Bybit 5dk, 118 likit coin, 2026-06-01→09-30, maliyet %0.10 dahil):
    n=3300  net ort +0.58%  medyan +0.50%  isabet %61  t=9.5  14/17 hafta artı  plasebo -0.12%
    piyasa PANİK anında +0.98% / isabet %70 · GERGİN +0.31 · SAKİN +0.30
    1 yıla uzatınca +0.22% t1.4 (sınırda) — kuralın gücü çöküş dönemlerine yığılı.

Bu modül: her 5dk kapanışından ~10 sn sonra evreni tarar, adayları ve piyasa seviyesini
durum olarak tutar, adaylara KÂĞIT pozisyon açar (tavan 10 eşzamanlı), 90 dk sonra kapatıp
data/cokus/islemler.jsonl defterine yazar. Gerçek emir göndermez.
"""
import os, json, time, threading, statistics
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import mexc

DIR = os.path.join(os.path.dirname(__file__), "data", "cokus")
os.makedirs(DIR, exist_ok=True)
LEDGER = os.path.join(DIR, "islemler.jsonl")
STATE_F = os.path.join(DIR, "durum.json")
TR = dt.timezone(dt.timedelta(hours=3))

# kural parametreleri
DROP20 = -0.02      # 20dk değişim eşiği
RSI_MAX = 25.0      # RSI14 eşiği (5dk kapanış)
HOLD_MIN = 90       # tutma süresi (dk)
MAX_OPEN = 10       # eşzamanlı kâğıt pozisyon tavanı
MIN_VOL = 3_000_000 # 24s hacim tabanı ($)
UNIVERSE_N = 130    # hacme göre ilk N coin
COST = 0.0010       # gidiş-dönüş maliyet varsayımı (%0.10)

import re
_NOT_CRYPTO = re.compile(r"STOCK|^XAU|^XAG|SILVER|GOLD|OIL|^SPY_|^QQQ|^NVIDIA|^TSLA|^AAPL|^MSFT|^AMZN|^META_|^GOOG|^COIN_|^MSTR|^HOOD|^NFLX|^INTC|^AMD_|^PLTR|^NVDA|^CRCL|^SOXL|^MUSTOCK|^DXY|^EUR|^GBP|^JPY|^CNH", re.I)
LOCK = threading.Lock()
STATE = {"ts": 0, "level": "—", "market": {}, "candidates": [], "near": [], "open": [],
         "stats": {}, "universe_n": 0, "last_bar": None, "error": None}
_UNI = {"ts": 0, "rows": []}


# ───────────────────────── yardımcılar ─────────────────────────
def _rsi(c, n=14):
    if len(c) < n + 2:
        return None
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def _fmt(ts):
    return dt.datetime.fromtimestamp(ts, TR).strftime("%H:%M")


def universe():
    """Hacme göre ilk N USDT perp (saatte bir yenilenir)."""
    if time.time() - _UNI["ts"] < 3600 and _UNI["rows"]:
        return _UNI["rows"]
    data = mexc.get("/api/v1/contract/ticker") or []
    rows = []
    for r in data:
        s = r.get("symbol", "")
        if not s.endswith("_USDT") or _NOT_CRYPTO.search(s):
            continue  # backtest evreni kripto; hisse/emtia tokenları dışarıda
        try:
            v = float(r.get("amount24", 0))
        except (TypeError, ValueError):
            continue
        if v >= MIN_VOL:
            rows.append({"s": s, "vol": v, "fr": float(r.get("fundingRate", 0) or 0),
                         "chg24": float(r.get("riseFallRate", 0) or 0)})
    rows.sort(key=lambda x: -x["vol"])
    if rows:
        _UNI["ts"] = time.time(); _UNI["rows"] = rows[:UNIVERSE_N]
    return _UNI["rows"]


def _bars(sym, minutes=400):
    end = int(time.time()); start = end - minutes * 60
    d = mexc.get(f"/api/v1/contract/kline/{sym}", interval="Min5", start=start, end=end)
    if not d or not d.get("time"):
        return []
    return [{"t": int(d["time"][i]), "o": float(d["open"][i]), "h": float(d["high"][i]),
             "l": float(d["low"][i]), "c": float(d["close"][i]), "v": float(d["vol"][i])}
            for i in range(len(d["time"]))]


def _analyse(row, now):
    b = _bars(row["s"])
    if len(b) < 40:
        return None
    # son KAPANMIŞ mum: t + 300 <= now
    k = len(b) - 1
    while k > 0 and b[k]["t"] + 300 > now:
        k -= 1
    if k < 20:
        return None
    closes = [x["c"] for x in b[:k + 1]]
    c20 = closes[-1] / closes[-5] - 1
    c60 = closes[-1] / closes[-13] - 1
    rsi = _rsi(closes[-60:])
    cur = b[-1]
    return {"s": row["s"], "vol": row["vol"], "fr": row["fr"], "chg24": row["chg24"],
            "bar_t": b[k]["t"], "close": closes[-1], "c20": c20, "c60": c60, "rsi": rsi,
            "px": cur["c"], "next_open": b[k + 1]["o"] if k + 1 < len(b) else cur["c"],
            "lo20": min(x["l"] for x in b[k - 3:k + 1]), "lo4h": min(x["l"] for x in b[max(0, k - 47):k + 1])}


# ───────────────────────── defter ─────────────────────────
def _read_ledger():
    out = []
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        pass
    return out


def _append_ledger(rec):
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _stats(closed):
    if not closed:
        return {"n": 0}
    rets = [x["ret_net"] for x in closed]
    wins = [r for r in rets if r > 0]
    today = dt.datetime.now(TR).date().isoformat()
    tod = [x["ret_net"] for x in closed if x.get("exit_tr", "").startswith(today)]
    return {"n": len(rets), "hit": len(wins) / len(rets), "avg": sum(rets) / len(rets),
            "median": statistics.median(rets), "sum": sum(rets),
            "best": max(rets), "worst": min(rets),
            "today_n": len(tod), "today_sum": sum(tod) if tod else 0.0}


# ───────────────────────── tarama ─────────────────────────
def scan():
    now = int(time.time())
    uni = universe()
    with ThreadPoolExecutor(12) as ex:
        rows = [r for r in ex.map(lambda r: _analyse(r, now), uni) if r]
    if not rows:
        with LOCK:
            STATE["error"] = "veri yok"; STATE["ts"] = now
        return STATE

    med1h = statistics.median([x["c60"] for x in rows])
    share20 = sum(1 for x in rows if x["c20"] <= DROP20) / len(rows)
    med24 = statistics.median([x["chg24"] for x in rows])
    if med1h <= -0.02 or share20 >= 0.20:
        level = "PANİK"
    elif med1h <= -0.01 or share20 >= 0.05 or med24 <= -0.05:
        level = "GERGİN"
    else:
        level = "SAKİN"

    cands = [x for x in rows if x["c20"] <= DROP20 and x["rsi"] is not None and x["rsi"] < RSI_MAX]
    cands.sort(key=lambda x: x["c20"])
    near = [x for x in rows if x not in cands and ((x["c20"] <= -0.015) or (x["rsi"] is not None and x["rsi"] < 30))]
    near.sort(key=lambda x: (x["c20"] + (x["rsi"] or 50) / 1000))
    near = near[:12]

    # kâğıt pozisyonlar
    with LOCK:
        opens = STATE["open"]
    px = {x["s"]: x["px"] for x in rows}
    still = []
    for p in opens:
        cur = px.get(p["s"], p["entry"])
        p["px"] = cur; p["ret"] = cur / p["entry"] - 1
        p["mae"] = min(p.get("mae", 0.0), p["ret"]); p["mfe"] = max(p.get("mfe", 0.0), p["ret"])
        if now >= p["exit_t"]:
            rec = dict(p); rec["exit"] = cur; rec["ret_gross"] = p["ret"]; rec["ret_net"] = p["ret"] - COST
            rec["exit_tr"] = dt.datetime.fromtimestamp(now, TR).strftime("%Y-%m-%d %H:%M")
            _append_ledger(rec)
        else:
            still.append(p)
    opens = still
    have = {p["s"] for p in opens}
    recent = {x["s"] for x in _read_ledger()[-200:] if now - x.get("entry_t", 0) < 3 * 3600}
    for c in cands:
        if len(opens) >= MAX_OPEN:
            break
        if c["s"] in have or c["s"] in recent:
            continue  # aynı coine 3 saat içinde tekrar girme
        opens.append({"s": c["s"], "entry": c["next_open"], "entry_t": c["bar_t"] + 300,
                      "entry_tr": dt.datetime.fromtimestamp(c["bar_t"] + 300, TR).strftime("%Y-%m-%d %H:%M"),
                      "exit_t": c["bar_t"] + 300 + HOLD_MIN * 60, "px": c["px"],
                      "ret": c["px"] / c["next_open"] - 1, "mae": 0.0, "mfe": 0.0,
                      "sig_c20": c["c20"], "sig_rsi": c["rsi"], "level": level, "lo20": c["lo20"]})
        have.add(c["s"])

    closed = _read_ledger()
    with LOCK:
        STATE.update({"ts": now, "level": level, "candidates": cands, "near": near, "open": opens,
                      "market": {"med1h": med1h, "share20": share20, "med24": med24, "n": len(rows)},
                      "stats": _stats(closed), "universe_n": len(uni),
                      "last_bar": rows[0]["bar_t"], "error": None,
                      "history": closed[-60:][::-1]})
        try:
            with open(STATE_F, "w") as f:
                json.dump(STATE, f, ensure_ascii=False)
        except OSError:
            pass
    return STATE


def board():
    """/api/cokus — son durum + açık pozisyonların anlık fiyatı."""
    with LOCK:
        st = json.loads(json.dumps(STATE))
    if st["open"]:
        try:
            data = mexc.get("/api/v1/contract/ticker") or []
            px = {r["symbol"]: float(r["lastPrice"]) for r in data if r.get("symbol") in {p["s"] for p in st["open"]}}
            for p in st["open"]:
                if p["s"] in px:
                    p["px"] = px[p["s"]]; p["ret"] = px[p["s"]] / p["entry"] - 1
        except Exception:
            pass
    st["now"] = int(time.time())
    st["rule"] = {"drop20": DROP20, "rsi_max": RSI_MAX, "hold_min": HOLD_MIN, "max_open": MAX_OPEN,
                  "min_vol": MIN_VOL, "cost": COST}
    return st


def loop():
    """Arka plan: her 5dk sınırından 10 sn sonra tara (sekme kapalıyken de defter dolsun)."""
    # açılışta durumu yükle (açık pozisyonlar kaybolmasın)
    try:
        if os.path.exists(STATE_F):
            with open(STATE_F) as f:
                old = json.load(f)
            with LOCK:
                STATE["open"] = old.get("open", [])
    except (OSError, ValueError):
        pass
    time.sleep(3)
    try:
        scan()
    except Exception as e:
        print("cokus ilk tarama hatası:", e)
    while True:
        now = time.time()
        nxt = (int(now) // 300 + 1) * 300 + 10
        time.sleep(max(1, nxt - now))
        try:
            scan()
        except Exception as e:
            print("cokus tarama hatası:", e)
            with LOCK:
                STATE["error"] = str(e)


if __name__ == "__main__":
    s = scan()
    print(s["level"], s["market"])
    for c in s["candidates"]:
        print(c["s"], f"{c['c20']*100:+.2f}%", f"rsi {c['rsi']:.1f}")
