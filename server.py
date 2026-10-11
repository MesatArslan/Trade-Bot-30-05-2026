"""
Funding Trader — yerel web sunucusu.
Tarayıcıdan buton/panel ile yönetim. Ek paket gerektirmez (stdlib).

Başlat:
    ./.venv/bin/python server.py
Sonra tarayıcıda:  http://localhost:8787
"""
import os
import re
import json
import time
import threading
import datetime as dt
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import urllib.request
import subprocess

import mexc
import binance
import scan
import decide
import sr_board
import cokus_board
import analist_tarama
import stoch_izle
import config as C

PORT = 8787
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
TR = dt.timezone(dt.timedelta(hours=3))

# ─── Paylaşılan durum ───────────────────────────────────────────
LOCK = threading.Lock()
STATE = {
    "scan": {"status": "idle", "candidates": [], "watchlist": [], "ts": 0, "msg": ""},
    "watch": None,   # aktif takip varsa dict
    "plan_watch": {},  # symbol -> retest izleyici durumu (Plan sekmesi "en iyi giriş")
}


def _notify(title, msg):
    """macOS bildirimi (sesli). funding_alarm.notify ile ayni mekanizma."""
    try:
        t = json.dumps(str(title)); m = json.dumps(str(msg))
        subprocess.run(["osascript", "-e",
                        f'display notification {m} with title {t} sound name "Ping"'],
                       check=False)
    except Exception:
        pass


def _fmt_ms(ms):
    if not ms:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000, TR).strftime("%H:%M:%S")


# ─── HIZLI FUNDING LİDER TABLOSU (geçmiş çekmez, anında) ────────
def funding_board(min_vol=3_000_000, top=25):
    """Tüm likit coinleri anlık funding'e göre sıralar. MEXC + Binance yan yana."""
    snap = mexc.snapshot()
    bmap = binance.funding_map()
    rows = []
    for r in snap:
        if r["amount24"] < min_vol:
            continue
        bsym = r["symbol"].replace("_", "").upper()
        b = bmap.get(bsym)
        bf = b["funding"] if b else None
        rows.append({
            "symbol": r["symbol"],
            "funding": round(r["fundingRate"], 6),
            "binance_funding": round(bf, 6) if bf is not None else None,
            "agree": (None if bf is None
                      else (bf > 0) == (r["fundingRate"] > 0)),
            "oi_usd": int(r["oi_usd"]),
            "vol24": int(r["amount24"]),
            "basis": round(r["basis"], 5),
        })
    pos = sorted([x for x in rows if x["funding"] > 0],
                 key=lambda x: x["funding"], reverse=True)[:top]
    neg = sorted([x for x in rows if x["funding"] < 0],
                 key=lambda x: x["funding"])[:top]
    return {"positive": pos, "negative": neg,
            "count": len(rows), "ts": int(time.time())}


# ─── SQUEEZE ADAYLARI (SKR profili — Bybit) ───────────────────────
# Derin & giderek negatifleşen funding + küçük OI + yeterli hacim.
# Kısa TTL cache: 30 coinin funding geçmişi çekildiği için ağır; 60 sn tutulur.
_BYBIT = "https://api.bybit.com"
SQ_FUNDING_MAX = -0.003     # funding <= -0.3%
SQ_MIN_TURNOVER = 3_000_000
SQ_MAX_OI = 60_000_000
SQ_FRESH_DAYS = 120
_SQ_CACHE = {"ts": 0, "data": None}
_SQ_LOCK = threading.Lock()


def _bybit_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def squeeze_board(max_detail=30):
    """SKR profiline uyan Bybit coinlerini skora göre döndürür (60sn cache)."""
    with _SQ_LOCK:
        if _SQ_CACHE["data"] is not None and time.time() - _SQ_CACHE["ts"] < 60:
            return _SQ_CACHE["data"]

    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    info = _bybit_get(f"{_BYBIT}/v5/market/instruments-info?category=linear&limit=1000")["result"]["list"]
    launch = {i["symbol"]: int(i.get("launchTime", "0") or 0) for i in info}
    # Bybit sembolunu -> gercek MEXC sembolune eslemek icin arama tablosu.
    # MEXC tokenize hisseleri "STOCK" ekiyle listeler (TEM -> TEMSTOCK_USDT),
    # o yuzden hem duz hem STOCK'suz alias'i ekliyoruz.
    mexc_lookup = {}
    try:
        for sym in mexc.contract_sizes().keys():
            if not sym.endswith("_USDT"):
                continue
            base = sym[:-5]
            mexc_lookup.setdefault(base, sym)
            if base.endswith("STOCK"):
                mexc_lookup.setdefault(base[:-5], sym)
    except Exception:
        mexc_lookup = {}

    now = time.time()
    cands = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            fr = float(t["fundingRate"]); oi = float(t["openInterestValue"])
            tov = float(t["turnover24h"]); pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError):
            continue
        if fr >= SQ_FUNDING_MAX or tov < SQ_MIN_TURNOVER or oi > SQ_MAX_OI:
            continue
        cands.append((s, fr, oi, tov, pc))

    cands.sort(key=lambda x: x[1])
    out = []
    for s, fr, oi, tov, pc in cands[:max_detail]:
        try:
            fh = _bybit_get(f"{_BYBIT}/v5/market/funding/history?category=linear&symbol={s}&limit=4")["result"]["list"]
            vals = [float(f["fundingRate"]) for f in fh]
        except Exception:
            vals = [fr]
        worsening = len(vals) >= 3 and vals[0] < vals[1] < vals[2]
        allneg = all(v < 0 for v in vals[:3])
        trend = "worsening" if worsening else ("negative" if allneg else "mixed")
        age = (now - launch.get(s, 0) / 1000) / 86400 if launch.get(s, 0) else 9999
        fresh = "taze" if age < SQ_FRESH_DAYS else ("orta" if age < 365 else "eski")
        pcp = pc * 100
        phase = "late" if pcp > 40 else ("fresh" if pcp < 15 else "mid")
        sc = min(abs(fr) / 0.01, 3) * 3
        sc += 3 if worsening else (1 if allneg else 0)
        sc += max(0, (SQ_MAX_OI - oi) / SQ_MAX_OI) * 2
        sc += 2 if fresh == "taze" else (1 if fresh == "orta" else 0)
        sc += 3 if phase == "fresh" else (-2 if phase == "late" else 0)
        out.append({
            "symbol": s, "funding": round(fr, 6), "oi_usd": int(oi),
            "vol24": int(tov), "pc24": round(pc, 4), "trend": trend,
            "age_days": None if age > 9000 else round(age),
            "fresh": fresh, "phase": phase,
            "mexc_sym": mexc_lookup.get(s[:-4]),
            "vals": [round(v, 6) for v in vals[:4]], "score": round(sc, 2),
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    data = {"candidates": out, "count": len(out), "ts": int(time.time())}
    with _SQ_LOCK:
        _SQ_CACHE["ts"] = time.time(); _SQ_CACHE["data"] = data
    return data


# ─── ANLIK PATLAMA TABLOSU (Bybit) — ŞU AN aniden fırlayan coinler ───
# Amaç: son 15dk-1s icinde sert/ani pump yapan coinleri yakala (24s yavas
# yukselen DEGIL). Short/reversal ya da momentum icin. 30 sn cache.
PUMP_MIN_TURNOVER = 3_000_000     # 24s hacim >= $3M (likidite)
PUMP_MIN_M15 = 0.025             # son 15dk >= +%2.5  → anlik hareket
PUMP_MIN_H1 = 0.05               # ya da son 1s >= +%5
PUMP_UNIVERSE = 200              # hacme gore ilk N coini kline ile tara
_PUMP_CACHE = {"ts": 0, "data": None}
_PUMP_LOCK = threading.Lock()


def _bybit_kl(sym, interval, limit):
    d = _bybit_get(
        f"{_BYBIT}/v5/market/kline?category=linear&symbol={sym}"
        f"&interval={interval}&limit={limit}")
    return sorted(d["result"]["list"], key=lambda x: int(x[0]))  # [ts,o,h,l,c,vol,turn]


# ─── BTC 5dk REJİM (yukarı / aşağı / yatay) ─────────────────────────
# EMA20/50 konumu + EMA50 eğimi + ADX14 yönü. 30 sn cache.
_BTCR_CACHE = {"ts": 0, "data": None}
_BTCR_LOCK = threading.Lock()


def _btcr_ema(vals, n):
    a = 2 / (n + 1); e = [vals[0]]
    for v in vals[1:]:
        e.append(e[-1] + a * (v - e[-1]))
    return e


def btc_regime_5m():
    """BTCUSDT 5dk mumlardan anlik rejim: up / down / flat + ADX ve skor."""
    with _BTCR_LOCK:
        if _BTCR_CACHE["data"] is not None and time.time() - _BTCR_CACHE["ts"] < 30:
            return _BTCR_CACHE["data"]
    try:
        k = _bybit_kl("BTCUSDT", "5", 300)[:-1]   # son mum kapanmamis → at
        c = [float(x[4]) for x in k]; h = [float(x[2]) for x in k]; l = [float(x[3]) for x in k]
        e20, e50 = _btcr_ema(c, 20), _btcr_ema(c, 50)
        tr, pdm, ndm = [], [], []
        for i in range(1, len(k)):
            tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
            up = h[i] - h[i - 1]; dn = l[i - 1] - l[i]
            pdm.append(up if up > dn and up > 0 else 0)
            ndm.append(dn if dn > up and dn > 0 else 0)

        def wilder(v, n=14):
            o = [sum(v[:n])]
            for x in v[n:]:
                o.append(o[-1] - o[-1] / n + x)
            return o
        atr, P, N = wilder(tr), wilder(pdm), wilder(ndm)
        pdi = [100 * p / a if a else 0 for p, a in zip(P, atr)]
        ndi = [100 * n / a if a else 0 for n, a in zip(N, atr)]
        dx = [100 * abs(p - n) / (p + n) if p + n else 0 for p, n in zip(pdi, ndi)]
        adx = [sum(dx[:14]) / 14]
        for v in dx[14:]:
            adx.append((adx[-1] * 13 + v) / 14)
        sc = 0
        sc += 1 if c[-1] > e20[-1] else -1
        sc += 1 if c[-1] > e50[-1] else -1
        sc += 1 if e20[-1] > e50[-1] else -1
        sc += 1 if e50[-1] > e50[-7] else -1
        sc += 1 if pdi[-1] > ndi[-1] else -1
        # ADX < 20 → yon ne olursa olsun yatay; skor ±3 altinda da yatay
        if adx[-1] < 20 or abs(sc) < 3:
            regime = "flat"
        else:
            regime = "up" if sc > 0 else "down"
        data = {
            "regime": regime, "score": sc, "adx": round(adx[-1], 1),
            "pdi": round(pdi[-1], 1), "ndi": round(ndi[-1], 1),
            "price": c[-1], "ema20": round(e20[-1], 1), "ema50": round(e50[-1], 1),
            "r30m": round(c[-1] / c[-7] - 1, 4), "r1h": round(c[-1] / c[-13] - 1, 4),
            "r4h": round(c[-1] / c[-49] - 1, 4),
            "bar_ts": int(k[-1][0]) // 1000, "ts": int(time.time()),
        }
    except Exception as e:
        data = {"regime": "na", "error": str(e), "ts": int(time.time())}
    with _BTCR_LOCK:
        _BTCR_CACHE["ts"] = time.time(); _BTCR_CACHE["data"] = data
    return data


def pump_board(max_detail=40):
    """ŞU AN aniden fırlayan Bybit coinlerini son-15dk/1s momentumuna göre
    döndürür. 24s toplam degil — son mumlardaki ani hareket + hacim patlamasi.
    Skor yüksek = daha taze/sert anlik pump. 30 sn cache."""
    with _PUMP_LOCK:
        if _PUMP_CACHE["data"] is not None and time.time() - _PUMP_CACHE["ts"] < 30:
            return _PUMP_CACHE["data"]

    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t["turnover24h"]); fr = float(t.get("fundingRate") or 0)
            pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError):
            continue
        if tov < PUMP_MIN_TURNOVER:
            continue
        liq.append((s, tov, fr, pc))
    liq.sort(key=lambda x: x[1], reverse=True)

    def detail(item):
        s, tov, fr, pc = item
        try:
            k5 = _bybit_kl(s, "5", 36)      # son 3 saat, 5dk mum
            cl = [float(x[4]) for x in k5]
            hi = [float(x[2]) for x in k5]
            lo = [float(x[3]) for x in k5]
            vol = [float(x[5]) for x in k5]
            price = cl[-1]
            m15 = (price / cl[-4] - 1) if len(cl) >= 5 else 0     # son 15dk (3x5dk)
            m30 = (price / cl[-7] - 1) if len(cl) >= 8 else 0     # son 30dk
            h1 = (price / cl[-13] - 1) if len(cl) >= 14 else 0    # son 1 saat
            # anlik hacim patlamasi: son 3 mum vs onceki ~2s medyan
            recent_v = sum(vol[-3:]) / 3 if len(vol) >= 3 else 0
            base_v = sorted(vol[-30:-3])[len(vol[-30:-3]) // 2] if len(vol) >= 33 else (recent_v or 1)
            vspike = recent_v / base_v if base_v > 0 else 0
            hi1h = max(hi[-12:]) if len(hi) >= 12 else price
            fade = (hi1h - price) / hi1h if hi1h else 0          # 1s tepesinden geri cekilme
        except Exception:
            price = m15 = m30 = h1 = vspike = fade = 0
        # anlik pump esigi
        if m15 < PUMP_MIN_M15 and h1 < PUMP_MIN_H1:
            return None
        # faz: hala firliyor mu, tepede mi, donuyor mu (short icin)
        if m15 <= 0 and fade >= 0.015:
            phase = "rolling"    # ani pump sonrasi donuyor → short adayi
        elif m15 < 0.005 and fade >= 0.008:
            phase = "topping"    # tepede, momentum kesiliyor
        else:
            phase = "running"    # su an aktif firliyor (taze)
        # skor: son 15dk agirlikli (anliklik) + 1s + hacim patlamasi
        sc = m15 * 400 + m30 * 120 + h1 * 60 + min(vspike, 8) * 6
        return {
            "symbol": s, "price": price, "pc24": round(pc, 4),
            "funding": round(fr, 6), "vol24": int(tov),
            "m15": round(m15, 4), "m30": round(m30, 4), "h1": round(h1, 4),
            "vspike": round(vspike, 2), "fade": round(fade, 4),
            "phase": phase, "score": round(sc, 2),
        }

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(detail, liq[:PUMP_UNIVERSE]))
    out = [r for r in res if r]
    out.sort(key=lambda r: r["score"], reverse=True)
    out = out[:max_detail]
    data = {"candidates": out, "count": len(out), "ts": int(time.time())}
    with _PUMP_LOCK:
        _PUMP_CACHE["ts"] = time.time(); _PUMP_CACHE["data"] = data
    return data


# ─── DİRENÇ / KIRILIM PLANI (Bybit) — uptrend'de direnci test eden LONG adayları ─
# Amac: senin istedigin arastirma ciktisi. Yukselen trendte, bir DIRENC
# seviyesinin hemen altinda sikisip tavani kirmaya calisan coinleri bulur.
# Her aday icin somut plan: giris (direnc kirilimi), stop (swing low),
# hedef1/hedef2 ve direnc noktalari. Her tarama diske saat damgasiyla kaydedilir.
PLAN_MIN_TURNOVER = 5_000_000     # 24s hacim >= $5M (likidite)
PLAN_UNIVERSE = 220               # hacme gore ilk N coini kline ile tara
PLAN_NEAR = 0.035                 # fiyat direncin en fazla %3.5 altinda olmali
PLAN_MAX_EXT24 = 0.45             # 24h > +%45 ise cok uzamis → kovalama, atla
PLAN_DIR = os.path.join(os.path.dirname(__file__), "plans")


def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else sum(xs) / len(xs)


