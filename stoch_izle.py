#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stoch_izle.py — 📶 1dk StochRSI izleme listesi (server.py içinden çalışır).

Liste: Toplu Analiz sekmesinden "👁 İzle" ile eklenen coinler (data/stoch_izle/liste.json).
Döngü: her 20 sn, listedeki her coin için MEXC Min1 ve Min5 (1dk/5dk) son 120 mum → StochRSI(14,14,3,3) %K/%D.
Uyarı: %K 30 ALTINA inince ("alt") veya 70 ÜSTÜNE çıkınca ("ust") — bölge değişiminde tek sefer; 30-70 bandına
dönünce yeniden kurulur. macOS bildirimi (server._notify) + olaylar listesi (data/stoch_izle/olaylar.jsonl).
"""
import os, json, time, threading
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "data", "stoch_izle")
LISTE = os.path.join(DIR, "liste.json")
OLAYLAR = os.path.join(DIR, "olaylar.jsonl")
ALT, UST = 30.0, 70.0
PERIYOT = 20          # sn
_notify = None
_LOCK = threading.Lock()
S = {"liste": {}, "olaylar": [], "son_tur": None, "hata": None}   # liste: sym -> {eklendi, k, d, rsi, bolge, fiyat, son, son_uyari}


def init(notify_fn):
    global _notify
    _notify = notify_fn
    os.makedirs(DIR, exist_ok=True)
    try:
        with open(LISTE, encoding="utf-8") as f:
            S["liste"] = json.load(f)
    except Exception:
        S["liste"] = {}
    try:
        with open(OLAYLAR, encoding="utf-8") as f:
            S["olaylar"] = [json.loads(l) for l in f if l.strip()][-300:]
    except Exception:
        S["olaylar"] = []


def _kaydet_liste():
    try:
        tmp = LISTE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(S["liste"], f, ensure_ascii=False)
        os.replace(tmp, LISTE)
    except Exception:
        pass


def _sym(x):
    s = (x or "").upper().replace("/", "").replace("-", "").replace(" ", "")
    if s.endswith("_USDT"): return s
    if s.endswith("USDT"): s = s[:-4]
    return (s.rstrip("_") + "_USDT") if s else ""


def ekle(coin, kaynak="toplu"):
    sym = _sym(coin)
    if not sym or len(sym) > 22: return {"ok": False, "msg": "geçersiz coin"}
    with _LOCK:
        if sym in S["liste"]: return {"ok": True, "msg": "zaten listede", "sym": sym}
        S["liste"][sym] = {"eklendi": time.time(), "kaynak": kaynak, "k": None, "d": None, "rsi": None, "bolge": None, "fiyat": None, "son": None, "son_uyari": None}
        _kaydet_liste()
    threading.Thread(target=lambda: _tara_bir(sym), daemon=True).start()   # hemen ilk ölçüm
    return {"ok": True, "sym": sym}


def sil(coin):
    sym = _sym(coin)
    with _LOCK:
        if sym in S["liste"]:
            del S["liste"][sym]; _kaydet_liste()
            return {"ok": True}
    return {"ok": False, "msg": "listede yok"}


def yenile():
    """Listedeki tüm coinleri hemen yeniden ölç (döngüyü beklemeden)."""
    from concurrent.futures import ThreadPoolExecutor
    with _LOCK: syms = list(S["liste"].keys())
    def bir(sym):
        try: _tara_bir(sym)
        except Exception as ex: S["hata"] = f"{sym}: {ex}"[:200]
    with ThreadPoolExecutor(max_workers=6) as ex: list(ex.map(bir, syms))
    S["son_tur"] = time.time()
    return {"ok": True, "n": len(syms)}


def durum():
    with _LOCK:
        liste = [{"sym": k, "coin": k.replace("_USDT", ""), **v} for k, v in S["liste"].items()]
    liste.sort(key=lambda x: -(x.get("eklendi") or 0))
    return {"liste": liste, "olaylar": S["olaylar"][-200:][::-1], "tfs": list(TFS.keys()), "son_tur": S["son_tur"], "hata": S["hata"], "alt": ALT, "ust": UST, "periyot": PERIYOT}


# ───────────────────────── StochRSI ─────────────────────────
def _rsi(c, n=14):
    c = np.asarray(c, float); d = np.diff(c); up = np.where(d > 0, d, 0); dn = np.where(d < 0, -d, 0)
    if len(d) < n + 1: return np.full(len(c), np.nan)
    au = np.empty(len(d)); ad = np.empty(len(d)); au[n - 1] = up[:n].mean(); ad[n - 1] = dn[:n].mean()
    for i in range(n, len(d)):
        au[i] = (au[i - 1] * (n - 1) + up[i]) / n; ad[i] = (ad[i - 1] * (n - 1) + dn[i]) / n
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        rs = au / np.where(ad == 0, 1e-12, ad); r = 100 - 100 / (1 + rs)
    out = np.full(len(c), np.nan); out[n:] = r[n - 1:]; return out


def stoch_rsi(c, rsi_n=14, st_n=14, k_n=3, d_n=3):
    r = _rsi(c, rsi_n)
    raw = np.full(len(r), np.nan)
    for i in range(len(r)):
        w = r[i - st_n + 1:i + 1]
        if i >= st_n - 1 and not np.isnan(w).any():
            lo, hi = w.min(), w.max(); raw[i] = 50.0 if hi == lo else (r[i] - lo) / (hi - lo) * 100
    def sma(x, n):
        o = np.full(len(x), np.nan)
        for i in range(n - 1, len(x)):
            w = x[i - n + 1:i + 1]
            if not np.isnan(w).any(): o[i] = w.mean()
        return o
    k = sma(raw, k_n); d = sma(k, d_n)
    return k, d, r


def _bolge(k):
    if k is None or k != k: return None
    return "alt" if k < ALT else ("ust" if k > UST else "orta")


TFS = {"1m": ("Min1", 1), "5m": ("Min5", 5)}


def _tara_bir(sym):
    import mexc
    for tf, (iv, mins) in TFS.items():
        k = mexc.klines(sym, minutes=120 * mins, interval=iv)
        if not k or len(k) < 40:
            with _LOCK:
                if sym in S["liste"]: S["liste"][sym].setdefault(tf, {})["hata"] = "mum yok"
            continue
        c = [x["c"] for x in k]
        kk, dd, rr = stoch_rsi(c)
        kv = float(kk[-1]) if kk[-1] == kk[-1] else None
        dv = float(dd[-1]) if dd[-1] == dd[-1] else None
        rv = float(rr[-1]) if rr[-1] == rr[-1] else None
        yeni = _bolge(kv); tetik = False
        with _LOCK:
            if sym not in S["liste"]: return
            e = S["liste"][sym].setdefault(tf, {})
            eski = e.get("bolge")
            e.update({"k": round(kv, 1) if kv is not None else None, "d": round(dv, 1) if dv is not None else None,
                      "rsi": round(rv, 1) if rv is not None else None, "fiyat": c[-1], "son": time.time(), "hata": None, "mum_t": k[-1]["t"]})
            tetik = yeni in ("alt", "ust") and eski is not None and yeni != eski
            e["bolge"] = yeni
            if tetik:
                ol = {"ts": time.time(), "sym": sym, "coin": sym.replace("_USDT", ""), "tf": tf, "tur": yeni, "k": e["k"], "d": e["d"], "rsi": e["rsi"], "fiyat": c[-1]}
                S["olaylar"].append(ol); S["olaylar"] = S["olaylar"][-400:]; e["son_uyari"] = ol
                try:
                    with open(OLAYLAR, "a", encoding="utf-8") as f: f.write(json.dumps(ol, ensure_ascii=False) + "\n")
                except Exception: pass
            # geriye uyumluluk: üst seviye alanlar = 1m
            if tf == "1m":
                for kk_ in ("k", "d", "rsi", "bolge", "fiyat", "son", "son_uyari"): S["liste"][sym][kk_] = e.get(kk_)
        if tetik and _notify:
            ok_ = "📉 StochRSI 30 ALTINA indi" if yeni == "alt" else "📈 StochRSI 70 ÜSTÜNE çıktı"
            _notify(f"{sym.replace('_USDT','')} · {tf.replace('m','dk')} · {ok_}", f"%K {e['k']} · %D {e['d']} · fiyat {c[-1]}")
        time.sleep(0.1)


def loop():
    while True:
        try:
            with _LOCK: syms = list(S["liste"].keys())
            for s in syms:
                try: _tara_bir(s)
                except Exception as ex: S["hata"] = f"{s}: {ex}"[:200]
                time.sleep(0.15)
            S["son_tur"] = time.time()
        except Exception as ex:
            S["hata"] = str(ex)[:200]
        time.sleep(PERIYOT)
