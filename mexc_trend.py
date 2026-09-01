#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEXC-native Pro Trend sinyal panosu.

server.py'deki pro_trend_board() ile AYNI mantık ve AYNI eşikler (PT_*),
ama tüm veri MEXC contract API'sinden gelir (Bybit değil). Böylece sinyal,
emrin gönderileceği borsayla birebir aynı fiyat/sembol evreninden üretilir.

Çıktı şeması pro_trend_board() ile birebir aynı → protrend_live.py doğrudan
tüketebilir. 25 sn cache.
"""
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import mexc
# Gösterge matematiği ve eşikleri server.py'den aynen kullan (import güvenli:
# protrend_bot.py de server'ı import ediyor; ağır iş __main__ altında).
from server import (
    _ema, _adx, _resample,
    PT_MIN_TURNOVER, PT_UNIVERSE, PT_MIN_ADX, PT_MIN_ADX_5M,
    PT_MAX_EXT_1M, PT_MAX_CONSEC, PT_MIN_ATR, PT_MIN_RR, PT_MIN_CONF,
)

_CACHE = {"ts": 0, "data": None}
_LOCK = threading.Lock()
SCAN_WORKERS = 8   # MEXC public rate-limit'e saygılı


def _universe():
    """Likidite filtresinden geçen (symbol, turnover, funding, pc24) listesi,
    hacme göre azalan."""
    snap = mexc.snapshot()
    liq = []
    for r in snap:
        s = r["symbol"]
        tov = r["amount24"]
        if tov < PT_MIN_TURNOVER:
            continue
        liq.append((s, tov, r["fundingRate"], r.get("riseFallRate", 0.0)))
    liq.sort(key=lambda x: x[1], reverse=True)
    return liq


def _detail(item):
    s, tov, fr, pc = item
    try:
        k = mexc.klines(s, minutes=300, interval="Min1")   # ~son 5s, 1dk mum
        if not k:
            return None
        o1 = [x["o"] for x in k]; h1 = [x["h"] for x in k]
        l1 = [x["l"] for x in k]; c1 = [x["c"] for x in k]
        v1 = [x["vol"] for x in k]
    except Exception:
        return None
    n = len(c1)
    if n < 120:
        return None
    price = c1[-1]

    # ── 1dk (giriş zaman dilimi) ──
    e9  = _ema(c1[-60:], 9)
    e21 = _ema(c1[-80:], 21)
    e50 = _ema(c1[-150:], 50)
    e9_prev = _ema(c1[-63:-3], 9)
    if None in (e9, e21, e50, e9_prev):
        return None
    a1 = _adx(h1[-120:], l1[-120:], c1[-120:], 14)
    if a1 is None:
        return None
    adx1, pdi1, mdi1 = a1
    atr_abs = sum(h1[i] - l1[i] for i in range(n - 14, n)) / 14
    atr_pct = atr_abs / price if price else 0
    m5  = price / c1[-6]  - 1 if n >= 6  else 0
    m15 = price / c1[-16] - 1 if n >= 16 else 0
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
    rv = sum(v1[-3:]) / 3 if n >= 3 else 0
    seg = v1[-60:-3] if n >= 63 else v1[:-3] or v1
    bv = sorted(seg)[len(seg) // 2] if seg else (rv or 1)
    vspike = rv / bv if bv > 0 else 0

    # ── 5dk (üst zaman dilimi = TREND YÖNÜ) ──
    O5, H5, L5, C5, V5 = _resample(o1, h1, l1, c1, v1, 5)
    if len(C5) < 55:
        return None
    E9_5  = _ema(C5[-40:], 9)
    E21_5 = _ema(C5[-45:], 21)
    E50_5 = _ema(C5[-55:], 50)
    E9_5p = _ema(C5[-43:-3], 9)
    if None in (E9_5, E21_5, E50_5, E9_5p):
        return None
    a5 = _adx(H5[-60:], L5[-60:], C5[-60:], 14)
    if a5 is None:
        return None
    adx5, pdi5, mdi5 = a5

    # ── yön kararı ──
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

    # ── devam güveni (0-100) ──
    adx_sc  = max(0, min((adx1 - 20) / 25, 1)) * 25
    htf_sc  = max(0, min((adx5 - 18) / 22, 1)) * 20
    di_gap  = abs(pdi1 - mdi1) / (pdi1 + mdi1) if (pdi1 + mdi1) else 0
    dir_sc  = min(di_gap * 1.4, 1) * 15
    fresh   = 1 - min(ext / PT_MAX_EXT_1M, 1)
    fresh_sc = fresh * 22
    vol_sc  = max(0, min((vspike - 0.8) / 1.7, 1)) * 10
    room_sc = max(0, min((PT_MAX_CONSEC - consec) / PT_MAX_CONSEC, 1)) * 8
    conf = round(adx_sc + htf_sc + dir_sc + fresh_sc + vol_sc + room_sc, 1)
    if conf < PT_MIN_CONF:
        return None

    # ── plan ──
    entry = price
    drift = 0.55 * atr_abs
    exp_candles = 4 if conf < 72 else 5
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


def board(max_detail=20):
    """pro_trend_board() ile aynı şema; kaynak MEXC. 25 sn cache."""
    with _LOCK:
        if _CACHE["data"] is not None and time.time() - _CACHE["ts"] < 25:
            return _CACHE["data"]
    liq = _universe()
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        res = list(ex.map(_detail, liq[:PT_UNIVERSE]))
    out = [r for r in res if r]
    out.sort(key=lambda r: r["conf"], reverse=True)
    out = out[:max_detail]
    data = {
        "candidates": out, "count": len(out),
        "longs": sum(1 for r in out if r["side"] == "LONG"),
        "shorts": sum(1 for r in out if r["side"] == "SHORT"),
        "ts": int(time.time()),
    }
    with _LOCK:
        _CACHE["ts"] = time.time(); _CACHE["data"] = data
    return data


if __name__ == "__main__":
    import json
    t0 = time.time()
    b = board()
    print(f"MEXC pro-trend: {b['count']} aday ({b['longs']}L/{b['shorts']}S) "
          f"— {time.time()-t0:.1f}s")
    for c in b["candidates"][:8]:
        print(f"  {c['side']:5} {c['symbol']:14} conf {c['conf']:5} "
              f"rr {c['rr']} adx1 {c['adx1']} px {c['price']:g}")
