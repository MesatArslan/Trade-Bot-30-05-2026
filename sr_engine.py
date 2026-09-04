#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DESTEK/DİRENÇ MOTORU — walk-forward (ileriye bakmaz), çoklu zaman dilimi seviye üretimi + sinyal üretimi.

Veri formatı: bars = list of (ts_ms, o, h, l, c, vol, turnover); 5dk ana seri (k5m_180.pkl), 1s (k1h_vol.pkl), 4s (k4h_vol.pkl).

SEVİYE KAYNAKLARI (source):
  "swing5"  : 5dk fractal swing H/L (pencere w), lookback L bar
  "swing1h" : 1s fractal swing, lookback gün
  "swing4h" : 4s fractal swing, lookback gün
  "pivot"   : klasik günlük pivot (önceki UTC günün H/L/C → PP,R1,S1,R2,S2)
  "pdhl"    : önceki gün High/Low + önceki hafta High/Low
  "round"   : yuvarlak sayılar (fiyat büyüklüğüne göre 1-2-5 adımı)

Her seviye: dict(p=fiyat, kind='S'|'R'|'B' (B=iki yönlü, ör. pivot), n=temas sayısı, src=kaynak, born=doğrulama ts)

KRİTİK — İLERİYE BAKMAMA: bir swing high i indeksinde oluşur ama ancak i+w barı KAPANDIĞINDA doğrulanır.
Seviye listesi o andan önce kullanılamaz. HTF seviyeleri: HTF barının KAPANIŞ zamanı ≤ 5dk barın kapanış zamanı.