def plan_scan(max_detail=12):
    """Uptrend'de bir direnci test eden LONG kirilim adaylarini uretir.
    Her aday: giris (direnc + tampon), stop (swing low), tp1/tp2, direnc noktalari.
    15dk mumlarla calisir; giris = 'bu seviye ustunde mum kapanisinda al'."""
    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t["turnover24h"]); pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError):
            continue
        if tov < PLAN_MIN_TURNOVER:
            continue
        liq.append((s, tov, pc))
    liq.sort(key=lambda x: x[1], reverse=True)

    def detail(item):
        s, tov, pc = item
        try:
            k = _bybit_kl(s, "15", 200)          # son ~50 saat, 15dk mum
            hi = [float(x[2]) for x in k]
            lo = [float(x[3]) for x in k]
            cl = [float(x[4]) for x in k]
        except Exception:
            return None
        n = len(cl)
        if n < 60:
            return None
        price = cl[-1]
        sma20 = _sma(cl, 20); sma50 = _sma(cl, 50)
        # 1) Uptrend filtresi: fiyat > SMA20 > SMA50
        if not (price > sma20 > sma50):
            return None
        ret24 = price / cl[-96] - 1 if n >= 97 else price / cl[0] - 1
        if ret24 <= 0 or ret24 > PLAN_MAX_EXT24:      # dusuyor ya da cok uzamis
            return None
        # 2) Yakin direnc = son ~40 mumun (son mum haric) en yuksegi = test edilen tavan
        res1 = max(hi[-40:-1])
        res2 = max(hi[-160:]) if n >= 160 else max(hi)   # uzak/ana direnc
        dist = (res1 - price) / price                    # fiyat direncin ne kadar altinda
        if dist < -0.001 or dist > PLAN_NEAR:            # ya kirmis ya cok uzakta
            return None
        # 3) ATR (son 15 mum) — tampon/stop icin
        trs = [hi[i] - lo[i] for i in range(n - 15, n)]
        atr_abs = sum(trs) / 15
        atr_pct = atr_abs / price if price else 0
        # 4) Giris = direnc + kucuk tampon (sahte kirilimi elemek icin)
        buf = max(0.0015, atr_pct * 0.15)
        entry = res1 * (1 + buf)
        # 5) Stop = son 16 mumun swing low'u - 0.4 ATR, %1.5-%6 arasi kisitli
        swing_low = min(lo[-16:])
        stop = swing_low - 0.4 * atr_abs
        stop_pct = (entry - stop) / entry if entry else 0
        if stop_pct < 0.015:
            stop = entry * (1 - 0.015); stop_pct = 0.015
        elif stop_pct > 0.06:
            stop = entry * (1 - 0.06); stop_pct = 0.06
        sd = entry - stop
        # 6) Hedefler — oynakliga gore olceklenir
        tp1 = entry + max(1.6 * sd, 1.2 * atr_abs)
        tp2 = entry + max(4.5 * sd, 4.0 * atr_abs)
        # 7) Skor: kirilima yakinlik + trend egimi + hacim + momentum
        prox = 1 - max(0.0, dist) / PLAN_NEAR
        trend = sma20 / sma50 - 1
        vol_s = min(tov / 50_000_000, 1)
        score = prox * 4 + min(trend * 40, 3) + vol_s * 2 + min(ret24 * 5, 2)
        return {
            "symbol": s, "price": round(price, 8),
            "side": "LONG",
            "entry": round(entry, 8), "stop": round(stop, 8),
            "tp1": round(tp1, 8), "tp2": round(tp2, 8),
            "stop_pct": round(stop_pct * 100, 2),
            "tp1_pct": round((tp1 - entry) / entry * 100, 2),
            "tp2_pct": round((tp2 - entry) / entry * 100, 2),
            "res1": round(res1, 8), "res2": round(res2, 8),
            "support": round(swing_low, 8),
            "dist_pct": round(dist * 100, 2),
            "atr_pct": round(atr_pct * 100, 2),
            "ret24": round(ret24 * 100, 2),
            "vol24": int(tov), "score": round(score, 2),
        }

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(detail, liq[:PLAN_UNIVERSE]))
    out = [r for r in res if r]
    out.sort(key=lambda r: r["score"], reverse=True)
    out = out[:max_detail]
    return {"candidates": out, "count": len(out), "ts": int(time.time())}


def _plan_save(data, plan_dir=PLAN_DIR):
    """Tarama sonucunu saat damgasiyla plan_dir altina kaydeder, id ekler."""
    os.makedirs(plan_dir, exist_ok=True)
    now = dt.datetime.now(TR)
    rid = now.strftime("%Y%m%d_%H%M%S")
    data = dict(data)
    data["id"] = rid
    data["ts_tr"] = now.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(plan_dir, f"plan_{rid}.json"), "w") as f:
        json.dump(data, f)
    return data


def _plan_history(plan_dir=PLAN_DIR):
    """Kayitli taramalari yeniden eskiye listeler (ozet)."""
    if not os.path.isdir(plan_dir):
        return []
    out = []
    for fn in sorted(os.listdir(plan_dir), reverse=True):
        if not (fn.startswith("plan_") and fn.endswith(".json")):
            continue
        try:
            with open(os.path.join(plan_dir, fn)) as f:
                d = json.load(f)
            out.append({
                "id": d.get("id"),
                "ts_tr": d.get("ts_tr"),
                "count": len(d.get("candidates", [])),
                "symbols": [c["symbol"] for c in d.get("candidates", [])[:6]],
            })
        except Exception:
            continue
    return out


def _plan_load(rid, plan_dir=PLAN_DIR):
    p = os.path.join(plan_dir, f"plan_{rid}.json")
    if not (rid and os.path.exists(p)):
        return None
    with open(p) as f:
        return json.load(f)


def _plan_delete(rid, plan_dir=PLAN_DIR):
    """Kayitli bir taramayi siler. id gecerli/temiz degilse dokunmaz."""
    if not rid or not all(ch.isdigit() or ch == "_" for ch in rid):
        return False
    p = os.path.join(plan_dir, f"plan_{rid}.json")
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


# ─── RETEST TAMAMLANMIŞ / YUKARI DÖNEN (Bybit) — canlı pano ─────────
# Kullanicinin istedigi sekme: bir DIRENCI KIRMIS, o seviyeye RETEST yapmis
# ve SU AN tekrar YUKARI donmekte olan coinleri tarar. Plan sekmesindeki gibi
# "gelecek aday" degil; paterni SU AN tamamlamis "hazir firsat" listesi.
# 5dk mumlarla calisir (son ~16 saat yapisi). 30 sn cache.
RB_MIN_TURNOVER   = 5_000_000
RB_UNIVERSE       = 220
RB_RETEST_BAND    = 0.006    # retest, seviyeye %0.6 içine dokunmus olmali
RB_MAX_EXT        = 0.08     # fiyat seviyenin en fazla %8 ustunde (henuz uzamamis)
RB_BREAK_LOOKBACK = 24       # son N 5dk mumu icinde kirilim olmus olmali (~120dk)
_RB_CACHE = {"ts": 0, "data": None}
_RB_LOCK = threading.Lock()


def retest_board(max_detail=20):
    """Direnci kirip retest yapmis ve su an yukari donen Bybit coinleri."""
    with _RB_LOCK:
        if _RB_CACHE["data"] is not None and time.time() - _RB_CACHE["ts"] < 30:
            return _RB_CACHE["data"]

    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t["turnover24h"])
        except (KeyError, ValueError, TypeError):
            continue
        if tov < RB_MIN_TURNOVER:
            continue
        liq.append((s, tov))
    liq.sort(key=lambda x: x[1], reverse=True)

    def detail(item):
        s, tov = item
        try:
            k = _bybit_kl(s, "5", 200)
            hi = [float(x[2]) for x in k]
            lo = [float(x[3]) for x in k]
            cl = [float(x[4]) for x in k]
            vo = [float(x[5]) for x in k]
        except Exception:
            return None
        n = len(cl)
        if n < 80:
            return None
        price = cl[-1]
        sma50 = _sma(cl, 50)
        if price <= sma50:                       # genel yon yukari degilse ele
            return None
        # 1) Direnç seviyesi = son hareketten önceki tavan (son ~10 mum haric)
        R = max(hi[-60:-10])
        # 2) Kırılım: son RB_BREAK_LOOKBACK mumdan biri R üstünde KAPANMIŞ olsun
        bidx = None
        for i in range(n - RB_BREAK_LOOKBACK, n - 1):
            if i >= 1 and cl[i] > R * 1.001 and cl[i - 1] <= R * 1.001:
                bidx = i
        if bidx is None:
            return None
        # 3) Fiyat ŞU AN hâlâ seviyenin üstünde mi (kırılımı koruyor) ve
        #    henüz uzamamış mı? Tek zorunlu şart bu — "direnci kırmış" listesi.
        dist_above = (price - R) / R
        if not (0 <= dist_above <= RB_MAX_EXT):
            return None
        # 4) Retest / dönüş durumu — ZORUNLU DEĞİL, sadece bilgi + skor.
        seg_lo = lo[bidx:]
        retest_low = min(seg_lo)
        lowest_at = bidx + seg_lo.index(retest_low)
        retest_done = (retest_low <= R * (1 + RB_RETEST_BAND)
                       and retest_low >= R * (1 - 0.02)
                       and lowest_at < n - 1)
        rising = cl[-1] > cl[-2]
        ema9 = _ema(cl[-30:], 9)
        above_ema = ema9 is None or price > ema9
        # 5) Hacim (bilgi + skor). Son mum henüz oluşuyor olabilir → son 2'nin maksı.
        vavg = sum(vo[-20:]) / 20
        vrecent = max(vo[-1], vo[-2])
        vol_mult = vrecent / vavg if vavg else 0
        # 6) Plan: giris = simdiki fiyat, stop = retest dibi altı, hedefler R katı
        trs = [hi[i] - lo[i] for i in range(n - 14, n)]
        atr_abs = sum(trs) / 14
        atr_pct = atr_abs / price if price else 0
        entry = price
        stop = min(retest_low, R) - 0.4 * atr_abs
        stop_pct = (entry - stop) / entry if entry else 0
        if stop_pct < 0.015:
            stop = entry * (1 - 0.015); stop_pct = 0.015
        elif stop_pct > 0.06:
            stop = entry * (1 - 0.06); stop_pct = 0.06
        sd = entry - stop
        tp1 = entry + max(1.6 * sd, 1.2 * atr_abs)
        tp2 = entry + max(4.0 * sd, 3.5 * atr_abs)
        bars_ago = (n - 1) - bidx
        # 7) Skor: retest yaptiysa + yukari donuyorsa + taze + hacim + likidite
        fresh = 1 - min(bars_ago / RB_BREAK_LOOKBACK, 1)
        prox = 1 - min(dist_above / RB_MAX_EXT, 1)
        vol_s = min(vrecent / (vavg * 3), 1) if vavg else 0
        turn_s = min(tov / 50_000_000, 1)
        score = (fresh * 2 + prox * 2 + vol_s * 2 + turn_s * 1
                 + (2 if retest_done else 0)
                 + (1 if (rising and above_ema) else 0))
        return {
            "symbol": s, "side": "LONG",
            "price": round(price, 8), "level": round(R, 8), "res1": round(R, 8),
            "entry": round(entry, 8), "stop": round(stop, 8),
            "tp1": round(tp1, 8), "tp2": round(tp2, 8),
            "stop_pct": round(stop_pct * 100, 2),
            "tp1_pct": round((tp1 - entry) / entry * 100, 2),
            "tp2_pct": round((tp2 - entry) / entry * 100, 2),
            "retest_low": round(retest_low, 8),
            "retest_done": retest_done, "rising": bool(rising and above_ema),
            "dist_pct": round(dist_above * 100, 2),
            "atr_pct": round(atr_pct * 100, 2),
            "bars_ago": bars_ago,
            "vol_mult": round(vol_mult, 1),
            "vol24": int(tov), "score": round(score, 2),
        }

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(detail, liq[:RB_UNIVERSE]))
    out = [r for r in res if r]
    out.sort(key=lambda r: r["score"], reverse=True)
    out = out[:max_detail]
    data = {"candidates": out, "count": len(out), "ts": int(time.time())}
    with _RB_LOCK:
        _RB_CACHE["ts"] = time.time(); _RB_CACHE["data"] = data
    return data


# ─── DESTEK / KIRILIM PLANI (Bybit) — downtrend'de destegi test eden SHORT adaylari ─
# plan_scan'in aynasi: dususte, bir DESTEK seviyesinin hemen ustunde sikisip
# tabani asagi kirmaya calisan coinleri bulur. Her aday icin somut plan:
# giris (destek kirilimi asagi), stop (swing high), hedef1/hedef2 ve destek noktalari.
SPLAN_DIR = os.path.join(os.path.dirname(__file__), "plans_short")


def short_plan_scan(max_detail=12):
    """Downtrend'de bir destegi test eden SHORT kirilim adaylarini uretir.
    Her aday: giris (destek - tampon, asagi), stop (swing high), tp1/tp2,
    destek noktalari. 15dk mumlarla calisir; giris = 'bu seviye altinda mum
    kapanisinda sat (short ac)'."""
    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t["turnover24h"]); pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError):
            continue
        if tov < PLAN_MIN_TURNOVER:
            continue
        liq.append((s, tov, pc))
    liq.sort(key=lambda x: x[1], reverse=True)

    def detail(item):
        s, tov, pc = item
        try:
            k = _bybit_kl(s, "15", 200)          # son ~50 saat, 15dk mum
            hi = [float(x[2]) for x in k]
            lo = [float(x[3]) for x in k]
            cl = [float(x[4]) for x in k]
        except Exception:
            return None
        n = len(cl)
        if n < 60:
            return None
        price = cl[-1]
        sma20 = _sma(cl, 20); sma50 = _sma(cl, 50)
        # 1) Downtrend filtresi: fiyat < SMA20 < SMA50
        if not (price < sma20 < sma50):
            return None
        ret24 = price / cl[-96] - 1 if n >= 97 else price / cl[0] - 1
        if ret24 >= 0 or ret24 < -PLAN_MAX_EXT24:     # yukseliyor ya da cok dusmus
            return None
        # 2) Yakin destek = son ~40 mumun (son mum haric) en dusugu = test edilen taban
        sup1 = min(lo[-40:-1])
        sup2 = min(lo[-160:]) if n >= 160 else min(lo)   # uzak/ana destek
        dist = (price - sup1) / price                    # fiyat destegin ne kadar ustunde
        if dist < -0.001 or dist > PLAN_NEAR:            # ya kirmis ya cok uzakta
            return None
        # 3) ATR (son 15 mum) — tampon/stop icin
        trs = [hi[i] - lo[i] for i in range(n - 15, n)]
        atr_abs = sum(trs) / 15
        atr_pct = atr_abs / price if price else 0
        # 4) Giris = destek - kucuk tampon (sahte kirilimi elemek icin)
        buf = max(0.0015, atr_pct * 0.15)
        entry = sup1 * (1 - buf)
        # 5) Stop = son 16 mumun swing high'i + 0.4 ATR, %1.5-%6 arasi kisitli
        swing_high = max(hi[-16:])
        stop = swing_high + 0.4 * atr_abs
        stop_pct = (stop - entry) / entry if entry else 0
        if stop_pct < 0.015:
            stop = entry * (1 + 0.015); stop_pct = 0.015
        elif stop_pct > 0.06:
            stop = entry * (1 + 0.06); stop_pct = 0.06
        sd = stop - entry
        # 6) Hedefler — oynakliga gore olceklenir (asagi)
        tp1 = entry - max(1.6 * sd, 1.2 * atr_abs)
        tp2 = entry - max(4.5 * sd, 4.0 * atr_abs)
        # 7) Skor: kirilima yakinlik + trend egimi (asagi) + hacim + momentum
        prox = 1 - max(0.0, dist) / PLAN_NEAR
        trend = sma50 / sma20 - 1
        vol_s = min(tov / 50_000_000, 1)
        score = prox * 4 + min(trend * 40, 3) + vol_s * 2 + min(abs(ret24) * 5, 2)
        return {
            "symbol": s, "price": round(price, 8),
            "side": "SHORT",
            "entry": round(entry, 8), "stop": round(stop, 8),
            "tp1": round(tp1, 8), "tp2": round(tp2, 8),
            "stop_pct": round(stop_pct * 100, 2),
            "tp1_pct": round((entry - tp1) / entry * 100, 2),
            "tp2_pct": round((entry - tp2) / entry * 100, 2),
            "sup1": round(sup1, 8), "sup2": round(sup2, 8),
            "resistance": round(swing_high, 8),
            "dist_pct": round(dist * 100, 2),
            "atr_pct": round(atr_pct * 100, 2),
            "ret24": round(ret24 * 100, 2),
            "vol24": int(tov), "score": round(score, 2),
        }

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(detail, liq[:PLAN_UNIVERSE]))
    out = [r for r in res if r]
    out.sort(key=lambda r: r["score"], reverse=True)
    out = out[:max_detail]
    return {"candidates": out, "count": len(out), "ts": int(time.time())}


# ─── TREND SCALP (Bybit) — ŞU AN aktif trendde koşan coinler (LONG + SHORT) ─
# Kullanicinin istedigi sekme: "yukseliste olan coini tak diye yakala, trene atla,
# 15-20 dk vur cik" — ayni sekilde dususte olani short'la. Pump gibi +%15 firlamis
# ASIRI/GEC hareket DEGIL; EMA9>EMA21 dizilimi ile taze, saglikli, hala akan trend.
# Her aday: yon (LONG/SHORT), anlik giris = piyasa, dar ATR-stop, hizli tp1/tp2.
# 5dk mumlarla calisir. 30 sn cache.
TR_MIN_TURNOVER = 5_000_000       # 24s hacim >= $5M (likidite)
TR_UNIVERSE     = 200             # hacme gore ilk N coini kline ile tara
TR_MIN_M15      = 0.004           # son 15dk >= %0.4 (yonlu, aktif hareket)
TR_MAX_EXT      = 0.05            # fiyat EMA21'den en fazla %5 uzakta (kovalama degil)
_TR_CACHE = {"ts": 0, "data": None}
_TR_LOCK  = threading.Lock()

