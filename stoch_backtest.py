#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stoch_backtest.py — 📶 StochRSI İzleme sekmesinin geriye dönük testi.

Olay: 1dk StochRSI(14,14,3,3) %K, 30-70 bandından 30 ALTINA iner ("alt") veya 70 ÜSTÜNE çıkar ("ust") — sekmedeki uyarı mantığının aynısı
(bölge geçişi, aynı bölgede tekrar yok). Her olaydan sonra 5/15/30/60/120/240 dk ileri getiri (olay mumunun KAPANIŞINDAN giriş; mum kapanmadan
sinyal verilmediği varsayımı — sekme 20 sn'de bir açık mumla bakar, bu test biraz iyimser/kötümser olabilir, aşağıda ayrıca "sonraki mum açılışı" ile de ölçülür).

Ölçülenler:
  - alt→LONG (aşırı satım sıçraması), ust→SHORT (aşırı alım dönüşü)  = ters dönüş yorumu
  - alt→SHORT, ust→LONG                                                  = devam yorumu (aynı sayıların işareti ters)
  - plasebo: aynı coinde rastgele dakikalar, aynı adet
  - filtre: 4h trend (4h kapanış EMA20>EMA50 = yukarı) ile uyumlu olaylar (sekmeye Toplu Analiz'den eklenen coin mantığı)
  - istikrar: ilk yarı / ikinci yarı
Maliyet: MEXC taker %0.03 × 2 = %0.06 gidiş-dönüş (ayrıca gösterilir; kullanıcı brüt ister).

Kullanım: .venv/bin/python stoch_backtest.py [--gun 30] [--coin N] [--evren MAGIC,KAIA,...]
Veri önbelleği: data/stoch_izle/bt/<SYM>.npy
"""
import os, sys, time, json
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mexc
from stoch_izle import stoch_rsi, ALT, UST

HERE = os.path.dirname(os.path.abspath(__file__))
BT = os.path.join(HERE, "data", "stoch_izle", "bt"); os.makedirs(BT, exist_ok=True)
UFUK = [5, 15, 30, 60, 120, 240]
MALIYET = 0.06  # % gidiş-dönüş


def cek(sym, gun):
    f = os.path.join(BT, f"{sym}_{gun}g.npy")
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < 6 * 3600:
        return np.load(f)
    end = int(time.time()); start = end - gun * 86400
    rows = []; cur = start
    while cur < end:
        e = min(cur + 2000 * 60, end)
        d = mexc.get(f"/api/v1/contract/kline/{sym}", interval="Min1", start=cur, end=e)
        if d and d.get("time"):
            for i in range(len(d["time"])):
                rows.append((int(d["time"][i]), float(d["open"][i]), float(d["high"][i]), float(d["low"][i]), float(d["close"][i])))
        cur = e; time.sleep(0.12)
    if not rows: return None
    a = np.array(sorted(set(rows)), float)
    np.save(f, a); return a


def olaylar(c):
    k, d, r = stoch_rsi(c)
    bolge = np.where(np.isnan(k), 0, np.where(k < ALT, -1, np.where(k > UST, 1, 0)))
    ev = []
    for i in range(1, len(k)):
        if bolge[i] != 0 and bolge[i] != bolge[i - 1] and not np.isnan(k[i]):
            ev.append((i, int(bolge[i])))
    return ev


def ileri(c, o, i, giris="kapanis"):
    """olay mumu i; giriş: olay mumu kapanışı ya da sonraki mum açılışı. % getiri (long yönü)."""
    p0 = c[i] if giris == "kapanis" else (o[i + 1] if i + 1 < len(o) else np.nan)
    out = []
    for h in UFUK:
        j = i + h
        out.append((c[j] / p0 - 1) * 100 if j < len(c) and p0 == p0 else np.nan)
    return out


def trend4h(ts, c):
    """her 1dk bara, o anki (tamamlanmış) 4h EMA20>EMA50 durumu: +1/-1/0"""
    b4 = (ts // 14400).astype(int)
    uniq, idx = np.unique(b4, return_index=True)
    close4 = np.array([c[(np.searchsorted(b4, u, side="right") - 1)] for u in uniq])  # her 4h kovasının son kapanışı (ileriye bakar!) → bir kova geriden kullan
    def ema(x, n):
        a = 2 / (n + 1); o = np.empty_like(x); o[0] = x[0]
        for i in range(1, len(x)): o[i] = a * x[i] + (1 - a) * o[i - 1]
        return o
    e20, e50 = ema(close4, 20), ema(close4, 50)
    sig = np.where(e20 > e50, 1, -1); sig[:50] = 0
    kova_i = np.searchsorted(uniq, b4, side="right") - 1          # bu barın kovası
    prev = kova_i - 1                                               # tamamlanmış önceki kova
    out = np.where(prev >= 0, sig[np.clip(prev, 0, len(sig) - 1)], 0)
    return out


def ozet(x):
    x = x[~np.isnan(x)]
    if len(x) < 5: return None
    m = x.mean(); s = x.std(ddof=1) if len(x) > 1 else 0
    return {"n": int(len(x)), "ort": round(float(m), 3), "med": round(float(np.median(x)), 3), "isabet": round(float((x > 0).mean() * 100), 1),
            "t": round(float(m / (s / np.sqrt(len(x)))), 2) if s > 0 else 0.0}


def main():
    a = sys.argv[1:]; gun = 30; ncoin = 40; evren = None
    while a:
        k = a.pop(0)
        if k == "--gun": gun = int(a.pop(0))
        elif k == "--coin": ncoin = int(a.pop(0))
        elif k == "--evren": evren = [x.strip().upper() for x in a.pop(0).split(",")]
    if evren is None:
        t = mexc.get("/api/v1/contract/ticker") or []
        rows = sorted([r for r in t if r.get("symbol", "").endswith("_USDT")], key=lambda r: -float(r.get("amount24", 0)))
        evren = [r["symbol"].replace("_USDT", "") for r in rows[:ncoin]]
    print(f"evren {len(evren)} coin · {gun} gün · 1dk · StochRSI %K {ALT}/{UST} geçişi\n")
    R = {"kapanis": {"alt": {h: [] for h in UFUK}, "ust": {h: [] for h in UFUK}}, "acilis": {"alt": {h: [] for h in UFUK}, "ust": {h: [] for h in UFUK}}}
    PL = {h: [] for h in UFUK}
    F = {"alt_up": {h: [] for h in UFUK}, "alt_dn": {h: [] for h in UFUK}, "ust_up": {h: [] for h in UFUK}, "ust_dn": {h: [] for h in UFUK}}
    Y = {"ilk": {"alt": {h: [] for h in UFUK}, "ust": {h: [] for h in UFUK}}, "son": {"alt": {h: [] for h in UFUK}, "ust": {h: [] for h in UFUK}}}
    percoin = []
    rng = np.random.default_rng(7)
    for ci, coin in enumerate(evren):
        sym = coin + "_USDT"; a_ = cek(sym, gun)
        if a_ is None or len(a_) < 2000: print(f"  {coin}: veri yok"); continue
        ts, o, h, l, c = a_.T
        ev = olaylar(c); tr = trend4h(ts, c); mid = ts[0] + (ts[-1] - ts[0]) / 2
        n_alt = sum(1 for _, b in ev if b == -1); n_ust = len(ev) - n_alt
        cr30 = []
        for i, b in ev:
            key = "alt" if b == -1 else "ust"
            r1 = ileri(c, o, i, "kapanis"); r2 = ileri(c, o, i, "acilis")
            for hh, v1, v2 in zip(UFUK, r1, r2):
                R["kapanis"][key][hh].append(v1); R["acilis"][key][hh].append(v2)
                F[f"{key}_{'up' if tr[i] == 1 else 'dn'}"][hh].append(v1) if tr[i] != 0 else None
                Y["ilk" if ts[i] < mid else "son"][key][hh].append(v1)
            cr30.append(r1[2] * (1 if key == "alt" else -1))
        # plasebo: aynı adet rastgele bar
        for i in rng.integers(100, len(c) - 300, size=len(ev)):
            for hh, v in zip(UFUK, ileri(c, o, int(i), "kapanis")): PL[hh].append(v)
        percoin.append((coin, len(ev), float(np.nanmean(cr30)) if cr30 else np.nan))
        print(f"  {coin:<10} bar {len(c):>6}  olay alt {n_alt:>4} ust {n_ust:>4}  ters-dönüş 30dk brüt ort {np.nanmean(cr30):+.3f}%")
    print("\n=== TERS DÖNÜŞ yorumu (alt→LONG, ust→SHORT) · olay mumu KAPANIŞINDAN giriş · brüt % ===")
    print(f"{'ufuk':>6} | {'alt→LONG n':>10} {'ort':>7} {'med':>7} {'isabet':>7} {'t':>6} | {'ust→SHORT n':>11} {'ort':>7} {'med':>7} {'isabet':>7} {'t':>6} | {'plasebo ort':>11} {'|ort|':>6}")
    for hh in UFUK:
        A = ozet(np.array(R["kapanis"]["alt"][hh])); U = ozet(-np.array(R["kapanis"]["ust"][hh])); P = ozet(np.array(PL[hh])); PA = ozet(np.abs(np.array(PL[hh])))
        print(f"{hh:>4}dk | {A['n']:>10} {A['ort']:>+7.3f} {A['med']:>+7.3f} {A['isabet']:>6.1f}% {A['t']:>6} | {U['n']:>11} {U['ort']:>+7.3f} {U['med']:>+7.3f} {U['isabet']:>6.1f}% {U['t']:>6} | {P['ort']:>+11.3f} {PA['ort']:>6.3f}")
    print(f"\n(maliyet: MEXC taker gidiş-dönüş −{MALIYET}% → net = brüt − {MALIYET})")
    print("\n=== Aynı şey, SONRAKİ MUM AÇILIŞINDAN giriş (gerçekçi) ===")
    for hh in UFUK:
        A = ozet(np.array(R["acilis"]["alt"][hh])); U = ozet(-np.array(R["acilis"]["ust"][hh]))
        print(f"{hh:>4}dk | alt→LONG n {A['n']} ort {A['ort']:+.3f} isabet {A['isabet']}% t {A['t']} | ust→SHORT n {U['n']} ort {U['ort']:+.3f} isabet {U['isabet']}% t {U['t']}")
    print("\n=== 4h TREND FİLTRESİ (Toplu Analiz mantığı: trend yönünde gir) · kapanış girişi · ters-dönüş yönü ===")
    for hh in UFUK:
        a1 = ozet(np.array(F["alt_up"][hh])); a2 = ozet(np.array(F["alt_dn"][hh])); u1 = ozet(-np.array(F["ust_dn"][hh])); u2 = ozet(-np.array(F["ust_up"][hh]))
        f = lambda z: f"n {z['n']} ort {z['ort']:+.3f} t {z['t']}" if z else "—"
        print(f"{hh:>4}dk | alt→LONG & 4h YUKARI: {f(a1)} | alt→LONG & 4h AŞAĞI: {f(a2)} || ust→SHORT & 4h AŞAĞI: {f(u1)} | ust→SHORT & 4h YUKARI: {f(u2)}")
    print("\n=== İSTİKRAR: ilk yarı / ikinci yarı (ters-dönüş, kapanış) ===")
    for hh in (15, 30, 60):
        for key, sg in (("alt", 1), ("ust", -1)):
            i1 = ozet(sg * np.array(Y["ilk"][key][hh])); i2 = ozet(sg * np.array(Y["son"][key][hh]))
            print(f"{hh:>4}dk {key}: ilk yarı ort {i1['ort']:+.3f} t {i1['t']} (n {i1['n']}) · ikinci yarı ort {i2['ort']:+.3f} t {i2['t']} (n {i2['n']})")
    pc = sorted([p for p in percoin if p[2] == p[2]], key=lambda x: -x[2])
    print("\n=== Coin bazında 30dk ters-dönüş brüt ort (en iyi 5 / en kötü 5) ===")
    for p in pc[:5]: print(f"  {p[0]:<10} olay {p[1]:>4}  {p[2]:+.3f}%")
    print("  ...")
    for p in pc[-5:]: print(f"  {p[0]:<10} olay {p[1]:>4}  {p[2]:+.3f}%")
    json.dump({"evren": evren, "gun": gun, "ufuk": UFUK, "kapanis": {k: {h: ozet(np.array(v) * (1 if k == 'alt' else -1)) for h, v in d.items()} for k, d in R["kapanis"].items()},
               "plasebo": {h: ozet(np.array(v)) for h, v in PL.items()}, "percoin": percoin},
              open(os.path.join(BT, "sonuc.json"), "w"), ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    main()