SİNYALLER (5dk bar t kapanışında değerlendirilir, giriş = t kapanış fiyatı, simülasyon t+1'den başlar):
  bounce  : destekten dönüş LONG / dirençten dönüş SHORT (zone temas + kapanış seviyenin doğru tarafında)
  breakout: direnç üstü kapanış LONG / destek altı kapanış SHORT (buffer = b×ATR)
  retest  : kırılım sonrası seviyeye geri dönüş + tutma → kırılım yönünde
"""
import bisect, math, datetime as dt
from collections import defaultdict

MS5 = 300_000; MS1H = 3_600_000; MS4H = 4*MS1H; MSD = 24*MS1H; MSW = 7*MSD


# ───────────────────────── yardımcılar ─────────────────────────
def atr_series(bars, n=14):
    out = [None]*len(bars); prev = None
    for i, b in enumerate(bars):
        h, l = b[2], b[3]; pc = bars[i-1][4] if i else b[4]
        tr = max(h-l, abs(h-pc), abs(l-pc))
        if i < n: prev = tr if prev is None else prev+(tr-prev)/(i+1); out[i] = None if i < n-1 else prev
        else: prev = (prev*(n-1)+tr)/n; out[i] = prev
    return out


def vol_ma(bars, n=20):
    out = [None]*len(bars); s = 0.0
    for i, b in enumerate(bars):
        s += b[5]
        if i >= n: s -= bars[i-n][5]
        if i >= n-1: out[i] = s/n
    return out


def ema_series(bars, n):
    out = [None]*len(bars); k = 2/(n+1); e = None
    for i, b in enumerate(bars):
        e = b[4] if e is None else e+(b[4]-e)*k
        out[i] = e if i >= n else None
    return out


def swings(bars, w, tf_ms):
    """Fractal swing noktaları. Dönüş: liste of (confirm_ts, price, 'H'|'L', idx).
    confirm_ts = (i+w). barın KAPANIŞ zamanı = bars[i+w].ts + tf_ms"""
    out = []; n = len(bars)
    for i in range(w, n-w):
        h = bars[i][2]; l = bars[i][3]
        if all(bars[j][2] <= h for j in range(i-w, i+w+1) if j != i):
            out.append((bars[i+w][0]+tf_ms, h, 'H', i))
        if all(bars[j][3] >= l for j in range(i-w, i+w+1) if j != i):
            out.append((bars[i+w][0]+tf_ms, l, 'L', i))
    out.sort(key=lambda x: x[0])
    return out


def cluster(points, tol_pct):
    """points: list of (price, kind, ts). Yakın fiyatları birleştir → list of dict(p, n, kind, last_ts)."""
    if not points: return []
    pts = sorted(points, key=lambda x: x[0]); res = []
    for p, k, ts in pts:
        if res and abs(p-res[-1]["p"])/res[-1]["p"] <= tol_pct:
            g = res[-1]; g["members"].append(p); g["p"] = sum(g["members"])/len(g["members"]); g["n"] += 1
            g["last_ts"] = max(g["last_ts"], ts); g["kinds"].add(k)
        else:
            res.append({"p": p, "n": 1, "members": [p], "last_ts": ts, "kinds": {k}})
    for g in res:
        ks = {{'L': 'S', 'H': 'R'}.get(k, k) for k in g["kinds"]}   # swing L → destek(S), swing H → direnç(R)
        g["kind"] = 'B' if len(ks) > 1 else list(ks)[0]
        del g["members"]; del g["kinds"]
    return res


def day_start(ts): return ts - (ts % MSD)
def week_start(ts):
    d = dt.datetime.fromtimestamp(ts/1000, dt.timezone.utc); wd = d.weekday()
    return day_start(ts) - wd*MSD


def round_step(price):
    """Fiyat büyüklüğüne göre 'yuvarlak sayı' adımı (~%2-5 aralık)."""
    if price <= 0: return None
    e = math.floor(math.log10(price)); base = 10**e
    for m in (0.1, 0.2, 0.5, 1.0):
        if base*m/price >= 0.02: return base*m
    return base


# ───────────────────────── seviye sağlayıcı ─────────────────────────
class LevelProvider:
    """Bir coin için, verilen 5dk barında 'o an bilinebilen' seviye listesini üretir.
    Seviyeler refresh_every bar'da bir yeniden hesaplanır (hız için)."""

    def __init__(self, bars5, bars1h=None, bars4h=None, sources=("swing5",), w5=3, lb5=288,
                 w1h=3, lb1h_days=7, w4h=2, lb4h_days=30, tol_atr=0.5, tol_min=0.0015,
                 refresh_every=6, min_touch=1):
        self.b = bars5; self.sources = set(sources); self.n = len(bars5)
        self.atr = atr_series(bars5, 14)
        self.w5, self.lb5 = w5, lb5; self.lb1h = lb1h_days*MSD; self.lb4h = lb4h_days*MSD
        self.tol_atr, self.tol_min = tol_atr, tol_min; self.refresh = refresh_every; self.min_touch = min_touch
        self.sw5 = swings(bars5, w5, MS5) if "swing5" in self.sources else []
        self.sw1h = swings(bars1h, w1h, MS1H) if ("swing1h" in self.sources and bars1h) else []
        self.sw4h = swings(bars4h, w4h, MS4H) if ("swing4h" in self.sources and bars4h) else []
        self.sw5_ts = [s[0] for s in self.sw5]; self.sw1h_ts = [s[0] for s in self.sw1h]; self.sw4h_ts = [s[0] for s in self.sw4h]
        # günlük/haftalık H-L-C (önceki tamamlanmış periyot) — 5dk barlardan
        self.days = self._periods(day_start); self.weeks = self._periods(week_start)
        self._cache_i = -999; self._cache = []

    def _periods(self, keyfn):
        agg = {}
        for b in self.b:
            k = keyfn(b[0]); a = agg.get(k)
            if a is None: agg[k] = [b[2], b[3], b[4], b[0]]
            else: a[0] = max(a[0], b[2]); a[1] = min(a[1], b[3]); a[2] = b[4]; a[3] = b[0]
        keys = sorted(agg); return keys, [agg[k] for k in keys]

    def _prev_period(self, per, now_ts, keyfn):
        keys, vals = per; cur = keyfn(now_ts); j = bisect.bisect_left(keys, cur)-1
        if j < 0: return None
        return vals[j]  # [H, L, C, last_ts]

    def tol(self, i):
        a = self.atr[i]; p = self.b[i][4]
        return max((a or 0)/p*self.tol_atr, self.tol_min)

    def levels(self, i):
        """5dk bar i KAPANIŞINDA bilinen seviyeler. Liste of dict(p, kind, n, src)."""
        if i-self._cache_i < self.refresh and self._cache_i >= 0 and (i//self.refresh) == (self._cache_i//self.refresh):
            return self._cache
        close_ts = self.b[i][0]+MS5; price = self.b[i][4]; tol = self.tol(i); out = []
        if "swing5" in self.sources:
            lo_ts = self.b[max(0, i-self.lb5)][0]
            j = bisect.bisect_right(self.sw5_ts, close_ts); pts = [(s[1], s[2], s[0]) for s in self.sw5[:j] if s[0] >= lo_ts]
            for g in cluster(pts, tol):
                if g["n"] >= self.min_touch: out.append({"p": g["p"], "kind": g["kind"], "n": g["n"], "src": "swing5"})
        if "swing1h" in self.sources:
            j = bisect.bisect_right(self.sw1h_ts, close_ts); pts = [(s[1], s[2], s[0]) for s in self.sw1h[:j] if s[0] >= close_ts-self.lb1h]
            for g in cluster(pts, tol): out.append({"p": g["p"], "kind": g["kind"], "n": g["n"], "src": "swing1h"})
        if "swing4h" in self.sources:
            j = bisect.bisect_right(self.sw4h_ts, close_ts); pts = [(s[1], s[2], s[0]) for s in self.sw4h[:j] if s[0] >= close_ts-self.lb4h]
            for g in cluster(pts, tol): out.append({"p": g["p"], "kind": g["kind"], "n": g["n"], "src": "swing4h"})
        if "pivot" in self.sources:
            d = self._prev_period(self.days, self.b[i][0], day_start)
            if d:
                H, L, C = d[0], d[1], d[2]; PP = (H+L+C)/3
                for name, p in (("PP", PP), ("R1", 2*PP-L), ("S1", 2*PP-H), ("R2", PP+(H-L)), ("S2", PP-(H-L))):
                    out.append({"p": p, "kind": 'B', "n": 1, "src": "pivot:"+name})
        if "pdhl" in self.sources:
            d = self._prev_period(self.days, self.b[i][0], day_start)
            if d: out += [{"p": d[0], "kind": 'R', "n": 1, "src": "PDH"}, {"p": d[1], "kind": 'S', "n": 1, "src": "PDL"}]
            wk = self._prev_period(self.weeks, self.b[i][0], week_start)
            if wk: out += [{"p": wk[0], "kind": 'R', "n": 1, "src": "PWH"}, {"p": wk[1], "kind": 'S', "n": 1, "src": "PWL"}]
        if "round" in self.sources:
            st_ = round_step(price)
            if st_:
                base = math.floor(price/st_)*st_
                for k in (-2, -1, 0, 1, 2, 3): out.append({"p": base+k*st_, "kind": 'B', "n": 1, "src": "round"})
        self._cache_i = i; self._cache = out
        return out

    def nearest(self, i, side):
        """side=+1 → fiyatın ALTINDAKİ en yakın destek; -1 → ÜSTÜNDEKİ en yakın direnç. (seviye dict | None)"""
        price = self.b[i][4]; best = None
        for lv in self.levels(i):
            if side > 0 and lv["p"] < price and lv["kind"] in ('S', 'B'):
                if best is None or lv["p"] > best["p"]: best = lv
            if side < 0 and lv["p"] > price and lv["kind"] in ('R', 'B'):
                if best is None or lv["p"] < best["p"]: best = lv
        return best


# ───────────────────────── sinyal üreticiler ─────────────────────────
def gen_bounce(lp, zone_atr=0.5, max_pierce_atr=1.0, need_reject=True, fresh_bars=6, min_touch=1,
               kinds_long=('S', 'B'), kinds_short=('R', 'B'), vol_mult=None, start=300):
    """Destekten dönüş LONG / dirençten dönüş SHORT.
    LONG şartı: low ≤ S+zone (temas), low ≥ S−max_pierce (kırmadı), close > S, (reject: close>open ve close üst yarıda),
    son fresh_bars bar aynı seviyeye temas etmemiş. SHORT aynalı.
    Dönüş: list of dict(i, side, entry, level, lv, atr)"""
    b = lp.b; atr = lp.atr; vm = vol_ma(b, 20) if vol_mult else None; out = []; last_touch = {}
    for i in range(start, lp.n-1):
        a = atr[i]
        if not a: continue
        o, h, l, c = b[i][1], b[i][2], b[i][3], b[i][4]
        if vol_mult and (not vm[i] or b[i][5] < vol_mult*vm[i]): continue
        for lv in lp.levels(i):
            if lv["n"] < min_touch: continue
            p = lv["p"]; key = (lv["src"], round(p, 10))
            if lv["kind"] in kinds_long and l <= p+zone_atr*a and l >= p-max_pierce_atr*a and c > p and p < c:
                if need_reject and not (c > o and (c-l) >= 0.6*(h-l if h > l else 1e-12)): continue
                if i-last_touch.get(key, -999) <= fresh_bars: last_touch[key] = i; continue
                last_touch[key] = i
                out.append({"i": i, "side": 1, "entry": c, "level": p, "lv": lv, "atr": a}); break
            if lv["kind"] in kinds_short and h >= p-zone_atr*a and h <= p+max_pierce_atr*a and c < p:
                if need_reject and not (c < o and (h-c) >= 0.6*(h-l if h > l else 1e-12)): continue
                if i-last_touch.get(key, -999) <= fresh_bars: last_touch[key] = i; continue
                last_touch[key] = i
                out.append({"i": i, "side": -1, "entry": c, "level": p, "lv": lv, "atr": a}); break
    return out


def gen_breakout(lp, buf_atr=0.25, prior_bars=12, vol_mult=None, min_touch=1, cooldown=12, start=300):
    """Direnç üstü kapanış LONG / destek altı kapanış SHORT. prior_bars boyunca kapanışlar seviyenin diğer tarafında olmalı."""
    b = lp.b; atr = lp.atr; vm = vol_ma(b, 20) if vol_mult else None; out = []; last = {}
    for i in range(start, lp.n-1):
        a = atr[i]
        if not a: continue
        c = b[i][4]
        if vol_mult and (not vm[i] or b[i][5] < vol_mult*vm[i]): continue
        for lv in lp.levels(i):
            if lv["n"] < min_touch: continue
            p = lv["p"]; key = (lv["src"], round(p, 10))
            if i-last.get(key, -999) <= cooldown: continue
            if lv["kind"] in ('R', 'B') and c > p+buf_atr*a and all(b[j][4] <= p for j in range(i-prior_bars, i)):
                last[key] = i; out.append({"i": i, "side": 1, "entry": c, "level": p, "lv": lv, "atr": a}); break
            if lv["kind"] in ('S', 'B') and c < p-buf_atr*a and all(b[j][4] >= p for j in range(i-prior_bars, i)):
                last[key] = i; out.append({"i": i, "side": -1, "entry": c, "level": p, "lv": lv, "atr": a}); break
    return out


def gen_retest(lp, buf_atr=0.25, prior_bars=12, retest_within=36, zone_atr=0.5, need_reject=False, min_touch=1, start=300):
    """Kırılım (breakout ile aynı tanım) → sonraki retest_within bar içinde seviyeye geri dönüş (zone temas) + kapanış kırılım tarafında → giriş."""
    brk = gen_breakout(lp, buf_atr, prior_bars, None, min_touch, cooldown=retest_within, start=start)
    b = lp.b; atr = lp.atr; out = []
    for s in brk:
        p = s["level"]; side = s["side"]
        for j in range(s["i"]+2, min(lp.n-1, s["i"]+retest_within)):
            a = atr[j]; o, h, l, c = b[j][1], b[j][2], b[j][3], b[j][4]
            if not a: continue
            if side > 0:
                if c < p-zone_atr*a: break  # kırılım başarısız
                if l <= p+zone_atr*a and c > p and (not need_reject or c > o):
                    out.append({"i": j, "side": 1, "entry": c, "level": p, "lv": s["lv"], "atr": a, "brk_i": s["i"]}); break
            else:
                if c > p+zone_atr*a: break
                if h >= p-zone_atr*a and c < p and (not need_reject or c < o):
                    out.append({"i": j, "side": -1, "entry": c, "level": p, "lv": s["lv"], "atr": a, "brk_i": s["i"]}); break
    return out


# ───────────────────────── ölçüm ─────────────────────────
def fwd_returns(bars, i, side, atr, horizons=(6, 12, 24, 48)):
    """Sinyal yönünde ileri getiri (ATR birimi) + MFE/MAE (ATR)."""
    c0 = bars[i][4]; out = {}
    for hzn in horizons:
        j = min(len(bars)-1, i+hzn)
        out[f"r{hzn}"] = (bars[j][4]-c0)*side/atr
        hi = max(b[2] for b in bars[i+1:j+1]) if j > i else c0; lo = min(b[3] for b in bars[i+1:j+1]) if j > i else c0
        out[f"mfe{hzn}"] = ((hi-c0) if side > 0 else (c0-lo))/atr
        out[f"mae{hzn}"] = ((c0-lo) if side > 0 else (hi-c0))/atr
    return out


def month_key(ts): return dt.datetime.fromtimestamp(ts/1000, dt.timezone.utc).strftime("%Y-%m")
def hour_tr(ts): return int(dt.datetime.fromtimestamp((ts+3*3600000)/1000, dt.timezone.utc).strftime("%H"))


def agg(Rs):
    if not Rs: return None
    n = len(Rs); gp = sum(r for r in Rs if r > 0); gl = -sum(r for r in Rs if r < 0)
    wins = [r for r in Rs if r > 0]; losses = [r for r in Rs if r <= 0]
    aw = sum(wins)/len(wins) if wins else 0; al = -sum(losses)/len(losses) if losses else 0
    be = al/(aw+al)*100 if (aw+al) > 0 else 0
    return {"n": n, "hit%": round(sum(1 for r in Rs if r > 0)/n*100, 1), "avgR": round(sum(Rs)/n, 3),
            "totR": round(sum(Rs), 1), "PF": round(gp/gl, 2) if gl else 99.0, "avgWin": round(aw, 2), "avgLoss": round(al, 2),
            "breakeven_hit%": round(be, 1)}


def fmt(k, r, w=40):
    if r is None: return f"{k:>{w}}  (boş)"
    flag = " ✅" if r["avgR"] > 0 else ""
    return (f"{k:>{w}}  n={r['n']:>6}  hit={r['hit%']:>5}%  avgR={r['avgR']:>+7.3f}  PF={r['PF']:>5}  "
            f"W={r['avgWin']:>5} L={r['avgLoss']:>5}  başabaş={r['breakeven_hit%']:>5}%{flag}")


def by_month(trades):
    by = defaultdict(list)
    for t in trades: by[month_key(t["ts"])].append(t["R"])
    return {m: agg(v) for m, v in sorted(by.items())}


def month_line(trades):
    bm = by_month(trades); pos = sum(1 for v in bm.values() if v and v["avgR"] > 0)
    return f"aylar +:{pos}/{len(bm)}  " + " ".join(f"{m[2:]}:{v['avgR']:+.2f}" for m, v in bm.items())