# ═══════════════════════ PRO TREND (MTF: 5m trend + 1m giris) ═══════════════════
# "Trend Scalp"in profesyonel surumu. Ayni mantik (trendde ol) ama coklu zaman
# dilimi teyidi ile: 5dk TREND YONU verir (EMA9>EMA21>EMA50 tam dizilim + ADX guc),
# 1dk GIRIS + 4-5 mum DEVAM olasiligi olcer. Kullanici 1dk grafikte girer; hedefler
# 4-5 x 1dk ATR projeksiyonu. Amac: verdikten sonra coin 4-5 mum ayni yone kossun.
PT_MIN_TURNOVER = 8_000_000       # 24s hacim >= $8M (daha likit -> daha temiz akis)
PT_UNIVERSE     = 200             # hacme gore ilk N coini tara
PT_MIN_ADX      = 22              # 1dk ADX >= 22 -> gercek trend (chop degil)
PT_MIN_ADX_5M   = 20             # 5dk ADX >= 20 -> ust zaman dilimi de trendde
PT_MAX_EXT_1M   = 0.012           # 1dk EMA21'den en fazla %1.2 uzak (taze giris, kovalama yok)
PT_MAX_CONSEC   = 6               # 6+ ard arda ayni renk 1dk mum -> tukenme, alma
PT_MIN_ATR      = 0.0011          # 1dk ATR >= %0.11 -> 4-5 mum hareketi ise deger (olu coin ele)
PT_MIN_RR       = 1.3             # hedef2/stop >= 1.3 -> mantikli risk-odul
PT_MIN_CONF     = 58              # devam guveni >= 58 -> panoda goster
_PT_CACHE = {"ts": 0, "data": None}
_PT_LOCK  = threading.Lock()

# ═══════════════════════ 1DK SCALP (Pro Trend + giris kalitesi) ══════════════════
# Pro Trend'in scalp'e ozel, en secici surumu. AYNI MTF cekirdegi (5dk trend + 1dk
# giris) uzerine SCALP GIRIS KALITESI ekler: sadece EMA9'a taze pullback / dipten
# donus / temiz breakout aninda, onay mumu ile sinyal verir. Stop salinim yapisinin
# ardina konur (gercek scalp stopu), hedefler 4-5x1dk ATR. Amac: 1dk grafikte tak
# diye gir, 4-5 mum kosur. Az ama en kaliteli kurulumlar. 20 sn cache.
SC_MIN_TURNOVER = 10_000_000      # 24s hacim >= $10M (en likit -> temiz fill, dar spread)
SC_UNIVERSE     = 220
SC_MIN_ADX      = 23              # 1dk ADX >= 23 (Pro Trend'den bir tik daha guclu)
SC_MIN_ADX_5M   = 20
SC_MAX_EXT_1M   = 0.010           # 1dk EMA21'den max %1.0 uzak (kovalama yasak)
SC_MAX_CONSEC   = 5              # 5+ ard arda mum -> tukenme, alma
SC_MIN_ATR      = 0.0012          # 1dk ATR >= %0.12
SC_MIN_RR       = 1.5             # hedef2/stop >= 1.5 (scalp icin sikilastirilmis)
SC_MIN_CONF     = 62              # scalp guveni >= 62 -> goster (yuksek bar)
SC_PULLBACK_ATR = 0.55            # fiyat EMA9'a <= 0.55xATR ise taze pullback (en iyi giris)
_SC_CACHE = {"ts": 0, "data": None}
_SC_LOCK  = threading.Lock()


def scalp_board(max_detail=18):
    """1DK SCALP motoru — Pro Trend cekirdegi + scalp giris kalitesi kapisi.
    Yalnizca (a) 5dk+1dk ayni yon+guc, (b) 1dk'da EMA9'a taze pullback / dipten
    donus / temiz breakout, (c) onay mumu olan adaylari verir. Stop salinim
    yapisinin ardinda, hedefler 4-5x1dk ATR projeksiyonu. 20 sn cache."""
    with _SC_LOCK:
        if _SC_CACHE["data"] is not None and time.time() - _SC_CACHE["ts"] < 20:
            return _SC_CACHE["data"]

    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t["turnover24h"]); fr = float(t.get("fundingRate") or 0)
            pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError):
            continue
        if tov < SC_MIN_TURNOVER:
            continue
        liq.append((s, tov, fr, pc))
    liq.sort(key=lambda x: x[1], reverse=True)

    def detail(item):
        s, tov, fr, pc = item
        try:
            k = _bybit_kl(s, "1", 300)
            o1 = [float(x[1]) for x in k]; h1 = [float(x[2]) for x in k]
            l1 = [float(x[3]) for x in k]; c1 = [float(x[4]) for x in k]
            v1 = [float(x[5]) for x in k]
        except Exception:
            return None
        n = len(c1)
        if n < 120:
            return None
        price = c1[-1]

        # ── 1dk (giris zaman dilimi) ──
        e9  = _ema(c1[-60:], 9); e21 = _ema(c1[-80:], 21)
        e50 = _ema(c1[-150:], 50); e9_prev = _ema(c1[-63:-3], 9)
        if None in (e9, e21, e50, e9_prev):
            return None
        adx1 = _adx(h1[-120:], l1[-120:], c1[-120:], 14)
        if adx1 is None:
            return None
        adx1, pdi1, mdi1 = adx1
        atr_abs = sum(h1[i] - l1[i] for i in range(n - 14, n)) / 14
        atr_pct = atr_abs / price if price else 0
        if atr_pct < SC_MIN_ATR:
            return None
        m5  = price / c1[-6]  - 1 if n >= 6  else 0
        m15 = price / c1[-16] - 1 if n >= 16 else 0
        consec = 1; cur_up = c1[-1] > o1[-1]
        for i in range(n - 2, 0, -1):
            if (c1[i] > o1[i]) == cur_up:
                consec += 1
            else:
                break
        rv = sum(v1[-3:]) / 3
        seg = v1[-60:-3] if n >= 63 else v1[:-3] or v1
        bv = sorted(seg)[len(seg) // 2] if seg else (rv or 1)
        vspike = rv / bv if bv > 0 else 0

        # ── 5dk (ust zaman dilimi = trend yonu) ──
        O5, H5, L5, C5, V5 = _resample(o1, h1, l1, c1, v1, 5)
        if len(C5) < 55:
            return None
        E9_5 = _ema(C5[-40:], 9); E21_5 = _ema(C5[-45:], 21)
        E50_5 = _ema(C5[-55:], 50); E9_5p = _ema(C5[-43:-3], 9)
        if None in (E9_5, E21_5, E50_5, E9_5p):
            return None
        adx5 = _adx(H5[-60:], L5[-60:], C5[-60:], 14)
        if adx5 is None:
            return None
        adx5, pdi5, mdi5 = adx5

        # ── yon: 5dk tam dizilim + 1dk ayni yon + guc + taze ──
        side = None
        htf_long  = E9_5 > E21_5 > E50_5 and E9_5 > E9_5p and pdi5 > mdi5
        htf_short = E9_5 < E21_5 < E50_5 and E9_5 < E9_5p and mdi5 > pdi5
        ltf_long  = price > e9 > e21 and e9 > e9_prev and pdi1 > mdi1
        ltf_short = price < e9 < e21 and e9 < e9_prev and mdi1 > pdi1
        if htf_long and ltf_long:
            ext = price / e21 - 1
            if (adx1 >= SC_MIN_ADX and adx5 >= SC_MIN_ADX_5M
                    and 0 <= ext <= SC_MAX_EXT_1M and m5 > 0
                    and consec <= SC_MAX_CONSEC and price > e50):
                side = "LONG"
        elif htf_short and ltf_short:
            ext = 1 - price / e21
            if (adx1 >= SC_MIN_ADX and adx5 >= SC_MIN_ADX_5M
                    and 0 <= ext <= SC_MAX_EXT_1M and m5 < 0
                    and consec <= SC_MAX_CONSEC and price < e50):
                side = "SHORT"
        if side is None:
            return None

        # ── SCALP GIRIS KALITESI: pullback / dipten donus / breakout + onay mumu ──
        last_bull = c1[-1] > o1[-1]; prev_bull = c1[-2] > o1[-2]
        dist9 = abs(price / e9 - 1)
        near9 = dist9 <= SC_PULLBACK_ATR * atr_pct
        sw_hi = max(h1[-7:-1]); sw_lo = min(l1[-7:-1])
        if side == "LONG":
            confirm  = last_bull or (near9 and price > e9)
            breakout = price >= sw_hi
            reclaim  = (not prev_bull) and last_bull and near9
        else:
            confirm  = (not last_bull) or (near9 and price < e9)
            breakout = price <= sw_lo
            reclaim  = prev_bull and (not last_bull) and near9
        if not confirm:
            return None
        if not (near9 or breakout or reclaim):
            return None
        setup = ("PULLBACK" if (near9 or reclaim) else "BREAKOUT")

        # ── scalp guveni (0-100) ──
        adx_sc  = max(0, min((adx1 - 20) / 25, 1)) * 24
        htf_sc  = max(0, min((adx5 - 18) / 22, 1)) * 18
        di_gap  = abs(pdi1 - mdi1) / (pdi1 + mdi1) if (pdi1 + mdi1) else 0
        dir_sc  = min(di_gap * 1.4, 1) * 14
        fresh   = 1 - min(dist9 / (SC_PULLBACK_ATR * atr_pct * 2 or 1), 1)
        fresh_sc = fresh * 18
        vol_sc  = max(0, min((vspike - 0.8) / 1.7, 1)) * 10
        setup_sc = 16 if setup == "PULLBACK" else 10
        conf = round(adx_sc + htf_sc + dir_sc + fresh_sc + vol_sc + setup_sc, 1)
        if conf < SC_MIN_CONF:
            return None

        # ── plan: stop = salinim yapisinin ardi (ATR sinirli), hedef = 4-5x1dk ATR ──
        entry = price
        drift = 0.55 * atr_abs
        exp_candles = 4 if conf < 74 else 5
        if side == "LONG":
            raw = entry - sw_lo + 0.10 * atr_abs
            stop_dist = min(max(raw, 0.9 * atr_abs, 0.0015 * entry), 0.012 * entry)
            stop = entry - stop_dist
        else:
            raw = sw_hi - entry + 0.10 * atr_abs
            stop_dist = min(max(raw, 0.9 * atr_abs, 0.0015 * entry), 0.012 * entry)
            stop = entry + stop_dist
        stop_pct = stop_dist / entry
        tp1_dist = 2.5 * drift
        tp2_dist = (exp_candles + 0.5) * drift
        if side == "LONG":
            tp1, tp2 = entry + tp1_dist, entry + tp2_dist
        else:
            tp1, tp2 = entry - tp1_dist, entry - tp2_dist
        rr = round(tp2_dist / stop_dist, 2) if stop_dist else 0
        if rr < SC_MIN_RR:
            return None

        return {
            "symbol": s, "side": side, "price": round(price, 8),
            "entry": round(entry, 8), "stop": round(stop, 8),
            "tp1": round(tp1, 8), "tp2": round(tp2, 8),
            "stop_pct": round(stop_pct * 100, 2),
            "tp1_pct": round(tp1_dist / entry * 100, 2),
            "tp2_pct": round(tp2_dist / entry * 100, 2),
            "conf": conf, "exp_candles": exp_candles, "rr": rr,
            "setup": setup, "dist9": round(dist9 * 100, 3),
            "adx1": round(adx1, 1), "adx5": round(adx5, 1),
            "m5": round(m5, 4), "m15": round(m15, 4),
            "vspike": round(vspike, 2), "atr_pct": round(atr_pct * 100, 3),
            "consec": consec, "funding": round(fr, 6), "pc24": round(pc, 4),
            "vol24": int(tov),
        }

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(detail, liq[:SC_UNIVERSE]))
    out = [r for r in res if r]
    out.sort(key=lambda r: r["conf"], reverse=True)
    out = out[:max_detail]
    data = {
        "candidates": out, "count": len(out),
        "longs": sum(1 for r in out if r["side"] == "LONG"),
        "shorts": sum(1 for r in out if r["side"] == "SHORT"),
        "ts": int(time.time()),
    }
    with _SC_LOCK:
        _SC_CACHE["ts"] = time.time(); _SC_CACHE["data"] = data
    return data


def _adx(hi, lo, cl, n=14):
    """Wilder ADX + son +DI/-DI. Trend gucu (>20-25 = gercek trend)."""
    m = len(cl)
    if m < 2 * n + 2:
        return None
    tr, pdm, ndm = [], [], []
    for i in range(1, m):
        up = hi[i] - hi[i - 1]
        dn = lo[i - 1] - lo[i]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])))

    def wilder(xs):
        s = sum(xs[:n]); out = [s]
        for x in xs[n:]:
            s = s - s / n + x; out.append(s)
        return out

    atr, pd, nd = wilder(tr), wilder(pdm), wilder(ndm)
    dxs, last_pdi, last_mdi = [], 0.0, 0.0
    for i in range(len(atr)):
        if atr[i] == 0:
            continue
        pdi = 100 * pd[i] / atr[i]; mdi = 100 * nd[i] / atr[i]
        last_pdi, last_mdi = pdi, mdi
        den = pdi + mdi
        if den:
            dxs.append(100 * abs(pdi - mdi) / den)
    if len(dxs) < n:
        adx = sum(dxs) / len(dxs) if dxs else 0.0
    else:
        adx = sum(dxs[-n:]) / n
    return adx, last_pdi, last_mdi


def _resample(o, h, l, c, v, f=5):
    """1dk OHLCV dizilerini f-kat (5) ust zaman dilimine indirger. En yeni,
    henuz kapanmamis kismi kova atilir (son kapanan tam kovaya kadar)."""
    n = len(c)
    start = n % f
    O, H, L, C, V = [], [], [], [], []
    i = start
    while i + f <= n:
        O.append(o[i]); H.append(max(h[i:i + f])); L.append(min(l[i:i + f]))
        C.append(c[i + f - 1]); V.append(sum(v[i:i + f])); i += f
    return O, H, L, C, V


