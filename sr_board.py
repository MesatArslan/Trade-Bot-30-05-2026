#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DESTEK / DİRENÇ CANLI TARAYICI — Bybit USDT-perp evreninde, 5dk barlarda:
   • her coin için o an bilinen destek/direnç seviyeleri (5dk swing, 1s swing, önceki gün/hafta H-L, günlük pivot)
   • son KAPANMIŞ mumda oluşan sinyaller: bounce (dönüş), breakout (kırılım), retest (kır-geri-test)
   • seviyeye "yaklaşmış" coinler (≤0.5 ATR) — sinyal yok ama izlemeye değer
Motor: sr_engine.py (backtest ile birebir aynı seviye/sinyal tanımı → canlı = test edilen şey).
Kullanım: server.py /api/sr ucu ve `python3 sr_tarayici.py` bunu çağırır."""
import json, time, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor
from sr_engine import LevelProvider, gen_bounce, gen_breakout, gen_retest, MS5

_BYBIT = "https://api.bybit.com"
SR_MIN_TURNOVER = 5_000_000
SR_UNIVERSE = 150
SR_TF_ALLOWED = {"5", "15"}
# Backtest'te kullanılan tanımlar (sr_findings raporuna bak) — değiştirirsen backtest sonucu geçerliliğini yitirir
SR_LEVELS = dict(sources=("swing5", "swing1h", "pdhl", "pivot"), w5=5, lb5=288, w1h=3, lb1h_days=7,
                 tol_atr=0.5, tol_min=0.0015, min_touch=1, refresh_every=1)
SR_BOUNCE = dict(zone_atr=0.5, max_pierce_atr=1.0, need_reject=True, fresh_bars=6, min_touch=1)
SR_BREAK = dict(buf_atr=0.25, prior_bars=24, vol_mult=None, min_touch=1, cooldown=12)
SR_RETEST = dict(buf_atr=0.25, prior_bars=24, retest_within=36, zone_atr=0.5, need_reject=False, min_touch=1)
SR_PLAN = dict(stop_atr=1.0, tp_r=2.0)     # stop = seviye ∓ 1 ATR, hedef = 2R
NEAR_ATR = 0.5                              # "seviyeye yakın" eşiği

_CACHE = {}; _LOCK = threading.Lock()


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _kl(sym, interval, limit):
    d = _get(f"{_BYBIT}/v5/market/kline?category=linear&symbol={sym}&interval={interval}&limit={limit}")
    rows = sorted(d["result"]["list"], key=lambda x: int(x[0]))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6])) for r in rows]


def _src_label(src):
    return {"swing5": "5dk swing", "swing1h": "1s swing", "PDH": "önceki gün H", "PDL": "önceki gün L",
            "PWH": "önceki hafta H", "PWL": "önceki hafta L"}.get(src, src.replace("pivot:", "pivot "))


def _detail(item, tf="5"):
    s, tov, fr, pc = item
    try:
        b5 = _kl(s, tf, 600); b1 = _kl(s, "60", 200)
    except Exception:
        return None
    if len(b5) < 320: return None
    lp = LevelProvider(b5, b1, None, **SR_LEVELS)
    i = len(b5)-2                     # son KAPANMIŞ mum (sonuncusu canlı)
    a = lp.atr[i]
    if not a: return None
    price = b5[i][4]; tol = lp.tol(i)
    lv = lp.levels(i)
    # seviye listesi: bölgedeki kaynakları birleştir (confluence sayısı)
    merged = []
    for x in sorted(lv, key=lambda z: z["p"]):
        if merged and abs(x["p"]-merged[-1]["p"])/merged[-1]["p"] <= tol:
            m = merged[-1]; m["srcs"].append(x["src"]); m["n"] = max(m["n"], x["n"]); m["p"] = (m["p"]+x["p"])/2
            if m["kind"] != x["kind"]: m["kind"] = 'B'
        else:
            merged.append({"p": x["p"], "kind": x["kind"], "n": x["n"], "srcs": [x["src"]]})
    sup = [m for m in merged if m["p"] < price and m["kind"] in ('S', 'B')]
    res = [m for m in merged if m["p"] > price and m["kind"] in ('R', 'B')]
    sup.sort(key=lambda m: -m["p"]); res.sort(key=lambda m: m["p"])
    def pack(m):
        return {"p": round(m["p"], 8), "dist_atr": round(abs(price-m["p"])/a, 2), "dist_pct": round((m["p"]/price-1)*100, 2),
                "n": m["n"], "srcs": [_src_label(x) for x in m["srcs"]], "conf": len(set(x.split(":")[0] for x in m["srcs"]))}
    sups = [pack(m) for m in sup[:3]]; ress = [pack(m) for m in res[:3]]
    # sinyaller — son 30 barlık pencerede üret, son kapanmış mumdakini "taze" say
    start = max(300, i-30)
    sig = None; recent = []
    for typ, fn, kw in (("bounce", gen_bounce, SR_BOUNCE), ("breakout", gen_breakout, SR_BREAK), ("retest", gen_retest, SR_RETEST)):
        try: out = fn(lp, start=start, **kw)
        except Exception: out = []
        for x in out:
            age = i-x["i"]
            if age < 0 or age > 6: continue
            rec = {"type": typ, "side": "LONG" if x["side"] > 0 else "SHORT", "age": age, "level": x["level"],
                   "src": x["lv"]["src"], "n": x["lv"]["n"], "entry": x["entry"], "atr": x["atr"], "sd": x["side"]}
            recent.append(rec)
            if age == 0 and (sig is None or rec["n"] > sig["n"]): sig = rec
    near = None
    if sups and sups[0]["dist_atr"] <= NEAR_ATR: near = ("S", sups[0])
    if ress and ress[0]["dist_atr"] <= NEAR_ATR and (near is None or ress[0]["dist_atr"] < near[1]["dist_atr"]): near = ("R", ress[0])
    row = {"symbol": s, "price": price, "atr_pct": round(a/price*100, 2), "vol24": int(tov), "funding": round(fr, 6),
           "pc24": round(pc*100, 2), "supports": sups, "resistances": ress, "bar_ts": b5[i][0],
           "near": {"kind": near[0], **near[1]} if near else None, "signal": None,
           "recent": [{k: v for k, v in r.items() if k != "sd"} for r in sorted(recent, key=lambda r: r["age"])[:3]]}
    if sig:
        sd = sig["sd"]; p = sig["level"]; entry = sig["entry"]; k = SR_PLAN["stop_atr"]
        stop = p-k*a if sd > 0 else p+k*a
        if sd > 0 and stop >= entry: stop = entry-k*a
        if sd < 0 and stop <= entry: stop = entry+k*a
        R = (entry-stop)*sd; tp = entry+sd*SR_PLAN["tp_r"]*R
        opp = (ress[0] if ress else None) if sd > 0 else (sups[0] if sups else None)
        row["signal"] = {"type": sig["type"], "side": sig["side"], "level": round(p, 8), "src": _src_label(sig["src"]), "n": sig["n"],
                         "entry": round(entry, 8), "stop": round(stop, 8), "stop_pct": round(R/entry*100, 2),
                         "tp": round(tp, 8), "tp_pct": round(SR_PLAN["tp_r"]*R/entry*100, 2), "rr": SR_PLAN["tp_r"],
                         "next_level": opp["p"] if opp else None, "next_level_r": round(abs(opp["p"]-entry)/R, 2) if opp else None}
    return row


def sr_board(tf="5", max_detail=60):
    tf = tf if tf in SR_TF_ALLOWED else "5"
    with _LOCK:
        c = _CACHE.get(tf)
        if c and time.time()-c["ts"] < 30: return c["data"]
    tk = _get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = []
    for t in tk:
        s = t["symbol"]
        if not s.endswith("USDT"): continue
        try: tov = float(t["turnover24h"]); fr = float(t.get("fundingRate") or 0); pc = float(t["price24hPcnt"])
        except (KeyError, ValueError, TypeError): continue
        if tov >= SR_MIN_TURNOVER: liq.append((s, tov, fr, pc))
    liq.sort(key=lambda x: x[1], reverse=True)
    with ThreadPoolExecutor(max_workers=16) as ex:
        rows = [r for r in ex.map(lambda it: _detail(it, tf), liq[:SR_UNIVERSE]) if r]
    def rank(r):
        sg = r["signal"]; sc = 0
        if sg: sc += 100 + sg["n"]*5 + (10 if "1s" in sg["src"] or "önceki" in sg["src"] else 0)
        elif r["recent"]: sc += 50 - r["recent"][0]["age"]*5
        if r["near"]: sc += 20 + r["near"]["conf"]*5 + r["near"]["n"]
        return sc
    rows.sort(key=rank, reverse=True)
    sigs = [r for r in rows if r["signal"]]
    data = {"candidates": rows[:max_detail], "count": len(rows), "tf": tf, "signals": len(sigs),
            "longs": sum(1 for r in sigs if r["signal"]["side"] == "LONG"), "shorts": sum(1 for r in sigs if r["signal"]["side"] == "SHORT"),
            "near": sum(1 for r in rows if r["near"]), "ts": int(time.time()), "plan": SR_PLAN}
    with _LOCK: _CACHE[tf] = {"ts": time.time(), "data": data}
    return data


if __name__ == "__main__":
    import sys
    d = sr_board(sys.argv[1] if len(sys.argv) > 1 else "5")
    print(json.dumps({k: v for k, v in d.items() if k != "candidates"}, ensure_ascii=False))
    for r in d["candidates"][:15]:
        print(r["symbol"], r["price"], "S:", [(x["p"], x["dist_atr"], x["n"]) for x in r["supports"][:2]], "R:", [(x["p"], x["dist_atr"], x["n"]) for x in r["resistances"][:2]], "SIG:", r["signal"], "near:", r["near"] and r["near"]["kind"])
