#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coin_analiz.py — Tek coin için çok zaman dilimli TEKNİK özet + borsa duyuruları (coin-analist ajanı için).

Kullanım:
    .venv/bin/python coin_analiz.py SOL            # okunur rapor
    .venv/bin/python coin_analiz.py SOL --json     # ham JSON (ajan bunu okur)

Veri: MEXC perp mumları (mexc.klines), funding (mexc.funding_detail), OI (mexc.current_oi_usd),
      Bybit/Binance duyuruları (news_feed.recent_for, cache; --haber-yenile ile taze çeker).
Hesaplanan: EMA20/50/200, RSI14, ATR14, ADX14, Supertrend(10,3), hacim RVOL, 20 barlık en yüksek/düşük,
            ZigZag benzeri swing yapısı (HH/HL/LH/LL), BTC aynı dilimde yön, 24s/7g değişim.
Hiçbir şey "sinyal" değildir; ajan yorumlar. Sadece numpy + stdlib.
"""
import sys, json, time, math
import numpy as np

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import mexc  # noqa: E402

TFS = [  # (etiket, MEXC interval, dakika, kaç bar)
    ("5m",  "Min5",  5,    300),
    ("15m", "Min15", 15,   300),
    ("1h",  "Min60", 60,   300),
    ("4h",  "Hour4", 240,  300),
    ("1d",  "Day1",  1440, 200),
]


# ───────────────────────── indikatörler ─────────────────────────
def ema(x, n):
    x = np.asarray(x, float); a = 2 / (n + 1); out = np.empty_like(x); out[0] = x[0]
    for i in range(1, len(x)): out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def rsi(c, n=14):
    c = np.asarray(c, float); d = np.diff(c); up = np.where(d > 0, d, 0); dn = np.where(d < 0, -d, 0)
    if len(d) < n + 1: return np.full(len(c), np.nan)
    au = np.empty(len(d)); ad = np.empty(len(d)); au[n - 1] = up[:n].mean(); ad[n - 1] = dn[:n].mean()
    for i in range(n, len(d)):
        au[i] = (au[i - 1] * (n - 1) + up[i]) / n; ad[i] = (ad[i - 1] * (n - 1) + dn[i]) / n
    with np.errstate(over="ignore", divide="ignore"):
        rs = au / np.where(ad == 0, 1e-12, ad); r = 100 - 100 / (1 + rs)
    out = np.full(len(c), np.nan); out[n:] = r[n - 1:]; return out


def tr_(h, l, c):
    pc = np.r_[c[0], c[:-1]]; return np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))


def atr(h, l, c, n=14):
    t = tr_(h, l, c); out = np.full(len(c), np.nan)
    if len(c) < n: return out
    out[n - 1] = t[:n].mean()
    for i in range(n, len(c)): out[i] = (out[i - 1] * (n - 1) + t[i]) / n
    return out


def adx(h, l, c, n=14):
    if len(c) < 2 * n + 1: return np.nan, np.nan, np.nan
    up = h[1:] - h[:-1]; dn = l[:-1] - l[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0); ndm = np.where((dn > up) & (dn > 0), dn, 0)
    t = tr_(h, l, c)[1:]
    def wil(x):
        o = np.empty(len(x)); o[n - 1] = x[:n].sum()
        for i in range(n, len(x)): o[i] = o[i - 1] - o[i - 1] / n + x[i]
        return o
    tr14 = wil(t); pdi = 100 * wil(pdm) / np.where(tr14 == 0, 1e-12, tr14); ndi = 100 * wil(ndm) / np.where(tr14 == 0, 1e-12, tr14)
    dx = 100 * abs(pdi - ndi) / np.where(pdi + ndi == 0, 1e-12, pdi + ndi)
    dx = dx[n - 1:]; a = np.empty(len(dx)); a[n - 1] = dx[:n].mean()
    for i in range(n, len(dx)): a[i] = (a[i - 1] * (n - 1) + dx[i]) / n
    return float(a[-1]), float(pdi[-1]), float(ndi[-1])


def supertrend(h, l, c, n=10, m=3.0):
    a = atr(h, l, c, n); hl2 = (h + l) / 2; ub = hl2 + m * a; lb = hl2 - m * a
    fu = ub.copy(); fl = lb.copy(); dirn = np.ones(len(c), int); line = np.full(len(c), np.nan)
    first = int(np.argmax(~np.isnan(a)))
    for i in range(first + 1, len(c)):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        if dirn[i - 1] == 1: dirn[i] = -1 if c[i] < fl[i] else 1
        else: dirn[i] = 1 if c[i] > fu[i] else -1
        line[i] = fl[i] if dirn[i] == 1 else fu[i]
    # kaç bar önce döndü
    since = 0
    for i in range(len(c) - 1, 0, -1):
        if dirn[i] != dirn[i - 1]: break
        since += 1
    return int(dirn[-1]), float(line[-1]), since


def swings(h, l, k=3):
    """Fraktal pivotlar; son 4 pivotla yapı (HH/HL/LH/LL)."""
    piv = []
    for i in range(k, len(h) - k):
        if h[i] == h[i - k:i + k + 1].max(): piv.append(("H", i, float(h[i])))
        if l[i] == l[i - k:i + k + 1].min(): piv.append(("L", i, float(l[i])))
    piv.sort(key=lambda p: p[1])
    # ardışık aynı tipleri birleştir (H'de yüksek olan, L'de düşük olan kalsın)
    m = []
    for p in piv:
        if m and m[-1][0] == p[0]:
            if (p[0] == "H" and p[2] > m[-1][2]) or (p[0] == "L" and p[2] < m[-1][2]): m[-1] = p
        else: m.append(p)
    hs = [p for p in m if p[0] == "H"][-2:]; ls = [p for p in m if p[0] == "L"][-2:]
    lab = []
    if len(hs) == 2: lab.append("HH" if hs[1][2] > hs[0][2] else "LH")
    if len(ls) == 2: lab.append("HL" if ls[1][2] > ls[0][2] else "LL")
    return {
        "yapi": "+".join(lab) if lab else "?",
        "son_tepe": hs[-1][2] if hs else None, "son_dip": ls[-1][2] if ls else None,
        "onceki_tepe": hs[0][2] if len(hs) == 2 else None, "onceki_dip": ls[0][2] if len(ls) == 2 else None,
    }


# ───────────────────────── dilim analizi ─────────────────────────
def analiz_tf(k, etiket):
    if len(k) < 60: return {"tf": etiket, "hata": f"yetersiz bar ({len(k)})"}
    o = np.array([x["o"] for x in k]); h = np.array([x["h"] for x in k]); l = np.array([x["l"] for x in k])
    c = np.array([x["c"] for x in k]); v = np.array([x["amount"] or x["vol"] for x in k])
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    r = rsi(c); a = atr(h, l, c); ad, pdi, ndi = adx(h, l, c); st_dir, st_line, st_since = supertrend(h, l, c)
    px = float(c[-1]); atr_pct = float(a[-1] / px * 100) if not np.isnan(a[-1]) else None
    rvol = float(v[-2] / v[-22:-2].mean()) if v[-22:-2].mean() > 0 else None  # son KAPANMIŞ bar (açık bar eksik kalır)
    sw = swings(h, l)
    # EMA dizilimi puanı: -3..+3
    puan = int(px > e20[-1]) + int(e20[-1] > e50[-1]) + int(e50[-1] > e200[-1] if len(c) >= 200 else 0)
    puan -= int(px < e20[-1]) + int(e20[-1] < e50[-1]) + int(e50[-1] < e200[-1] if len(c) >= 200 else 0)
    # EMA50 eğimi (son 10 bar, ATR cinsinden)
    egim = float((e50[-1] - e50[-11]) / a[-1]) if a[-1] and not np.isnan(a[-1]) else None
    if ad is not None and not math.isnan(ad) and ad < 20: trend = "YATAY"
    elif puan >= 2 and st_dir == 1: trend = "YUKARI"
    elif puan <= -2 and st_dir == -1: trend = "AŞAĞI"
    else: trend = "KARIŞIK"
    n = len(c)
    return {
        "tf": etiket, "fiyat": px, "trend": trend, "ema_puan": puan,
        "ema20": round(float(e20[-1]), 8), "ema50": round(float(e50[-1]), 8), "ema200": round(float(e200[-1]), 8) if n >= 200 else None,
        "ema50_egim_atr": round(egim, 2) if egim is not None else None,
        "rsi14": round(float(r[-1]), 1) if not np.isnan(r[-1]) else None,
        "atr14": round(float(a[-1]), 8) if not np.isnan(a[-1]) else None, "atr_pct": round(atr_pct, 2) if atr_pct else None,
        "adx14": round(ad, 1) if ad == ad else None, "di_plus": round(pdi, 1) if pdi == pdi else None, "di_minus": round(ndi, 1) if ndi == ndi else None,
        "supertrend": {"yon": "YUKARI" if st_dir == 1 else "AŞAĞI", "seviye": round(st_line, 8) if st_line == st_line else None, "kac_bar_once_dondu": st_since},
        "rvol_son_kapanan_bar": round(rvol, 2) if rvol else None,
        "en_yuksek_20": float(h[-20:].max()), "en_dusuk_20": float(l[-20:].min()),
        "en_yuksek_50": float(h[-50:].max()), "en_dusuk_50": float(l[-50:].min()),
        "swing": sw,
        "degisim_pct": {"son_bar": round(float((c[-1] / c[-2] - 1) * 100), 2), "son_10": round(float((c[-1] / c[-11] - 1) * 100), 2), "son_50": round(float((c[-1] / c[-51] - 1) * 100), 2)},
        "son_5_mum": [{"t": x["t"], "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"]} for x in k[-5:]],
    }


def coin_sym(s):
    s = s.upper().replace("/", "").replace("-", "").replace("_", "")
    if s.endswith("USDT"): s = s[:-4]
    return s + "_USDT"


def analiz(coin, haber_yenile=False):
    sym = coin_sym(coin); out = {"coin": sym, "zaman_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "dilimler": {}, "btc": {}, "hatalar": []}
    for et, iv, mins, bars in TFS:
        k = mexc.klines(sym, minutes=mins * bars, interval=iv)
        if not k: out["hatalar"].append(f"{et}: mum yok"); continue
        out["dilimler"][et] = analiz_tf(k, et)
    if not out["dilimler"]:
        out["hatalar"].append("MEXC'te bu sembol yok veya veri gelmedi"); return out
    for et, iv, mins, bars in [TFS[2], TFS[3]]:  # BTC 1h + 4h
        k = mexc.klines("BTC_USDT", minutes=mins * bars, interval=iv)
        if k:
            a = analiz_tf(k, et); out["btc"][et] = {"trend": a["trend"], "ema_puan": a["ema_puan"], "rsi14": a["rsi14"], "degisim_pct": a["degisim_pct"], "supertrend": a["supertrend"]["yon"]}
    fd = mexc.funding_detail(sym)
    if fd:
        out["funding"] = {"oran_pct": round(fd["fundingRate"] * 100, 4), "dongu_saat": fd["collectCycle"],
                          "sonraki_odeme_dk": max(0, int((fd["nextSettleTime"] / 1000 - time.time()) / 60)),
                          "yorum": "long'lar ödüyor (kalabalık long)" if fd["fundingRate"] > 0.0001 else ("short'lar ödüyor (kalabalık short)" if fd["fundingRate"] < -0.0001 else "nötr")}
    oi = mexc.current_oi_usd(sym)
    if oi: out["oi_usd"] = round(oi)
    d1 = out["dilimler"].get("1d")
    if d1: out["degisim_24s_pct"] = d1["degisim_pct"]["son_bar"]; out["degisim_7g_pct"] = round((d1["son_5_mum"][-1]["c"] / out["dilimler"]["1d"]["son_5_mum"][0]["o"] - 1) * 100, 2)
    # hızlı seviye önerisi (ajan düzeltir): 1h ATR tabanlı
    h1 = out["dilimler"].get("1h")
    if h1 and h1.get("atr14"):
        px = h1["fiyat"]; a = h1["atr14"]
        out["seviye_taslak"] = {"atr_1h": a, "stop_1atr_long": round(px - 1.2 * a, 8), "stop_1atr_short": round(px + 1.2 * a, 8),
                                "yakin_direnc": h1["en_yuksek_20"], "yakin_destek": h1["en_dusuk_20"],
                                "4h_direnc": out["dilimler"].get("4h", {}).get("en_yuksek_50"), "4h_destek": out["dilimler"].get("4h", {}).get("en_dusuk_50")}
    # borsa duyuruları
    try:
        import news_feed
        base = sym.replace("_", "")
        items = news_feed.recent_for(base, hours=72, refresh=haber_yenile)
        out["duyurular"] = [{"ts": news_feed._fmt_ts(i["ts"]), "kaynak": i.get("source"), "tur": i.get("kind"), "baslik": i.get("title"), "url": i.get("url")} for i in items[:10]]
        if news_feed.LAST_ERRORS: out["hatalar"] += news_feed.LAST_ERRORS[:3]
    except Exception as e:
        out["hatalar"].append(f"duyuru: {e}")
    # özet oy
    oylar = {et: d.get("trend") for et, d in out["dilimler"].items()}
    out["trend_oylari"] = oylar
    yukari = sum(1 for v in oylar.values() if v == "YUKARI"); asagi = sum(1 for v in oylar.values() if v == "AŞAĞI")
    out["trend_ozet"] = "ÇOĞUNLUK YUKARI" if yukari >= 3 else ("ÇOĞUNLUK AŞAĞI" if asagi >= 3 else "KARIŞIK/YATAY")
    return out


def yazdir(o):
    print(f"== {o['coin']}  {o['zaman_utc']} UTC  | 24s {o.get('degisim_24s_pct')}%  7g {o.get('degisim_7g_pct')}%  | OI ${o.get('oi_usd', 0):,}")
    if o.get("funding"): f = o["funding"]; print(f"funding {f['oran_pct']}% / {f['dongu_saat']}s, sonraki {f['sonraki_odeme_dk']} dk — {f['yorum']}")
    print(f"TREND ÖZET: {o.get('trend_ozet')}   oylar: {o.get('trend_oylari')}")
    for et, d in o["dilimler"].items():
        if "hata" in d: print(f"  {et}: {d['hata']}"); continue
        print(f"  {et:>3} {d['trend']:<8} EMA{d['ema_puan']:+d} RSI {d['rsi14']} ADX {d['adx14']} ST {d['supertrend']['yon']}({d['supertrend']['kac_bar_once_dondu']}b) "
              f"ATR {d['atr_pct']}% RVOL {d['rvol_son_kapanan_bar']} yapı {d['swing']['yapi']}  chg10 {d['degisim_pct']['son_10']}%")
    if o.get("btc"): print("  BTC:", {k: v["trend"] for k, v in o["btc"].items()})
    if o.get("seviye_taslak"): print("  seviye taslağı:", o["seviye_taslak"])
    for d in o.get("duyurular", []): print(f"  📰 {d['ts']} [{d['kaynak']}/{d['tur']}] {d['baslik']}")
    for h in o.get("hatalar", []): print("  ⚠", h)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args: print(__doc__); sys.exit(1)
    o = analiz(args[0], haber_yenile="--haber-yenile" in sys.argv)
    if "--json" in sys.argv: print(json.dumps(o, ensure_ascii=False, indent=1))
    else: yazdir(o)