def pro_trend_board(max_detail=20):
    """PROFESYONEL trend scalp — MTF. 5dk = trend yonu/gucu, 1dk = taze giris ve
    4-5 mum devam olasiligi. Sadece iki zaman dilimi de ayni yonu ve gucu teyit
    eden, 1dk'da asiri uzamamis (taze) adaylari verir. Hedefler 4-5x1dk ATR
    projeksiyonu. 25 sn cache."""
    with _PT_LOCK:
        if _PT_CACHE["data"] is not None and time.time() - _PT_CACHE["ts"] < 25:
            return _PT_CACHE["data"]

    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t["turnover24h"]); fr = float(t.get("fundingRate") or 0)
            pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError):
            continue
        if tov < PT_MIN_TURNOVER:
            continue
        liq.append((s, tov, fr, pc))
    liq.sort(key=lambda x: x[1], reverse=True)

    def detail(item):
        s, tov, fr, pc = item
        try:
            k = _bybit_kl(s, "1", 300)           # son ~5s, 1dk mum
            o1 = [float(x[1]) for x in k]
            h1 = [float(x[2]) for x in k]
            l1 = [float(x[3]) for x in k]
            c1 = [float(x[4]) for x in k]
            v1 = [float(x[5]) for x in k]
        except Exception:
            return None
        n = len(c1)
        if n < 120:
            return None
        price = c1[-1]

        # ── 1dk (giris zaman dilimi) ──
        e9  = _ema(c1[-60:], 9)
        e21 = _ema(c1[-80:], 21)
        e50 = _ema(c1[-150:], 50)
        e9_prev = _ema(c1[-63:-3], 9)            # 3 mum onceki EMA9 -> egim
        if None in (e9, e21, e50, e9_prev):
            return None
        adx1 = _adx(h1[-120:], l1[-120:], c1[-120:], 14)
        if adx1 is None:
            return None
        adx1, pdi1, mdi1 = adx1
        # ATR (1dk, son 14)
        atr_abs = sum(h1[i] - l1[i] for i in range(n - 14, n)) / 14
        atr_pct = atr_abs / price if price else 0
        # 1dk momentum
        m5  = price / c1[-6]  - 1 if n >= 6  else 0   # son 5dk (5x1dk)
        m15 = price / c1[-16] - 1 if n >= 16 else 0   # son 15dk
        # ard arda ayni renk mum sayisi (tukenme filtresi)
        consec = 0
        for i in range(n - 1, 0, -1):
            up = c1[i] > o1[i]; dn = c1[i] < o1[i]
            if consec == 0:
                cur_up = up
                if not up and not dn:
                    break
                consec = 1
            else:
                if (up and cur_up) or (dn and not cur_up):
                    consec += 1
                else:
                    break
        # hacim destegi (son 3 vs taban)
        rv = sum(v1[-3:]) / 3 if n >= 3 else 0
        seg = v1[-60:-3] if n >= 63 else v1[:-3] or v1
        bv = sorted(seg)[len(seg) // 2] if seg else (rv or 1)
        vspike = rv / bv if bv > 0 else 0

        # ── 5dk (ust zaman dilimi = TREND YONU, ayni mantik) ──
        O5, H5, L5, C5, V5 = _resample(o1, h1, l1, c1, v1, 5)
        if len(C5) < 55:
            return None
        E9_5  = _ema(C5[-40:], 9)
        E21_5 = _ema(C5[-45:], 21)
        E50_5 = _ema(C5[-55:], 50)
        E9_5p = _ema(C5[-43:-3], 9)
        if None in (E9_5, E21_5, E50_5, E9_5p):
            return None
        adx5 = _adx(H5[-60:], L5[-60:], C5[-60:], 14)
        if adx5 is None:
            return None
        adx5, pdi5, mdi5 = adx5

        # ── yon karari: 5dk tam dizilim + 1dk ayni yon + taze + guclu ──
        side = None
        htf_long  = E9_5 > E21_5 > E50_5 and E9_5 > E9_5p and pdi5 > mdi5
        htf_short = E9_5 < E21_5 < E50_5 and E9_5 < E9_5p and mdi5 > pdi5
        ltf_long  = price > e9 > e21 and e9 > e9_prev and pdi1 > mdi1
        ltf_short = price < e9 < e21 and e9 < e9_prev and mdi1 > pdi1

        if atr_pct < PT_MIN_ATR:
            return None
        if htf_long and ltf_long:
            ext = price / e21 - 1
            if (adx1 >= PT_MIN_ADX and adx5 >= PT_MIN_ADX_5M
                    and 0 <= ext <= PT_MAX_EXT_1M and m5 > 0
                    and consec <= PT_MAX_CONSEC and price > e50):
                side = "LONG"
        elif htf_short and ltf_short:
            ext = 1 - price / e21
            if (adx1 >= PT_MIN_ADX and adx5 >= PT_MIN_ADX_5M
                    and 0 <= ext <= PT_MAX_EXT_1M and m5 < 0
                    and consec <= PT_MAX_CONSEC and price < e50):
                side = "SHORT"
        if side is None:
            return None

        # ── devam guveni (0-100): iki TF hizasi + guc + tazelik + hacim ──
        adx_sc  = max(0, min((adx1 - 20) / 25, 1)) * 25          # 1dk trend gucu
        htf_sc  = max(0, min((adx5 - 18) / 22, 1)) * 20          # 5dk trend gucu
        di_gap  = abs(pdi1 - mdi1) / (pdi1 + mdi1) if (pdi1 + mdi1) else 0
        dir_sc  = min(di_gap * 1.4, 1) * 15                      # yon baskinligi
        fresh   = 1 - min(ext / PT_MAX_EXT_1M, 1)                # EMA'ya yakin = taze
        fresh_sc = fresh * 22
        vol_sc  = max(0, min((vspike - 0.8) / 1.7, 1)) * 10      # hacim destegi
        room_sc = max(0, min((PT_MAX_CONSEC - consec) / PT_MAX_CONSEC, 1)) * 8
        conf = round(adx_sc + htf_sc + dir_sc + fresh_sc + vol_sc + room_sc, 1)
        if conf < PT_MIN_CONF:
            return None

        # ── plan: stop = 1dk gurultu (ATR bazli), hedef = 4-5 x 1dk ATR projeksiyonu ──
        entry = price
        drift = 0.55 * atr_abs                                   # trend mumu basi net beklenen
        exp_candles = 4 if conf < 72 else 5                      # guven -> hedef mum sayisi
        stop_dist = min(max(1.3 * atr_abs, 0.0015 * entry), 0.012 * entry)
        stop_pct = stop_dist / entry
        tp1_dist = 2.5 * drift
        tp2_dist = (exp_candles + 0.5) * drift
        if side == "LONG":
            stop = entry - stop_dist
            tp1, tp2 = entry + tp1_dist, entry + tp2_dist
        else:
            stop = entry + stop_dist
            tp1, tp2 = entry - tp1_dist, entry - tp2_dist
        tp1_pct = tp1_dist / entry * 100
        tp2_pct = tp2_dist / entry * 100
        rr = round(tp2_dist / stop_dist, 2) if stop_dist else 0
        if rr < PT_MIN_RR:
            return None

        return {
            "symbol": s, "side": side, "price": round(price, 8),
            "entry": round(entry, 8), "stop": round(stop, 8),
            "tp1": round(tp1, 8), "tp2": round(tp2, 8),
            "stop_pct": round(stop_pct * 100, 2),
            "tp1_pct": round(tp1_pct, 2), "tp2_pct": round(tp2_pct, 2),
            "conf": conf, "exp_candles": exp_candles, "rr": rr,
            "adx1": round(adx1, 1), "adx5": round(adx5, 1),
            "m5": round(m5, 4), "m15": round(m15, 4),
            "vspike": round(vspike, 2), "atr_pct": round(atr_pct * 100, 3),
            "consec": consec, "funding": round(fr, 6), "pc24": round(pc, 4),
            "vol24": int(tov),
        }

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(detail, liq[:PT_UNIVERSE]))
    out = [r for r in res if r]
    out.sort(key=lambda r: r["conf"], reverse=True)
    out = out[:max_detail]
    data = {
        "candidates": out, "count": len(out),
        "longs": sum(1 for r in out if r["side"] == "LONG"),
        "shorts": sum(1 for r in out if r["side"] == "SHORT"),
        "ts": int(time.time()),
    }
    with _PT_LOCK:
        _PT_CACHE["ts"] = time.time(); _PT_CACHE["data"] = data
    return data


def trend_board(max_detail=24):
    """ŞU AN aktif trendde kosan Bybit coinlerini (LONG + SHORT) tek panoda verir.
    LONG:  fiyat > EMA9 > EMA21, EMA9 yukari egimli, son 15dk artiyor, asiri uzamamis.
    SHORT: aynanin tersi. Her aday icin anlik giris + dar stop + hizli hedefler."""
    with _TR_LOCK:
        if _TR_CACHE["data"] is not None and time.time() - _TR_CACHE["ts"] < 30:
            return _TR_CACHE["data"]

    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t["turnover24h"]); fr = float(t.get("fundingRate") or 0)
            pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError):
            continue
        if tov < TR_MIN_TURNOVER:
            continue
        liq.append((s, tov, fr, pc))
    liq.sort(key=lambda x: x[1], reverse=True)

    def detail(item):
        s, tov, fr, pc = item
        try:
            k = _bybit_kl(s, "5", 60)            # son ~5 saat, 5dk mum
            hi = [float(x[2]) for x in k]
            lo = [float(x[3]) for x in k]
            cl = [float(x[4]) for x in k]
            vo = [float(x[5]) for x in k]
        except Exception:
            return None
        n = len(cl)
        if n < 30:
            return None
        price = cl[-1]
        ema9  = _ema(cl[-30:], 9)
        ema21 = _ema(cl[-40:], 21) if n >= 40 else _ema(cl, 21)
        ema9_prev = _ema(cl[-33:-3], 9) if n >= 33 else ema9   # 3 mum onceki EMA9
        if ema9 is None or ema21 is None or ema9_prev is None:
            return None
        # momentum
        m15 = price / cl[-4] - 1 if n >= 4 else 0     # son 15dk (3x5dk)
        m30 = price / cl[-7] - 1 if n >= 7 else 0     # son 30dk
        h1  = price / cl[-13] - 1 if n >= 13 else 0   # son 1 saat
        # 1s tepe/dip — asiri uzama ve konum icin
        hi1h = max(hi[-12:]) if n >= 12 else max(hi)
        lo1h = min(lo[-12:]) if n >= 12 else min(lo)
        # ATR (son 14 mum)
        trs = [hi[i] - lo[i] for i in range(n - 14, n)]
        atr_abs = sum(trs) / 14
        atr_pct = atr_abs / price if price else 0
        # hacim: son 3 mum vs onceki tabana gore
        recent_v = sum(vo[-3:]) / 3 if n >= 3 else 0
        base_seg = vo[-30:-3] if n >= 33 else vo[:-3] or vo
        base_v = sorted(base_seg)[len(base_seg) // 2] if base_seg else (recent_v or 1)
        vspike = recent_v / base_v if base_v > 0 else 0

        side = None
        if price > ema9 > ema21 and ema9 > ema9_prev and m15 >= TR_MIN_M15 and m30 > 0:
            ext = price / ema21 - 1                       # EMA21 ustunde ne kadar
            near_hi = price >= hi1h * 0.985               # 1s tepesine yakin (hala guclu)
            if ext <= TR_MAX_EXT and near_hi:
                side = "LONG"
        elif price < ema9 < ema21 and ema9 < ema9_prev and m15 <= -TR_MIN_M15 and m30 < 0:
            ext = 1 - price / ema21
            near_lo = price <= lo1h * 1.015
            if ext <= TR_MAX_EXT and near_lo:
                side = "SHORT"
        if side is None:
            return None

        # plan — anlik giris, dar ATR-stop (%0.5-%2), hizli hedefler (R-katli)
        entry = price
        if side == "LONG":
            stop = entry - 1.0 * atr_abs
            stop_pct = (entry - stop) / entry if entry else 0
            stop_pct = min(max(stop_pct, 0.005), 0.02)
            stop = entry * (1 - stop_pct)
            sd = entry - stop
            tp1 = entry + 1.2 * sd
            tp2 = entry + 2.2 * sd
            tp1_pct = (tp1 - entry) / entry * 100
            tp2_pct = (tp2 - entry) / entry * 100
            trend_sep = ema9 / ema21 - 1
            mom = m15
        else:
            stop = entry + 1.0 * atr_abs
            stop_pct = (stop - entry) / entry if entry else 0
            stop_pct = min(max(stop_pct, 0.005), 0.02)
            stop = entry * (1 + stop_pct)
            sd = stop - entry
            tp1 = entry - 1.2 * sd
            tp2 = entry - 2.2 * sd
            tp1_pct = (entry - tp1) / entry * 100
            tp2_pct = (entry - tp2) / entry * 100
            trend_sep = ema21 / ema9 - 1
            mom = -m15

        # skor: taze momentum + trend dizilimi (EMA ayrigi) + hacim + 1s momentum
        score = (min(mom * 300, 4) + min(trend_sep * 60, 2)
                 + min(vspike, 5) * 0.4 + min(abs(h1) * 60, 2))
        return {
            "symbol": s, "side": side, "price": round(price, 8),
            "entry": round(entry, 8), "stop": round(stop, 8),
            "tp1": round(tp1, 8), "tp2": round(tp2, 8),
            "stop_pct": round(stop_pct * 100, 2),
            "tp1_pct": round(tp1_pct, 2), "tp2_pct": round(tp2_pct, 2),
            "m15": round(m15, 4), "m30": round(m30, 4), "h1": round(h1, 4),
            "vspike": round(vspike, 2), "atr_pct": round(atr_pct * 100, 2),
            "funding": round(fr, 6), "pc24": round(pc, 4),
            "vol24": int(tov), "score": round(score, 2),
        }

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(detail, liq[:TR_UNIVERSE]))
    out = [r for r in res if r]
    out.sort(key=lambda r: r["score"], reverse=True)
    out = out[:max_detail]
    data = {
        "candidates": out, "count": len(out),
        "longs": sum(1 for r in out if r["side"] == "LONG"),
        "shorts": sum(1 for r in out if r["side"] == "SHORT"),
        "ts": int(time.time()),
    }
    with _TR_LOCK:
        _TR_CACHE["ts"] = time.time(); _TR_CACHE["data"] = data
    return data


# ─── TARAMA + PLAN (arka planda) ────────────────────────────────
def _run_scan():
    with LOCK:
        STATE["scan"] = {"status": "running", "candidates": [], "watchlist": [],
                         "ts": int(time.time()), "msg": "Taranıyor..."}
    try:
        res = scan.screen(verbose=False)
        cands = res["candidates"]
        watchlist = res["watchlist"]
        enriched = []
        for c in cands:
            d = decide.decide(c)
            item = dict(c)
            if d:
                # provizyonel referans: son 5 dk H/L
                k = mexc.klines(c["symbol"], minutes=6, interval="Min1")
                if len(k) >= 5:
                    rh = max(x["h"] for x in k[-5:])
                    rl = min(x["l"] for x in k[-5:])
                else:
                    rh, rl = d["last"] * 1.003, d["last"] * 0.997
                p = decide.position_plan(d, rh, rl)
                item["tradeable"] = True
                item["side"] = d["side"]
                item["setup"] = d["setup"]
                item["ret_8h"] = d["ret_8h"]
                item["ret_1h"] = d["ret_1h"]
                item["atr_pct"] = d["atr_pct"]
                item["plan"] = p
                item["ref_high"] = rh
                item["ref_low"] = rl
            else:
                item["tradeable"] = False
            enriched.append(item)
        with LOCK:
            STATE["scan"] = {"status": "done", "candidates": enriched,
                             "watchlist": watchlist, "ts": int(time.time()),
                             "msg": f"{len(enriched)} aday"}
    except Exception as e:
        with LOCK:
            STATE["scan"] = {"status": "error", "candidates": [],
                             "watchlist": [], "ts": int(time.time()),
                             "msg": str(e)}


# ─── CANLI TAKİP (watch) — durum makinesi, arka plan thread'i ──
class Watcher:
    def __init__(self, symbol):
        self.symbol = symbol
        self.stop_flag = False
        self.confirm_entry = False
        self.d = None
        self.plan = None
        self.data = {
            "symbol": symbol,
            "phase": "init",          # init|reference|waiting|entry|monitor|done|cancel|reject
            "message": "Başlatılıyor...",
            "side": None,
            "plan": None,
            "ref_high": None,
            "ref_low": None,
            "price": None,
            "oi_usd": None,
            "oi_drop_pct": None,
            "elapsed_min": None,
            "log": [],
            "signal": None,           # 'ENTRY' | 'EXIT_TP' | 'EXIT_TIME' | None
        }

    def _log(self, msg):
        stamp = dt.datetime.now(TR).strftime("%H:%M:%S")
        self.data["log"].insert(0, f"[{stamp}] {msg}")
        self.data["log"] = self.data["log"][:40]
        self._push()

    def _set(self, **kw):
        self.data.update(kw)
        self._push()

    def _push(self):
        with LOCK:
            STATE["watch"] = dict(self.data)

    def run(self):
        try:
            self._evaluate()
        except Exception as e:
            self._set(phase="cancel", message=f"Hata: {e}")

    def _evaluate(self):
        sym = self.symbol
        det = mexc.funding_detail(sym)
        st = mexc.funding_stats(sym, pages=C.FUNDING_HIST_PAGES)
        snap = [r for r in mexc.snapshot() if r["symbol"] == sym]
        if not det or not st or not snap:
            self._set(phase="reject", message="Bu sembol için yeterli veri yok.")
            return
        r = snap[0]
        fnorm = r["fundingRate"] * (8.0 / det["collectCycle"])
        z = (fnorm - st["mean_norm"]) / st["std_norm"]
        crowd = "LONG" if fnorm > 0 else "SHORT"
        cand = {"symbol": sym, "crowd": crowd, "z": round(z, 2),
                "basis": r["basis"], "funding_norm": fnorm}
        d = decide.decide(cand)
        if not d:
            self._set(phase="reject",
                      message=f"{sym} şu an tradeable çeyrekte DEĞİL "
                              f"(kalabalık fiyatla çelişmiyor). İşlem açma.")
            return
        self.d = d
        self._set(phase="reference", side=d["side"],
                  message=f"Yön: {d['side']} ({d['setup']}). "
                          f"Referans mum için 5 dk izleniyor...")
        self._log(f"Yön belirlendi: {d['side']} — z={z:.1f}, ATR={d['atr_pct']*100:.2f}%")

        # ── 1) Referans mum: 5 dk ──
        t0 = time.time()
        rh = rl = None
        while time.time() - t0 < 5 * 60 and not self.stop_flag:
            k = mexc.klines(sym, minutes=6, interval="Min1")
            if len(k) >= 5:
                rh = max(x["h"] for x in k[-5:])
                rl = min(x["l"] for x in k[-5:])
                self._set(ref_high=rh, ref_low=rl, price=k[-1]["c"])
            time.sleep(15)
        if self.stop_flag:
            return self._set(phase="cancel", message="Durduruldu.")
        if rh is None:
            return self._set(phase="cancel", message="Referans mum oluşmadı.")

        self.plan = decide.position_plan(d, rh, rl)
        self._set(plan=self.plan, ref_high=rh, ref_low=rl)
        trig = self.plan["entry"]
        self._log(f"Referans: H={rh:.6g} L={rl:.6g}. Tetik: "
                  f"{'H kır' if d['side']=='LONG' else 'L kır'} = {trig:.6g}")

        # ── 2) Tetik bekle (T+25) ──
        self._set(phase="waiting",
                  message=f"Kırılım tetiği bekleniyor (max {C.ENTRY_WINDOW_MIN} dk)...")
        deadline = time.time() + C.ENTRY_WINDOW_MIN * 60
        hit = False
        while time.time() < deadline and not self.stop_flag:
            k = mexc.klines(sym, minutes=3, interval="Min1")
            if k:
                price = k[-1]["c"]
                self._set(price=price)
                if d["side"] == "LONG" and price > trig:
                    hit = True; break
                if d["side"] == "SHORT" and price < trig:
                    hit = True; break
            time.sleep(15)
        if self.stop_flag:
            return self._set(phase="cancel", message="Durduruldu.")
        if not hit:
            self._log("25 dk içinde tetik gelmedi.")
            return self._set(phase="done", signal=None,
                             message="OLAY İPTAL — tetik gelmedi. İşlem açma.")

        # ── GİRİŞ SİNYALİ ──
        self._set(phase="entry", signal="ENTRY",
                  message=f"GİRİŞ SİNYALİ: {d['side']} {sym}. "
                          f"MEXC'te pozisyonu aç, sonra 'Pozisyonu Açtım'a bas.")
        self._log(f"🟢 GİRİŞ SİNYALİ — {d['side']} @ ~{trig:.6g}")

        # kullanıcı onayını bekle
        while not self.confirm_entry and not self.stop_flag:
            time.sleep(1)
        if self.stop_flag:
            return self._set(phase="cancel", message="Durduruldu (giriş öncesi).")

        # ── 3) Çıkış izleme ──
        self._set(phase="monitor", signal=None,
                  message="Pozisyon açık. Çıkış izleniyor: OI durması + T+90dk.")
        self._log("Çıkış izlemesi başladı.")
        oi_peak = mexc.current_oi_usd(sym) or 0
        entry_time = time.time()
        stall = 0
        while not self.stop_flag:
            elapsed = (time.time() - entry_time) / 60
            self._set(elapsed_min=round(elapsed, 1))
            if elapsed > C.TIME_STOP_MIN:
                self._log("⏹ Zaman stopu (T+90dk).")
                return self._set(phase="done", signal="EXIT_TIME",
                                 message="ZAMAN STOPU — KOŞULSUZ KAPAT. Olay bitti.")
            oi = mexc.current_oi_usd(sym)
            k = mexc.klines(sym, minutes=2, interval="Min1")
            if k:
                self._set(price=k[-1]["c"])
            if oi is not None:
                if oi > oi_peak:
                    oi_peak = oi
                drop = (oi_peak - oi) / oi_peak if oi_peak else 0
                self._set(oi_usd=oi, oi_drop_pct=round(drop * 100, 1))
                if drop > 0.02:
                    stall += 1
                if stall >= 3 and drop > 0.02:
                    self._log("🟡 OI tepeden düşüp stabilize oldu — yakıt azalıyor.")
                    return self._set(phase="done", signal="EXIT_TP",
                                     message="KÂR AL / küçült — OI yakıtı bitiyor.")
            time.sleep(20)
        self._set(phase="cancel", message="Durduruldu.")


_watcher = None


def start_watch(symbol):
    global _watcher
    symbol = symbol.upper()
    if not symbol.endswith("_USDT"):
        symbol += "_USDT"
    if _watcher and _watcher.data["phase"] in ("reference", "waiting",
                                               "entry", "monitor"):
        _watcher.stop_flag = True
    _watcher = Watcher(symbol)
    STATE["watch"] = dict(_watcher.data)
    threading.Thread(target=_watcher.run, daemon=True).start()


def confirm_entry():
    if _watcher:
        _watcher.confirm_entry = True


def stop_watch():
    if _watcher:
        _watcher.stop_flag = True


# ─── PLAN "EN İYİ GİRİŞ" — canlı 1dk RETEST izleyici (Bybit) ─────
# Plan/Short-Plan adayı için: önce 15dk KAPANIŞI seviyeyi kırsın (kullanıcının
# kuralı), sonra 1dk'da kırılan seviyeye RETEST gelsin ve o seviye TUTSUN
# (reclaim + EMA9 + hacim teyidi). Tutunca 'ŞİMDİ GİR' bildirimi atar.
# Sadece-retest modu: retest gelmezse sinyal YOK, kovalama yok.
PRW_BREAK_TTL_MIN   = 360    # kırılımı bu kadar dk bekle, gelmezse iptal
PRW_RETEST_TTL_MIN  = 45     # kırılımdan sonra retest+onay için pencere
PRW_TOUCH_BAND      = 0.0015 # fiyat seviyeye %0.15 yaklaşınca "retest dokundu"
PRW_VOL_MULT        = 1.5    # onay mumunda hacim >= 1.5x son 20 mum ort.
PRW_POLL_SEC        = 15

_plan_watchers = {}
_PRW_LOCK = threading.Lock()


def _ema(xs, n):
    if not xs:
        return None
    k = 2.0 / (n + 1)
    e = xs[0]
    for v in xs[1:]:
        e = v * k + e * (1 - k)
    return e


class PlanRetestWatcher:
    """Tek bir aday için 15dk kırılım + 1dk retest onayı izler."""
    def __init__(self, symbol, side, level, entry, stop, tp1, tp2):
        self.symbol = symbol
        self.side = side                # LONG | SHORT
        self.level = float(level)       # kırılan seviye (LONG: direnç, SHORT: destek)
        self.entry = entry; self.stop = stop
        self.tp1 = tp1; self.tp2 = tp2
        self.stop_flag = False
        self.data = {
            "symbol": symbol, "side": side, "level": self.level,
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
            "phase": "wait_break",   # wait_break|wait_retest|fired|expired|cancel|error
            "message": "Kırılım bekleniyor (15dk kapanışı)...",
            "price": None, "touched": False,
            "log": [], "ts": int(time.time()),
        }

    def _log(self, msg):
        stamp = dt.datetime.now(TR).strftime("%H:%M:%S")
        self.data["log"].insert(0, f"[{stamp}] {msg}")
        self.data["log"] = self.data["log"][:30]
        self._push()

    def _set(self, **kw):
        self.data.update(kw); self._push()

    def _push(self):
        with LOCK:
            STATE["plan_watch"][self.symbol] = dict(self.data)

    def _broke(self, close):
        return close > self.level if self.side == "LONG" else close < self.level

    def _touched(self, low, high):
        # fiyat kırdıktan sonra seviyeye geri dokundu mu?
        if self.side == "LONG":
            return low <= self.level * (1 + PRW_TOUCH_BAND)
        return high >= self.level * (1 - PRW_TOUCH_BAND)

    def run(self):
        try:
            self._evaluate()
        except Exception as e:
            self._set(phase="error", message=f"Hata: {e}")

    def _evaluate(self):
        sym = self.symbol
        # ── 1) 15dk KAPANIŞI seviyeyi kırsın ──
        deadline = time.time() + PRW_BREAK_TTL_MIN * 60
        self._log(f"İzleme başladı. {self.side} — seviye {self.level:.6g}")
        broke = False
        while time.time() < deadline and not self.stop_flag:
            try:
                k = _bybit_kl(sym, "15", 3)   # son kapanmış 15dk mumu
                closed = k[-2]                 # -1 henüz oluşuyor; -2 = son KAPANAN
                c = float(closed[4]); self._set(price=float(k[-1][4]))
                if self._broke(c):
                    broke = True
                    self._log(f"✅ 15dk kapanış kırdı: {c:.6g}")
                    break
            except Exception:
                pass
            time.sleep(PRW_POLL_SEC)
        if self.stop_flag:
            return self._set(phase="cancel", message="Durduruldu.")
        if not broke:
            return self._set(phase="expired",
                             message="Kırılım gelmedi (süre doldu). Sinyal yok.")

        # ── 2) 1dk'da RETEST + TUTMA onayı ──
        self._set(phase="wait_retest",
                  message="Kırıldı. 1dk retest + tutma onayı bekleniyor...")
        rdeadline = time.time() + PRW_RETEST_TTL_MIN * 60
        touched = False
        while time.time() < rdeadline and not self.stop_flag:
            try:
                k = _bybit_kl(sym, "1", 40)
                cur = float(k[-1][4]); self._set(price=cur)
                closed = k[-2]                 # son KAPANAN 1dk mumu
                o = float(closed[1]); h = float(closed[2])
                lo = float(closed[3]); cl = float(closed[4]); vol = float(closed[5])
                closes = [float(x[4]) for x in k[:-1]]     # kapanan mumlar
                vols = [float(x[5]) for x in k[:-1]]
                ema9 = _ema(closes[-30:], 9)
                vavg = sum(vols[-20:]) / min(20, len(vols[-20:] or [1]))

                if not touched and self._touched(lo, h):
                    touched = True
                    self._set(touched=True)
                    self._log(f"↩︎ Retest dokundu (L={lo:.6g} H={h:.6g}).")

                if touched:
                    vol_ok = vol >= PRW_VOL_MULT * vavg
                    if self.side == "LONG":
                        hold = cl > self.level and (ema9 is None or cl > ema9)
                    else:
                        hold = cl < self.level and (ema9 is None or cl < ema9)
                    if hold and vol_ok:
                        return self._fire(cl, vol, vavg)
                    if hold and not vol_ok:
                        self._log(f"Tutma var ama hacim zayıf ({vol:.0f} < "
                                  f"{PRW_VOL_MULT}x{vavg:.0f}). Bekleniyor.")
            except Exception:
                pass
            time.sleep(PRW_POLL_SEC)
        if self.stop_flag:
            return self._set(phase="cancel", message="Durduruldu.")
        msg = ("Retest geldi ama onay tutmadı — sinyal yok."
               if touched else "Retest gelmedi (süre doldu) — sinyal yok.")
        self._set(phase="expired", message=msg)

    def _fire(self, cl, vol, vavg):
        arrow = "🟢 LONG" if self.side == "LONG" else "🔴 SHORT"
        self._set(phase="fired",
                  message=f"ŞİMDİ GİR — retest tuttu @ {cl:.6g}. "
                          f"Giriş {self.entry:.6g}, stop {self.stop:.6g}.")
        self._log(f"🎯 ŞİMDİ GİR — {arrow} @ {cl:.6g} (hacim {vol:.0f}/{vavg:.0f})")
        _notify(f"🎯 ŞİMDİ GİR · {self.symbol.replace('USDT','')} {self.side}",
                f"Retest tuttu @ {cl:.6g} · giriş {self.entry:.6g} · "
                f"stop {self.stop:.6g} · TP1 {self.tp1:.6g}")


def arm_plan_retest(symbol, side, level, entry, stop, tp1, tp2):
    symbol = symbol.upper()
    with _PRW_LOCK:
        old = _plan_watchers.get(symbol)
        if old and old.data["phase"] in ("wait_break", "wait_retest"):
            old.stop_flag = True
        w = PlanRetestWatcher(symbol, side, level, entry, stop, tp1, tp2)
        _plan_watchers[symbol] = w
    w._push()
    threading.Thread(target=w.run, daemon=True).start()
    return True


def disarm_plan_retest(symbol):
    symbol = (symbol or "").upper()
    with _PRW_LOCK:
        w = _plan_watchers.get(symbol)
        if w:
            w.stop_flag = True
    with LOCK:
        STATE["plan_watch"].pop(symbol, None)
    return True


# ─── STOCH RSI CANLI SİNYAL (Bybit 1dk) — kullanıcının verdiği coinler ───
def _rsi_series(closes, n=14):
    """Wilder RSI serisi. closes uzunluğu > n olmalı. Her bar için bir RSI."""
    if len(closes) < n + 1:
        return []
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    out = []
    def _rsi(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)
    out.append(_rsi(avg_g, avg_l))
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
        out.append(_rsi(avg_g, avg_l))
    return out  # RSI[0] ilk n+1. bara denk gelir


def _stoch_rsi(closes, rsi_n=14, stoch_n=14, k_smooth=3, d_smooth=3):
    """StochRSI %K ve %D serisi döndürür (0-100). Standart 14/14/3/3."""
    rsi = _rsi_series(closes, rsi_n)
    if len(rsi) < stoch_n + k_smooth + d_smooth:
        return None
    raw = []  # ham stochrsi (0-100)
    for i in range(stoch_n - 1, len(rsi)):
        win = rsi[i - stoch_n + 1:i + 1]
        lo, hi = min(win), max(win)
        raw.append(0.0 if hi == lo else (rsi[i] - lo) / (hi - lo) * 100.0)
    # %K = raw'ın k_smooth SMA'sı
    k = [sum(raw[i - k_smooth + 1:i + 1]) / k_smooth
         for i in range(k_smooth - 1, len(raw))]
    # %D = %K'nin d_smooth SMA'sı
    d = [sum(k[i - d_smooth + 1:i + 1]) / d_smooth
         for i in range(d_smooth - 1, len(k))]
    # k ve d'yi son elemanları hizalı olacak şekilde döndür
    return k, d


def _signal_one(sym):
    """Tek coin için 1dk StochRSI sinyalini hesaplar."""
    kl = _bybit_kl(sym, "1", 200)  # son ~3.3 saat 1dk mum
    if not kl or len(kl) < 60:
        raise ValueError("yetersiz veri")
    o = [float(x[1]) for x in kl]; h = [float(x[2]) for x in kl]
    l = [float(x[3]) for x in kl]; c = [float(x[4]) for x in kl]
    price = c[-1]
    sr = _stoch_rsi(c)
    if sr is None:
        raise ValueError("yetersiz veri")
    k_ser, d_ser = sr
    k, d = k_ser[-1], d_ser[-1]
    pk, pd = (k_ser[-2], d_ser[-2]) if len(k_ser) > 1 and len(d_ser) > 1 else (k, d)
    rsi_ser = _rsi_series(c)
    rsi = rsi_ser[-1] if rsi_ser else 50.0
    # EMA9/EMA21 yön teyidi
    ema9 = _ema(c, 9); ema21 = _ema(c, 21)
    trend_up = ema9 is not None and ema21 is not None and ema9 > ema21

    bull_cross = pk <= pd and k > d       # %K, %D'yi yukarı kesti
    bear_cross = pk >= pd and k < d       # %K, %D'yi aşağı kesti
    zone_buy = k <= 30
    zone_sell = k >= 70

    signal, strength, note = "BEKLE", "", ""
    if zone_sell:
        signal = "SAT"
        if bear_cross and rsi >= 55 and not trend_up:
            strength, note = "GÜÇLÜ", "70 bandı + %K aşağı kesişim + RSI/EMA teyit"
        elif bear_cross:
            strength, note = "GÜÇLÜ", "70 bandı + %K %D'yi aşağı kesti"
        else:
            strength, note = "İZLE", "70 üstü — aşağı kesişimi bekle"
    elif zone_buy:
        signal = "AL"
        if bull_cross and rsi <= 45 and trend_up:
            strength, note = "GÜÇLÜ", "30 bandı + %K yukarı kesişim + RSI/EMA teyit"
        elif bull_cross:
            strength, note = "GÜÇLÜ", "30 bandı + %K %D'yi yukarı kesti"
        else:
            strength, note = "İZLE", "30 altı — yukarı kesişimi bekle"
    else:
        note = "nötr bölge (30–70) — sinyal yok"

    return {
        "symbol": sym, "price": price,
        "k": round(k, 1), "d": round(d, 1), "rsi": round(rsi, 1),
        "cross": "bull" if bull_cross else ("bear" if bear_cross else "none"),
        "trend": "up" if trend_up else "down",
        "signal": signal, "strength": strength, "note": note,
        "ts": int(time.time()),
    }


def signal_snapshot(symbols):
    """Kullanıcının verdiği coin listesi için canlı StochRSI sinyalleri."""
    out = []
    syms = []
    for s in symbols:
        s = (s or "").strip().upper().replace("_USDT", "USDT").replace("-", "")
        if not s:
            continue
        if not s.endswith("USDT"):
            s += "USDT"
        if s not in syms:
            syms.append(s)
    if not syms:
        return {"rows": [], "ts": int(time.time())}
    results = {}
    with ThreadPoolExecutor(max_workers=min(8, len(syms))) as ex:
        futs = {ex.submit(_signal_one, s): s for s in syms}
        for f in futs:
            s = futs[f]
            try:
                results[s] = f.result()
            except Exception as e:
                results[s] = {"symbol": s, "error": str(e)}
    for s in syms:                       # istenen sırayı koru
        out.append(results.get(s, {"symbol": s, "error": "veri yok"}))
    return {"rows": out, "ts": int(time.time())}


# ─── EMA50 + STOCHRSI TARAYICI (Bybit) — kullanıcının verdiği kurulum ───
# EMA:  Length 50, source close, smoothing line EMA(9).
# StochRSI: RSI 14, Stoch 14, %K 3, %D 3.
# LONG  : fiyat > EMA50  ve  StochRSI %K < 30  (yukarı trend + aşırı satım / dip)
# SHORT : fiyat < EMA50  ve  StochRSI %K > 70  (aşağı trend + aşırı alım / tepe)
ES_MIN_TURNOVER = 5_000_000       # 24s hacim >= $5M (likidite)
ES_UNIVERSE     = 200             # hacme göre ilk N coini kline ile tara
ES_TF_ALLOWED   = {"1", "3", "5", "15", "30", "60", "240"}
# REJİM FİLTRESİ: StochRSI k<30/k>70 kuralı YATAY piyasada sürekli tetikler
# (whipsaw). ADX ile trend gücü şartı koşuluyor. Backtest (Bybit 19-22 likit
# perp, ~62 gün): ADX>=22 → 15dk avgR 0.026→0.055, PF 1.04→1.10, maxDD 116→47R;
# 60dk avgR 0.002→0.026, PF 1.00→1.04. Her iki TF'de expectancy+PF+DD iyileşti.
ES_ADX_MIN      = 22
_ES_CACHE = {}                    # tf -> {"ts", "data"}
_ES_LOCK  = threading.Lock()


def _adx_last(hi, lo, cl, n=14):
    """Wilder ADX'in SON değeri (trend gücü, 0-100). Veri yetersizse None.
    hi/lo/cl aynı uzunlukta KAPANMIŞ bar listeleri."""
    N = len(cl)
    if N < 2 * n + 2:
        return None
    tr = [0.0] * N; pdm = [0.0] * N; mdm = [0.0] * N
    for i in range(1, N):
        up = hi[i] - hi[i - 1]; dn = lo[i - 1] - lo[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
    tr_s = sum(tr[1:n + 1]); p_s = sum(pdm[1:n + 1]); m_s = sum(mdm[1:n + 1])
    dxs = []
    for i in range(n + 1, N):
        tr_s = tr_s - tr_s / n + tr[i]
        p_s = p_s - p_s / n + pdm[i]
        m_s = m_s - m_s / n + mdm[i]
        pdi = 100 * p_s / tr_s if tr_s else 0.0
        mdi = 100 * m_s / tr_s if tr_s else 0.0
        den = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / den if den else 0.0)
    if len(dxs) < n:
        return None
    adx = sum(dxs[:n]) / n                 # ilk ADX = DX'in n-ortalaması
    for dx in dxs[n:]:                      # sonra Wilder yumuşatma
        adx = (adx * (n - 1) + dx) / n
    return adx


def _ema_series(xs, n):
    """Tüm seri için EMA değerleri (girişle aynı uzunlukta liste)."""
    if not xs:
        return []
    k = 2.0 / (n + 1)
    e = xs[0]
    out = [e]
    for v in xs[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def _emastoch_detail(item, tf):
    """Tek bir coin öğesini (symbol, turnover24h, funding, pc24) EMA50+StochRSI
    kurulumuna göre değerlendirir. Kurulumu tutmuyorsa None döner.
    Hem tarayıcı (emastoch_board) hem izleme (emastoch_watch) bunu kullanır."""
    s, tov, fr, pc = item
    try:
        k = _bybit_kl(s, tf, 250)
        hi = [float(x[2]) for x in k]
        lo = [float(x[3]) for x in k]
        cl = [float(x[4]) for x in k]
    except Exception:
        return None
    # Son mum HALA OLUŞUYOR (kapanmamış) — değerleri saniyede bir değişir ve
    # grafikte tutmaz. TradingView ile birebir olması için canlı mumu düşüp
    # SON KAPANMIŞ mum üzerinden hesapla.
    if len(cl) >= 2:
        hi, lo, cl = hi[:-1], lo[:-1], cl[:-1]
    n = len(cl)
    if n < 120:                          # EMA50 + StochRSI için yeterli veri
        return None
    price = cl[-1]
    ema_ser = _ema_series(cl, 50)        # EMA50 (close)
    ema50 = ema_ser[-1]
    ema200 = _ema_series(cl, 200)[-1]    # üst-trend proxy (aynı TF, ek API yok)
    smooth = _ema(ema_ser[-40:], 9)      # EMA50'nin EMA9 yumuşatma çizgisi
    sr = _stoch_rsi(cl)                  # 14/14/3/3
    if sr is None:
        return None
    k_ser, d_ser = sr
    kv, dv = k_ser[-1], d_ser[-1]
    pk, pd = (k_ser[-2], d_ser[-2]) if len(k_ser) > 1 and len(d_ser) > 1 else (kv, dv)
    rsi_ser = _rsi_series(cl)
    rsi = rsi_ser[-1] if rsi_ser else 50.0

    side = None
    if price > ema50 and kv < 30:
        side = "LONG"
    elif price < ema50 and kv > 70:
        side = "SHORT"
    if side is None:
        return None

    # REJİM FİLTRESİ (ADX): yatay piyasadaki whipsaw sinyallerini ele.
    # Backtest'te en yüksek etkili tek iyileştirme (yukarıdaki ES_ADX_MIN notu).
    adx = _adx_last(hi, lo, cl, 14)
    if adx is None or adx < ES_ADX_MIN:
        return None

    # ATR (son 14 mum) — plan için
    trs = [hi[i] - lo[i] for i in range(n - 14, n)]
    atr_abs = sum(trs) / 14
    atr_pct = atr_abs / price if price else 0
    dist_ema = (price / ema50 - 1) * 100      # fiyatın EMA50'ye uzaklığı %

    entry = price
    if side == "LONG":
        stop = entry - 1.2 * atr_abs
        sd = entry - stop
        tp1 = entry + 1.5 * sd
        tp2 = entry + 2.5 * sd
        stop_pct = (entry - stop) / entry * 100 if entry else 0
        tp1_pct = (tp1 - entry) / entry * 100 if entry else 0
        tp2_pct = (tp2 - entry) / entry * 100 if entry else 0
        cross = "bull" if (pk <= pd and kv > dv) else "none"
        depth = (30 - kv) / 30                 # ne kadar dip (aşırı satım)
    else:
        stop = entry + 1.2 * atr_abs
        sd = stop - entry
        tp1 = entry - 1.5 * sd
        tp2 = entry - 2.5 * sd
        stop_pct = (stop - entry) / entry * 100 if entry else 0
        tp1_pct = (entry - tp1) / entry * 100 if entry else 0
        tp2_pct = (entry - tp2) / entry * 100 if entry else 0
        cross = "bear" if (pk >= pd and kv < dv) else "none"
        depth = (kv - 70) / 30                 # ne kadar tepe (aşırı alım)

    # TREND UYUMU (EMA200 aynı TF): StochRSI dönüşü SADECE üst-trendle aynı
    # yöndeyken değerli. Backtest (40 likit perp, ~15.6 gün, 15dk) 2x2 bucket:
    #   cross=VAR & uyumlu   → avgR +0.078  PF 1.14  maxDD -10.9  (TEK artı bucket)
    #   cross=VAR & TERS     → avgR -0.322  PF 0.52  (en kötü bucket — bıçak)
    #   cross=YOK (uyumlu)   → avgR -0.047  PF 0.92
    # Ters-trend (uyumsuz) adayları elemek yerine skorda cezalandırıp kademeye
    # düşürüyoruz (izleme sekmesi ters kurulumu da göstermeli). cross'u ZORUNLU
    # filtre YAPMADIK: walk-forward'da cross tek başına dönemler arası kararsız
    # (dilim avgR -0.039 / -0.093 / +0.113) ve zorunlu yapınca schedule kayıp
    # daha geniş/kötü popülasyon giriyor → toplam expectancy düşüyor. Bu yüzden
    # cross bir kalite/kademe sinyali olarak kalır, giriş filtresi değil.
    aligned = (side == "LONG" and price > ema200) or \
              (side == "SHORT" and price < ema200)
    trend = "up" if price > ema200 else "down"   # EMA200'e göre üst-trend yönü

    near = max(0.0, 1 - abs(dist_ema) / 6)     # EMA'dan <%6 uzak = taze giriş
    has_cross = cross != "none"
    cross_b = 1.5 if has_cross else 0.0        # %K/%D dönüş kesişimi teyidi
    adx_b = min(1.0, max(0.0, (adx - ES_ADX_MIN) / 18.0))  # güçlü trendi öne al
    align_b = 1.5 if aligned else 0.0          # trend uyumu (EMA200) primi
    # ters-trend + kesişim = ölçülen en kötü bucket → sert ceza (dibe it)
    counter_pen = 2.0 if (has_cross and not aligned) else (0.8 if not aligned else 0.0)
    score = max(0.0, depth * 4 + near * 2 + cross_b + adx_b + align_b - counter_pen)

    # KADEME (A/B/C) — ölçülen bucket'lara birebir eşlenir:
    #   A = uyumlu + kesişim (tek artı-beklenti bucket)
    #   B = uyumlu, kesişim yok (trend doğru ama teyit zayıf)
    #   C = ters-trend (dikkat — negatif beklenti, özellikle kesişimliyse)
    if aligned and has_cross:
        grade = "A"
    elif aligned:
        grade = "B"
    else:
        grade = "C"
    # 0-100 normalize kalite (UI rozeti). Skor ~0..9 aralığını ölçekle.
    q = int(max(0, min(100, round(score / 9.0 * 100))))

    return {
        "symbol": s, "side": side, "price": round(price, 8),
        "ema50": round(ema50, 8), "ema200": round(ema200, 8),
        "smooth": round(smooth, 8) if smooth is not None else None,
        "dist_ema": round(dist_ema, 2),
        "k": round(kv, 1), "d": round(dv, 1), "rsi": round(rsi, 1),
        "adx": round(adx, 1),
        "cross": cross, "aligned": aligned, "trend": trend,
        "grade": grade, "q": q,
        "entry": round(entry, 8), "stop": round(stop, 8),
        "tp1": round(tp1, 8), "tp2": round(tp2, 8),
        "stop_pct": round(stop_pct, 2), "tp1_pct": round(tp1_pct, 2),
        "tp2_pct": round(tp2_pct, 2), "atr_pct": round(atr_pct * 100, 2),
        "funding": round(fr, 6), "pc24": round(pc, 4),
        "vol24": int(tov), "score": round(score, 2),
        # kar = TP2'ye kadar potansiyel kazanç %'si (sabit 2.5R olduğundan
        # yüksek = daha hareketli fırsat). Sekmede en yüksek kar en üstte.
        "kar": round(tp2_pct, 2),
    }


def emastoch_board(tf="15", max_detail=40):
    """EMA50 + StochRSI(14/14/3/3) kurulumuna uyan Bybit coinlerini tarar.
    LONG: fiyat EMA50 üstünde + %K < 30.  SHORT: fiyat EMA50 altında + %K > 70."""
    tf = tf if tf in ES_TF_ALLOWED else "15"
    with _ES_LOCK:
        c = _ES_CACHE.get(tf)
        if c and c["data"] is not None and time.time() - c["ts"] < 30:
            return c["data"]

    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t["turnover24h"]); fr = float(t.get("fundingRate") or 0)
            pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError):
            continue
        if tov < ES_MIN_TURNOVER:
            continue
        liq.append((s, tov, fr, pc))
    liq.sort(key=lambda x: x[1], reverse=True)

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(lambda it: _emastoch_detail(it, tf), liq[:ES_UNIVERSE]))
    out = [r for r in res if r]
    # Sıralama v2: ÖNCE kalite kademesi (A/B/C — ölçülen bucket beklentisi),
    # sonra skor, en son kar potansiyeli. Böylece backtestte tek artı-beklenti
    # bucket'ı (uyumlu+kesişim = A) en üste çıkar; ters-trend (C) dibe düşer.
    # (Eski sıralama kar'ı öne alıyordu — kar sadece ATR büyüklüğü, edge değil.)
    _grank = {"A": 3, "B": 2, "C": 1}
    out.sort(key=lambda r: (_grank.get(r.get("grade"), 0), r["score"], r["kar"]),
             reverse=True)
    out = out[:max_detail]
    # TF UYGUNLUK notu (backtest, ~15.6/8.7/3.1 gün, 40 likit perp):
    #   1dk avgR -0.24 PF 0.67 · 5dk avgR -0.14 PF 0.79 · 15dk PF ~0.92-0.99.
    # Alt TF'ler net negatif beklenti → kullanıcıyı 15dk+'ya yönlendir.
    if tf in ("1", "3"):
        tf_grade, tf_note = "kotu", ("⚠️ 1dk backtestte NET NEGATİF (avgR -0.24, "
            "PF 0.67) — whipsaw + komisyon. Bu kurulum için ÖNERİLMEZ, 15dk+ kullan.")
    elif tf == "5":
        tf_grade, tf_note = "zayif", ("⚠️ 5dk backtestte negatif beklenti "
            "(PF ~0.79). 15dk daha dengeli — dikkatli.")
    else:
        tf_grade, tf_note = "uygun", ("15dk+ bu kurulum için en dengeli TF "
            "(en düşük drawdown, A-kademe girişlere odaklan).")
    data = {
        "candidates": out, "count": len(out), "tf": tf,
        "tf_grade": tf_grade, "tf_note": tf_note,
        "longs": sum(1 for r in out if r["side"] == "LONG"),
        "shorts": sum(1 for r in out if r["side"] == "SHORT"),
        "grades": {g: sum(1 for r in out if r.get("grade") == g)
                   for g in ("A", "B", "C")},
        "ts": int(time.time()),
    }
    with _ES_LOCK:
        _ES_CACHE[tf] = {"ts": time.time(), "data": data}
    return data


def emastoch_watch(symbols, tf="15"):
    """Kullanıcının İZLEMEYE aldığı coin listesini EMA50+StochRSI kurulumuna
    karşı denetler. Tüm evreni taramaz — sadece verilen coinleri kontrol eder
    ve YALNIZCA kurulumu tutanları döndürür (LONG→AL, SHORT). Kurulumu
    tutmayan izlenen coinler alt kısımda gösterilmez."""
    tf = tf if tf in ES_TF_ALLOWED else "15"
    syms = []
    for s in symbols:
        s = (s or "").strip().upper().replace("_USDT", "USDT").replace("-", "")
        if not s:
            continue
        if not s.endswith("USDT"):
            s += "USDT"
        if s not in syms:
            syms.append(s)
    if not syms:
        return {"candidates": [], "count": 0, "watched": 0, "tf": tf,
                "longs": 0, "shorts": 0, "ts": int(time.time())}

    # ticker verisi (funding / 24s hacim / 24s %) — kart için bilgi amaçlı
    info = {}
    try:
        tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
        for t in tk:
            info[t["symbol"]] = t
    except Exception:
        pass

    items = []
    for s in syms:
        t = info.get(s, {})
        try:
            tov = float(t.get("turnover24h") or 0)
            fr = float(t.get("fundingRate") or 0)
            pc = float(t.get("price24hPcnt") or 0)
        except (ValueError, TypeError):
            tov = fr = pc = 0.0
        items.append((s, tov, fr, pc))

    with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
        res = list(ex.map(lambda it: _emastoch_detail(it, tf), items))
    out = [r for r in res if r]
    out.sort(key=lambda r: r["score"], reverse=True)
    return {
        "candidates": out, "count": len(out), "watched": len(syms), "tf": tf,
        "longs": sum(1 for r in out if r["side"] == "LONG"),
        "shorts": sum(1 for r in out if r["side"] == "SHORT"),
        "ts": int(time.time()),
    }


# ─── DİP / ZİRVE TARAYICI (çoklu indikatör confluence — 5dk odaklı) ──────
# "En dipte al, en tepede sat" — bu trend-takip DEĞİL, aşırı uçlarda DÖNÜŞ
# yakalar (EMA50 sekmesinin tamamlayıcısı). Basit indikatörlerin AYNI ANDA
# aşırı-satım / aşırı-alım göstermesini (confluence) arar; ne kadar çok
# indikatör birlikte uçtaysa o kadar "en dip / en tepe":
#   1) RSI(14)          — aşırı satım ≤34 / aşırı alım ≥66 (derin: ≤25 / ≥75)
#   2) StochRSI %K       — ≤27 / ≥73  +  DÖNÜŞ kesişimi (%K, %D'yi kesti) = momentum döndü
#   3) Bollinger(20,2)   — fiyat alt/üst banda değdi/taştı = istatistiksel uç
#   4) EMA50 esneme      — fiyat EMA50'den ATR bazında çok uzak = aşırı gerilmiş
#   5) Dönüş mumu        — son kapanan mum ters yönde (yeşil dip / kırmızı tepe) = teyit
# Kademe: A = dönüş teyitli (kesişim + istatistiksel uç), B = uç ama teyit zayıf,
# C = sadece temel kapı (teyitsiz — "düşen bıçak" riski, dikkat). Sıralama A üstte.
# NOT: dönüş (mean-reversion) doğası gereği risklidir; temel kapı iki BAĞIMSIZ
# osilatörün (RSI + StochRSI) birlikte uçta olmasını şart koşar ki tek gösterge
# gürültüsüyle tetiklenmesin. Kesin sinyal değil, güçlü confluence adayıdır.
DZ_MIN_TURNOVER = 5_000_000       # 24s hacim >= $5M (likidite)
DZ_UNIVERSE     = 200             # hacme göre ilk N coini tara
DZ_TF_ALLOWED   = {"1", "3", "5", "15", "30", "60"}
_DZ_CACHE = {}                    # tf -> {"ts", "data"}
_DZ_LOCK  = threading.Lock()


def _bollinger(closes, n=20, k=2.0):
    """Bollinger Bantları — SON değerler (mid, up, low). SMA(n) ± k·std."""
    if len(closes) < n:
        return None
    win = closes[-n:]
    mid = sum(win) / n
    var = sum((x - mid) ** 2 for x in win) / n
    sd = var ** 0.5
    return mid, mid + k * sd, mid - k * sd


def _dipzirve_detail(item, tf):
    """Tek coini çoklu-indikatör dip/zirve confluence'ına göre değerlendirir.
    Temel kapıyı (RSI + StochRSI birlikte uçta) tutmuyorsa None döner."""
    s, tov, fr, pc = item
    try:
        kl = _bybit_kl(s, tf, 250)
        op = [float(x[1]) for x in kl]
        hi = [float(x[2]) for x in kl]
        lo = [float(x[3]) for x in kl]
        cl = [float(x[4]) for x in kl]
    except Exception:
        return None
    # canlı (kapanmamış) mumu düş — TradingView ile birebir olsun
    if len(cl) >= 2:
        op, hi, lo, cl = op[:-1], hi[:-1], lo[:-1], cl[:-1]
    bar_ts = int(kl[-2][0]) if len(kl) >= 2 else int(kl[-1][0])   # son KAPANMIŞ mum
    n = len(cl)
    if n < 120:
        return None
    price = cl[-1]
    ema50 = _ema_series(cl, 50)[-1]
    sr = _stoch_rsi(cl)
    if sr is None:
        return None
    k_ser, d_ser = sr
    kv, dv = k_ser[-1], d_ser[-1]
    pk, pd = (k_ser[-2], d_ser[-2]) if len(k_ser) > 1 and len(d_ser) > 1 else (kv, dv)
    rsi_ser = _rsi_series(cl)
    rsi = rsi_ser[-1] if rsi_ser else 50.0
    bb = _bollinger(cl, 20, 2.0)
    if bb is None:
        return None
    bb_mid, bb_up, bb_low = bb
    rng = bb_up - bb_low
    bb_pos = (price - bb_low) / rng * 100 if rng else 50.0   # %B: 0=alt bant, 100=üst
    trs = [max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
           for i in range(n - 14, n)]
    atr_abs = sum(trs) / 14
    atr_pct = atr_abs / price if price else 0
    dist_ema = (price / ema50 - 1) * 100
    stretch = abs(price - ema50) / atr_abs if atr_abs else 0   # EMA50'den kaç ATR uzak
    bull_cross = pk <= pd and kv > dv
    bear_cross = pk >= pd and kv < dv
    last_green = cl[-1] > op[-1]
    last_red = cl[-1] < op[-1]

    # ── Temel kapı: iki BAĞIMSIZ osilatör de aynı uçta olmalı ──
    side = None
    if rsi <= 34 and kv <= 27:
        side = "LONG"      # DİP → al
    elif rsi >= 66 and kv >= 73:
        side = "SHORT"     # ZİRVE → sat
    if side is None:
        return None

    # ── Confluence koşulları (her biri +1, hangileri tuttu = "reasons") ──
    reasons = []
    if side == "LONG":
        c_rsi_deep = rsi <= 25
        c_stoch    = kv <= 20
        c_bb       = price <= bb_low
        c_stretch  = stretch >= 1.8
        c_turn     = bull_cross
        c_candle   = last_green
        depth = max(0.0, (30 - kv) / 30) + max(0.0, (35 - rsi) / 35)
    else:
        c_rsi_deep = rsi >= 75
        c_stoch    = kv >= 80
        c_bb       = price >= bb_up
        c_stretch  = stretch >= 1.8
        c_turn     = bear_cross
        c_candle   = last_red
        depth = max(0.0, (kv - 70) / 30) + max(0.0, (rsi - 65) / 35)

    conds = 0
    if c_rsi_deep: conds += 1; reasons.append("RSI derin uç")
    if c_stoch:    conds += 1; reasons.append("StochRSI uç bölge")
    if c_bb:       conds += 1; reasons.append("Bollinger bandı taştı")
    if c_stretch:  conds += 1; reasons.append(f"EMA50'den {stretch:.1f}×ATR uzak")
    if c_turn:     conds += 1; reasons.append("StochRSI dönüş kesişimi")
    if c_candle:   conds += 1; reasons.append("Dönüş mumu")

    # ── Kademe: dönüş teyidi (kesişim) + en az bir istatistiksel uç = A ──
    extreme = c_bb or c_rsi_deep or c_stretch
    if c_turn and extreme:
        grade = "A"        # momentum döndü + fiyat gerçekten uçta
    elif extreme or c_turn:
        grade = "B"        # uç var ama teyit zayıf (ya da tersi)
    else:
        grade = "C"        # sadece temel kapı — teyitsiz, düşen bıçak riski

    # ── Plan (ATR bazlı; ilk hedef = ortalamaya dönüş / Bollinger orta bant) ──
    entry = price
    if side == "LONG":
        stop = min(min(lo[-3:]) - 0.2 * atr_abs, entry - 1.0 * atr_abs)
        sd = entry - stop
        tp1 = max(bb_mid, entry + 1.2 * sd)
        tp2 = entry + 2.5 * sd
        stop_pct = (entry - stop) / entry * 100 if entry else 0
        tp1_pct = (tp1 - entry) / entry * 100 if entry else 0
        tp2_pct = (tp2 - entry) / entry * 100 if entry else 0
    else:
        stop = max(max(hi[-3:]) + 0.2 * atr_abs, entry + 1.0 * atr_abs)
        sd = stop - entry
        tp1 = min(bb_mid, entry - 1.2 * sd)
        tp2 = entry - 2.5 * sd
        stop_pct = (stop - entry) / entry * 100 if entry else 0
        tp1_pct = (entry - tp1) / entry * 100 if entry else 0
        tp2_pct = (entry - tp2) / entry * 100 if entry else 0

    score = depth * 3 + conds * 1.2 + (1.5 if grade == "A" else 0)
    q = int(max(0, min(100, round(conds / 6 * 60 + depth * 20))))

    return {
        "symbol": s, "side": side, "price": round(price, 8),
        "ema50": round(ema50, 8), "dist_ema": round(dist_ema, 2),
        "k": round(kv, 1), "d": round(dv, 1), "rsi": round(rsi, 1),
        "bb_low": round(bb_low, 8), "bb_mid": round(bb_mid, 8),
        "bb_up": round(bb_up, 8), "bb_pos": round(bb_pos, 1),
        "stretch": round(stretch, 1), "cross": ("bull" if bull_cross else
            ("bear" if bear_cross else "none")),
        "conds": conds, "reasons": reasons, "grade": grade, "q": q,
        "entry": round(entry, 8), "stop": round(stop, 8),
        "tp1": round(tp1, 8), "tp2": round(tp2, 8),
        "stop_pct": round(stop_pct, 2), "tp1_pct": round(tp1_pct, 2),
        "tp2_pct": round(tp2_pct, 2), "atr_pct": round(atr_pct * 100, 2),
        "funding": round(fr, 6), "pc24": round(pc, 4),
        "vol24": int(tov), "score": round(score, 2),
        "kar": round(tp2_pct, 2), "bar_ts": bar_ts,
    }


def dipzirve_board(tf="5", max_detail=40):
    """Tüm likit Bybit evrenini tarar; çoklu indikatör confluence ile aşırı
    uçtaki (en dip / en tepe) dönüş adaylarını döndürür. 5dk odaklı."""
    tf = tf if tf in DZ_TF_ALLOWED else "5"
    with _DZ_LOCK:
        c = _DZ_CACHE.get(tf)
        if c and c["data"] is not None and time.time() - c["ts"] < 30:
            return c["data"]

    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            tov = float(t["turnover24h"]); fr = float(t.get("fundingRate") or 0)
            pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError):
            continue
        if tov < DZ_MIN_TURNOVER:
            continue
        liq.append((s, tov, fr, pc))
    liq.sort(key=lambda x: x[1], reverse=True)

    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(lambda it: _dipzirve_detail(it, tf), liq[:DZ_UNIVERSE]))
    out = [r for r in res if r]
    try:
        _dz_record(tf, out)          # taze tetikleri kalıcı deftere yaz
    except Exception as e:
        print("dipzirve log hata:", e)
    # Önce kademe (A dönüş teyidi üstte), sonra confluence skoru, sonra derinlik.
    _grank = {"A": 3, "B": 2, "C": 1}
    out.sort(key=lambda r: (_grank.get(r.get("grade"), 0), r["score"], r["q"]),
             reverse=True)
    out = out[:max_detail]
    data = {
        "candidates": out, "count": len(out), "tf": tf,
        "longs": sum(1 for r in out if r["side"] == "LONG"),
        "shorts": sum(1 for r in out if r["side"] == "SHORT"),
        "grades": {g: sum(1 for r in out if r.get("grade") == g)
                   for g in ("A", "B", "C")},
        "ts": int(time.time()),
    }
    with _DZ_LOCK:
        _DZ_CACHE[tf] = {"ts": time.time(), "data": data}
    return data


# ─── DİP / ZİRVE SİNYAL DEFTERİ (kalıcı kayıt + geçmiş sekmesi) ───────────
# Her tarama sonrası "taze tetik" (rising edge: bu mumda kapı açık, bir önceki
# mumda AYNI yön kapalı) sinyaller dipzirve_signals.jsonl'e eklenir — backtest
# ile aynı tanım. 5dk için arka planda 60 sn'de bir tarama yapılır (sekme
# açık olmasa da), diğer TF'ler sadece sekme açıkken taranır/kaydedilir.
DZ_LOG_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "dipzirve_signals.jsonl")
DZ_BG_TF     = "5"                # arka planda sürekli kaydedilen TF
DZ_BG_PERIOD = 60                 # sn
_DZ_LAST_BAR = {}                 # (tf, sym, side) -> kapı açık görülen son bar_ts
_DZ_LOG_LOCK = threading.Lock()
_DZ_PX_CACHE = {"ts": 0, "px": {}}


def _dz_record(tf, cands):
    """Taramadaki adaylardan yalnızca TAZE tetikleri deftere yazar."""
    tf_ms = int(tf) * 60_000
    now = int(time.time())
    new_rows = []
    with _DZ_LOG_LOCK:
        for r in cands:
            key = (tf, r["symbol"], r["side"])
            bts = r.get("bar_ts") or 0
            last = _DZ_LAST_BAR.get(key)
            # aynı mum tekrar görüldü -> atla; bir önceki mumda da açıktı -> süregelen, atla
            fresh = last is None or (bts - last) > tf_ms
            _DZ_LAST_BAR[key] = max(bts, last or 0)
            if not fresh or bts == last:
                continue
            row = dict(r)
            row.update({"ts": now, "tf": tf, "src": "live",
                        "id": f"{tf}_{r['symbol']}_{r['side']}_{bts}"})
            new_rows.append(row)
        if new_rows:
            with open(DZ_LOG_FILE, "a", encoding="utf-8") as f:
                for row in new_rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(new_rows)


def _dz_read_log():
    rows = []
    if not os.path.exists(DZ_LOG_FILE):
        return rows
    with _DZ_LOG_LOCK:
        with open(DZ_LOG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def _dz_prices():
    """Bybit anlık fiyatlar (30 sn cache) — sinyalden bu yana getiri için."""
    if time.time() - _DZ_PX_CACHE["ts"] < 30 and _DZ_PX_CACHE["px"]:
        return _DZ_PX_CACHE["px"]
    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    px = {}
    for t in tk:
        try:
            px[t["symbol"]] = float(t["lastPrice"])
        except (KeyError, ValueError, TypeError):
            pass
    _DZ_PX_CACHE.update({"ts": time.time(), "px": px})
    return px


def dipzirve_log(tf="all", hours=24, limit=2000):
    """Kaydedilmiş dip/zirve sinyalleri (en yeni üstte) + anlık sonuç."""
    rows = _dz_read_log()
    cutoff = time.time() - float(hours) * 3600
    sel = [r for r in rows if r.get("ts", 0) >= cutoff
           and (tf == "all" or r.get("tf") == tf)]
    # aynı id iki kez yazılmışsa (restart) tekilleştir
    seen, uniq = set(), []
    for r in sel:
        if r.get("id") in seen:
            continue
        seen.add(r.get("id")); uniq.append(r)
    uniq.sort(key=lambda r: r.get("ts", 0), reverse=True)
    uniq = uniq[:limit]
    try:
        px = _dz_prices()
    except Exception:
        px = {}
    wins = 0
    for r in uniq:
        p = px.get(r["symbol"])
        if p and r.get("entry"):
            chg = (p / r["entry"] - 1) * 100
            r["now_price"] = p
            r["now_pct"] = round(chg if r["side"] == "LONG" else -chg, 2)
            if r["now_pct"] > 0:
                wins += 1
        else:
            r["now_price"] = None; r["now_pct"] = None
    return {
        "signals": uniq, "count": len(uniq), "tf": tf, "hours": hours,
        "longs": sum(1 for r in uniq if r["side"] == "LONG"),
        "shorts": sum(1 for r in uniq if r["side"] == "SHORT"),
        "grades": {g: sum(1 for r in uniq if r.get("grade") == g)
                   for g in ("A", "B", "C")},
        "wins": wins, "total_logged": len(rows), "ts": int(time.time()),
    }


def _dz_recorder_loop():
    """Arka plan: 5dk taramasını sürekli çalıştırır ki sekme kapalıyken de
    sinyaller deftere düşsün."""
    time.sleep(5)
    while True:
        try:
            dipzirve_board(DZ_BG_TF)
        except Exception as e:
            print("dipzirve arka plan tarama hatası:", e)
        time.sleep(DZ_BG_PERIOD)


# ─── EMA50+StochRSI PAPER BOT İZLEME ─────────────────────────────
# emastoch_bot.py çalışırken işlemlerini <base>/emastoch_bot_<tf>m_trades.json
# dosyasına yazar. Bu fonksiyon o dosyayı okuyup panele canlı özet döndürür.
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))


def _bot_running(tf):
    """emastoch_bot.py <tf> ... süreci canlı mı? (pgrep)"""
    try:
        import subprocess
        out = subprocess.run(["pgrep", "-fl", "emastoch_bot.py"],
                             capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            parts = line.split("emastoch_bot.py", 1)
            if len(parts) == 2 and parts[1].split()[:1] == [str(tf)]:
                return True
        return False
    except Exception:
        return False


def bot_trades_snapshot(tf="5", limit=30, before=None, since=None, watch=None):
    """Sinyal botunun (emastoch_bot.py v4) kayıtlarını + özetini döndürür (canlı panel için).
    Bot kendi giriş yapmaz; kurulumu tutan her coin için giriş/stop/TP sinyali kaydeder.

    Sayfalama / artımlı güncelleme (panel tüm listeyi yeniden çizmesin diye):
      limit  : bir sayfada en fazla kaç sinyal (en yeni üstte)
      before : sadece id < before olan sinyaller (geriye doğru sayfa: "daha eski yükle")
      since  : sadece id > since olan sinyaller ("new") — poll'da yalnızca yeni geleni ekle
      watch  : virgüllü id listesi → o sinyallerin güncel hali ("watch"); ekranda açık
               görünen kartların TP/stop durumunu yerinde tazelemek için."""
    tf = str(tf)
    try:
        limit = max(1, min(int(limit or 30), 200))
    except Exception:
        limit = 30
    path = os.path.join(_BOT_DIR, f"emastoch_bot_{tf}m_trades.json")
    if not os.path.exists(path):
        return {"exists": False, "running": _bot_running(tf), "tf": tf,
                "trades": [], "ts": int(time.time())}
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception as e:
        return {"exists": False, "running": _bot_running(tf), "tf": tf,
                "trades": [], "error": str(e), "ts": int(time.time())}
    sigs = d.get("trades", [])
    done = [s for s in sigs if s.get("closed")]
    openp = [s for s in sigs if not s.get("closed")]
    tp1 = [s for s in sigs if s.get("tp1_hit")]
    tp2 = [s for s in sigs if s.get("tp2_hit")]
    stp = [s for s in done if s.get("result") == "stop"]
    avg_end = round(sum(s.get("end_r") or 0 for s in done) / len(done), 2) if done else 0.0
    avg_max = round(sum(s.get("max_r") or 0 for s in done) / len(done), 2) if done else 0.0
    out = {
        "exists": True, "running": _bot_running(tf), "tf": tf,
        "mode": d.get("mode", "paper"),
        "count": len(sigs), "open": len(openp), "closed": len(done),
        "tp1": len(tp1), "tp2": len(tp2), "stops": len(stp),
        "tp1_pct": round(100 * len(tp1) / len(sigs)) if sigs else 0,
        "tp2_pct": round(100 * len(tp2) / len(sigs)) if sigs else 0,
        "stop_pct": round(100 * len(stp) / len(done)) if done else 0,
        "avg_end_r": avg_end, "avg_max_r": avg_max,
        "updated": d.get("updated"),
        "total": len(sigs),
        "ts": int(time.time()),
    }
    def _id(s):
        try:
            return int(s.get("id"))
        except Exception:
            return -1
    newest_first = sorted(sigs, key=_id, reverse=True)
    if since is not None:
        # artımlı poll: sadece yeni gelenler (+ izlenen kartların güncel hali)
        try:
            since_i = int(since)
        except Exception:
            since_i = -1
        out["new"] = [s for s in newest_first if _id(s) > since_i][:limit]
        wids = set()
        for w in str(watch or "").split(","):
            w = w.strip()
            if w.isdigit():
                wids.add(int(w))
        out["watch"] = [s for s in sigs if _id(s) in wids] if wids else []
        out["trades"] = []
        return out
    # sayfa: before verilmişse id < before olanlardan, yoksa en yeniden itibaren
    page_src = newest_first
    if before is not None:
        try:
            before_i = int(before)
            page_src = [s for s in newest_first if _id(s) < before_i]
        except Exception:
            pass
    page = page_src[:limit]
    out["trades"] = page
    out["has_more"] = len(page_src) > len(page)
    out["min_id"] = _id(page[-1]) if page else None
    out["max_id"] = _id(page[0]) if page else None
    return out



# ─── 🧠 Coin Analist (claude -p --agent coin-analist: haber + teknik + trend + plan) ───
_ANALIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "coin_analist")
os.makedirs(_ANALIST_DIR, exist_ok=True)
_ANALIST_DEFTER = os.path.join(_ANALIST_DIR, "cevaplar.jsonl")
_ANALIST_JOBS = {}                      # id -> job dict
_ANALIST_LOCK = threading.Lock()
_ANALIST_SEM = threading.Semaphore(2)   # aynı anda en fazla 2 claude süreci
_ANALIST_RE = re.compile(r"PLAN_JSON:\s*(\{.*\})", re.S)


def _analist_plan_ayikla(text):
    m = _ANALIST_RE.search(text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip().splitlines()[0])
    except Exception:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None


def _analist_calistir(job):
    with _ANALIST_SEM:
        job["durum"] = "calisiyor"; job["baslangic"] = time.time()
        soru = f"{job['sym']} coinine bak: güncel haberleri araştır, teknik analiz yap, trendi tespit et ve işlem planı ver."
        if job.get("not"):
            soru += f"\nKullanıcı notu: {job['not']}"
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        cmd = ["claude", "-p", soru, "--agent", "coin-analist", "--output-format", "json",
               "--allowedTools", "Bash(.venv/bin/python coin_analiz.py:*)", "WebSearch", "WebFetch", "Read"]
        try:
            r = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)), env=env,
                               capture_output=True, text=True, timeout=600)
            out = r.stdout.strip()
            try:
                j = json.loads(out)
                cevap = j.get("result") or out
                job["maliyet_usd"] = j.get("total_cost_usd")
            except Exception:
                cevap = out or r.stderr.strip()
            if r.returncode != 0 and not cevap:
                raise RuntimeError(r.stderr.strip()[:500] or f"claude çıkış kodu {r.returncode}")
            job["cevap"] = cevap
            job["plan"] = _analist_plan_ayikla(cevap)
            job["durum"] = "bitti"
        except subprocess.TimeoutExpired:
            job["durum"] = "hata"; job["hata"] = "10 dk içinde cevap gelmedi"
        except Exception as e:
            job["durum"] = "hata"; job["hata"] = str(e)[:500]
        job["bitis"] = time.time()
        try:
            with open(_ANALIST_DEFTER, "a", encoding="utf-8") as f:
                f.write(json.dumps(job, ensure_ascii=False) + "\n")
        except Exception:
            pass


def analist_sor(sym, not_=""):
    sym = (sym or "").upper().replace("/", "").replace("-", "").replace("_", "").strip()
    if sym.endswith("USDT"):
        sym = sym[:-4]
    if not re.fullmatch(r"[A-Z0-9]{2,15}", sym):
        return {"ok": False, "msg": "geçersiz coin adı"}
    with _ANALIST_LOCK:
        for j in _ANALIST_JOBS.values():
            if j["sym"] == sym and j["durum"] in ("bekliyor", "calisiyor"):
                return {"ok": True, "id": j["id"], "msg": "zaten soruluyor"}
        jid = f"{int(time.time()*1000)}_{sym}"
        job = {"id": jid, "sym": sym, "not": (not_ or "")[:300], "durum": "bekliyor", "olusturma": time.time(),
               "baslangic": None, "bitis": None, "cevap": None, "plan": None, "hata": None}
        _ANALIST_JOBS[jid] = job
    threading.Thread(target=_analist_calistir, args=(job,), daemon=True).start()
    return {"ok": True, "id": jid}


def analist_job(jid):
    j = _ANALIST_JOBS.get(jid)
    if not j:
        # bellekte yoksa defterden ara
        for g in analist_gecmis(500):
            if g["id"] == jid:
                return g
        return None
    return j


def analist_gecmis(limit=50):
    out = []
    try:
        with open(_ANALIST_DEFTER, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    with _ANALIST_LOCK:
        aktif = [j for j in _ANALIST_JOBS.values() if j["durum"] in ("bekliyor", "calisiyor")]
    out = aktif + sorted(out, key=lambda x: -(x.get("olusturma") or 0))
    return out[:limit]


# ─── HTTP ───────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # sessiz

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # tarayici eski index.html'i onbellekten sunmasin — degisiklikler aninda gorunsun
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            try:
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "index.html yok")
            return
        if path == "/grafik" or path == "/grafik.html":
            try:
                with open(os.path.join(WEB_DIR, "grafik.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "grafik.html yok")
            return
        if path == "/api/kline":
            # Grafik penceresi: MEXC perp mumlari. ?symbol=BTC_USDT&interval=Min15&limit=1000
            q = parse_qs(urlparse(self.path).query)
            sym = (q.get("symbol", ["BTC_USDT"])[0] or "BTC_USDT").upper()
            itv = q.get("interval", ["Min15"])[0]
            try:
                lim = max(2, min(2000, int(q.get("limit", ["1000"])[0])))   # canlı tazeleme 3 mum ister
            except ValueError:
                lim = 1000
            secs = {"Min1": 60, "Min5": 300, "Min15": 900, "Min30": 1800, "Min60": 3600,
                    "Hour4": 14400, "Hour8": 28800, "Day1": 86400, "Week1": 604800}
            if itv not in secs:
                self._json({"error": "gecersiz interval"}, 400); return
            end_q = q.get("end", [None])[0]
            try:
                end = int(end_q) if end_q else int(time.time())
            except ValueError:
                end = int(time.time())
            start = end - lim * secs[itv]
            d = mexc.get(f"/api/v1/contract/kline/{sym}", interval=itv, start=start, end=end)
            if not d or not d.get("time"):
                self._json({"symbol": sym, "interval": itv, "t": [], "o": [], "h": [], "l": [], "c": [], "v": []})
                return
            self._json({"symbol": sym, "interval": itv, "t": d["time"], "o": d["open"], "h": d["high"],
                        "l": d["low"], "c": d["close"], "v": d.get("vol", []), "a": d.get("amount", [])})
            return
        if path == "/api/symbols":
            # Grafik penceresi: tum USDT perp'ler, fiyat/24s degisim/hacim/funding
            data = mexc.get("/api/v1/contract/ticker") or []
            rows = []
            for r in data:
                sym = r.get("symbol", "")
                if not sym.endswith("_USDT"):
                    continue
                try:
                    rows.append({"s": sym, "p": float(r["lastPrice"]), "chg": float(r.get("riseFallRate", 0)),
                                 "vol": float(r.get("amount24", 0)), "fr": float(r.get("fundingRate", 0)),
                                 "hi": float(r.get("high24Price", 0)), "lo": float(r.get("lower24Price", 0))})
                except (KeyError, TypeError, ValueError):
                    continue
            rows.sort(key=lambda x: -x["vol"])
            self._json({"ts": int(time.time()), "rows": rows})
            return
        if path == "/analist_ui.js":
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "analist_ui.js"), "rb") as f:
                return self._send(200, f.read(), "application/javascript; charset=utf-8")
        if path == "/api/stoch":
            return self._json(stoch_izle.durum())
        if path == "/api/analist/tarama":
            return self._json(analist_tarama.durum())
        if path == "/api/analist/job":
            q = parse_qs(urlparse(self.path).query)
            j = analist_job(q.get("id", [""])[0])
            return self._json(j or {"ok": False, "msg": "iş yok"}, 200 if j else 404)
        if path == "/api/analist/gecmis":
            q = parse_qs(urlparse(self.path).query)
            lim = int(q.get("limit", ["50"])[0])
            return self._json({"ok": True, "liste": [{k: v for k, v in g.items() if k != "cevap"} | {"ozet": (g.get("cevap") or "")[:160]} for g in analist_gecmis(lim)]})
        if path == "/api/analist/cevap":
            q = parse_qs(urlparse(self.path).query)
            j = analist_job(q.get("id", [""])[0])
            return self._json(j or {"ok": False}, 200 if j else 404)
        if path == "/api/cokus":
            try:
                self._json(cokus_board.board())
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        if path == "/api/schedule":
            det = mexc.funding_detail("BTC_USDT") or {}
            self._json({
                "now_tr": dt.datetime.now(TR).strftime("%Y-%m-%d %H:%M:%S"),
                "next_settle_ms": det.get("nextSettleTime", 0),
                "next_settle_tr": _fmt_ms(det.get("nextSettleTime", 0)),
            })
            return
        if path == "/api/status":
            with LOCK:
                self._json(STATE)
            return
        if path == "/api/funding":
            try:
                self._json(funding_board())
            except Exception as e:
                self._json({"positive": [], "negative": [], "error": str(e)}, 500)
            return
        if path == "/api/btc_regime":
            self._json(btc_regime_5m())
            return
        if path == "/api/squeeze":
            try:
                d = dict(squeeze_board()); d["btc"] = btc_regime_5m()
                self._json(d)
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/pump":
            try:
                self._json(pump_board())
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/retest":
            try:
                self._json(retest_board())
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/trend":
            try:
                self._json(trend_board())
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/protrend":
            try:
                self._json(pro_trend_board())
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/scalp":
            try:
                self._json(scalp_board())
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/signal":
            qs = parse_qs(urlparse(self.path).query)
            raw = qs.get("symbols", [""])[0]
            syms = [x for x in raw.split(",") if x.strip()]
            try:
                self._json(signal_snapshot(syms))
            except Exception as e:
                self._json({"rows": [], "error": str(e)}, 500)
            return
        if path == "/api/emastoch":
            qs = parse_qs(urlparse(self.path).query)
            tf = qs.get("tf", ["15"])[0]
            try:
                self._json(emastoch_board(tf))
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/dipzirve":
            qs = parse_qs(urlparse(self.path).query)
            tf = qs.get("tf", ["5"])[0]
            try:
                self._json(dipzirve_board(tf))
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/dipzirve_log":
            qs = parse_qs(urlparse(self.path).query)
            tf = qs.get("tf", ["all"])[0]
            try:
                hours = float(qs.get("hours", ["24"])[0])
            except ValueError:
                hours = 24
            try:
                self._json(dipzirve_log(tf, hours))
            except Exception as e:
                self._json({"signals": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/sr":
            qs = parse_qs(urlparse(self.path).query)
            tf = qs.get("tf", ["5"])[0]
            try:
                self._json(sr_board.sr_board(tf))
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/bottrades":
            qs = parse_qs(urlparse(self.path).query)
            tf = qs.get("tf", ["5"])[0]
            g = lambda k: qs.get(k, [None])[0]
            try:
                d = dict(bot_trades_snapshot(tf, limit=g("limit") or 30, before=g("before"),
                                             since=g("since"), watch=g("watch")))
                d["btc"] = btc_regime_5m()     # BTC 5dk rejimi (yukari/asagi/yatay)
                self._json(d)
            except Exception as e:
                self._json({"exists": False, "trades": [], "error": str(e)}, 500)
            return
        if path == "/api/signal_watch":
            # İzlenen coinleri EMA50+StochRSI kurulumuna karşı denetle;
            # yalnızca kurulumu tutanları döndür.
            qs = parse_qs(urlparse(self.path).query)
            raw = qs.get("symbols", [""])[0]
            tf = qs.get("tf", ["15"])[0]
            syms = [x for x in raw.split(",") if x.strip()]
            try:
                self._json(emastoch_watch(syms, tf))
            except Exception as e:
                self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
            return
        if path == "/api/plan/history":
            try:
                self._json({"runs": _plan_history()})
            except Exception as e:
                self._json({"runs": [], "error": str(e)}, 500)
            return
        if path == "/api/plan/armed":
            with LOCK:
                self._json({"watchers": list(STATE["plan_watch"].values())})
            return
        if path == "/api/plan/run":
            qs = parse_qs(urlparse(self.path).query)
            rid = qs.get("id", [None])[0]
            d = _plan_load(rid)
            if d is None:
                return self._json({"error": "kayıt yok"}, 404)
            return self._json(d)
        if path == "/api/splan/history":
            try:
                self._json({"runs": _plan_history(SPLAN_DIR)})
            except Exception as e:
                self._json({"runs": [], "error": str(e)}, 500)
            return
        if path == "/api/splan/run":
            qs = parse_qs(urlparse(self.path).query)
            rid = qs.get("id", [None])[0]
            d = _plan_load(rid, SPLAN_DIR)
            if d is None:
                return self._json({"error": "kayıt yok"}, 404)
            return self._json(d)
        self._send(404, "yok")

    def do_POST(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {}

        if path == "/api/stoch/yenile":
            if self.headers.get("X-Analist") != "1":
                return self._json({"ok": False, "msg": "başlık yok"}, 403)
            return self._json(stoch_izle.yenile())
        if path in ("/api/stoch/ekle", "/api/stoch/sil"):
            if self.headers.get("X-Analist") != "1":
                return self._json({"ok": False, "msg": "başlık yok"}, 403)
            fn = stoch_izle.ekle if path.endswith("ekle") else stoch_izle.sil
            return self._json(fn(body.get("sym", "")))
        if path == "/api/analist/tarama/baslat":
            if self.headers.get("X-Analist") != "1":
                return self._json({"ok": False, "msg": "başlık yok"}, 403)
            try:
                n = max(0, min(100, int(body.get("n", 10))))  # 0 = skor eşiğini geçen HEPSİ
                mv = float(body.get("min_vol", 5_000_000))
            except (TypeError, ValueError):
                return self._json({"ok": False, "msg": "geçersiz ayar"}, 400)
            return self._json(analist_tarama.baslat(n=n, min_vol=mv))
        if path == "/api/analist/tarama/durdur":
            if self.headers.get("X-Analist") != "1":
                return self._json({"ok": False, "msg": "başlık yok"}, 403)
            return self._json(analist_tarama.durdur())
        if path == "/api/analist/sor":
            if self.headers.get("X-Analist") != "1":
                return self._json({"ok": False, "msg": "başlık yok"}, 403)
            return self._json(analist_sor(body.get("sym", ""), body.get("not", "")))
        if path == "/api/scan":
            if STATE["scan"]["status"] == "running":
                return self._json({"ok": True, "msg": "zaten çalışıyor"})
            threading.Thread(target=_run_scan, daemon=True).start()
            return self._json({"ok": True})
        if path == "/api/plan/scan":
            try:
                data = _plan_save(plan_scan())
                return self._json(data)
            except Exception as e:
                return self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
        if path == "/api/plan/delete":
            rid = body.get("id") or (qs.get("id", [None])[0])
            ok = _plan_delete(rid)
            return self._json({"ok": ok})
        if path == "/api/splan/scan":
            try:
                data = _plan_save(short_plan_scan(), SPLAN_DIR)
                return self._json(data)
            except Exception as e:
                return self._json({"candidates": [], "count": 0, "error": str(e)}, 500)
        if path == "/api/splan/delete":
            rid = body.get("id") or (qs.get("id", [None])[0])
            ok = _plan_delete(rid, SPLAN_DIR)
            return self._json({"ok": ok})
        if path == "/api/plan/arm":
            sym = body.get("symbol")
            if not sym:
                return self._json({"ok": False, "msg": "symbol yok"}, 400)
            try:
                arm_plan_retest(
                    sym, body.get("side", "LONG"),
                    float(body["level"]), body.get("entry"), body.get("stop"),
                    body.get("tp1"), body.get("tp2"))
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "msg": str(e)}, 400)
        if path == "/api/plan/disarm":
            sym = body.get("symbol") or (qs.get("symbol", [None])[0])
            disarm_plan_retest(sym)
            return self._json({"ok": True})
        if path == "/api/watch":
            sym = body.get("symbol") or (qs.get("symbol", [None])[0])
            if not sym:
                return self._json({"ok": False, "msg": "symbol yok"}, 400)
            start_watch(sym)
            return self._json({"ok": True})
        if path == "/api/watch/confirm":
            confirm_entry()
            return self._json({"ok": True})
        if path == "/api/watch/stop":
            stop_watch()
            return self._json({"ok": True})
        self._send(404, "yok")


def main():
    analist_tarama.init(analist_sor, analist_job, _notify)
    stoch_izle.init(_notify)
    threading.Thread(target=stoch_izle.loop, daemon=True).start()   # 📶 1dk StochRSI izleme
    threading.Thread(target=_dz_recorder_loop, daemon=True).start()   # dip/zirve defteri
    threading.Thread(target=cokus_board.loop, daemon=True).start()   # ⚡ çöküş sıçraması kâğıt motoru
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\n  Funding Trader web arayüzü çalışıyor:  {url}")
    print("  Durdurmak için: Ctrl+C\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Sunucu durduruldu.")


if __name__ == "__main__":
    main()
