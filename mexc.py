"""
MEXC Futures public API istemcisi.
Sadece OKUMA yapar — hiçbir emir göndermez, API anahtarı gerektirmez.
"""
import time
import math
import threading
import requests

BASE = "https://contract.mexc.com"

# Her thread'e ayrı session (paralel taramada güvenli olması için)
_local = threading.local()


def _sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": "funding-trader/1.0"})
        _local.s = s
    return s


_S = _sess()


_CSIZE = {}   # symbol -> contractSize (holdVol'ü coin'e çevirmek için)


def contract_sizes():
    """Tüm kontratların contractSize'ını tek çağrıda çeker ve cache'ler.
    holdVol *kontrat* birimindedir; coin = holdVol * contractSize."""
    global _CSIZE
    if _CSIZE:
        return _CSIZE
    d = get("/api/v1/contract/detail") or []
    for x in d:
        try:
            _CSIZE[x["symbol"]] = float(x["contractSize"])
        except (KeyError, TypeError, ValueError):
            continue
    return _CSIZE


def get(path, retries=4, **params):
    """Tek GET isteği, başarısızlıkta artan beklemeyle tekrar dener."""
    for i in range(retries):
        try:
            r = _sess().get(BASE + path, params=params, timeout=12)
            j = r.json()
            if j.get("success"):
                return j.get("data")
        except Exception:
            pass
        time.sleep(0.4 * (i + 1))
    return None


# ─── ANLIK GÖRÜNTÜ (tüm pariteler, tek çağrı) ───────────────────
def snapshot():
    """
    Tüm USDT perp'leri için tek çağrıda anlık veri döner.
    Her satır bir dict: türetilmiş oi_usd / spread / basis alanları eklenir.
    """
    data = get("/api/v1/contract/ticker") or []
    csize = contract_sizes()
    now_ms = int(time.time() * 1000)
    out = []
    for r in data:
        sym = r.get("symbol", "")
        if not sym.endswith("_USDT"):
            continue
        try:
            fair = float(r["fairPrice"]); idx = float(r["indexPrice"])
            bid = float(r["bid1"]); ask = float(r["ask1"])
            hold = float(r["holdVol"]) * csize.get(sym, 1.0)  # kontrat -> coin
            row = {
                "ts_ms": now_ms,
                "symbol": sym,
                "lastPrice": float(r["lastPrice"]),
                "fairPrice": fair,
                "indexPrice": idx,
                "fundingRate": float(r["fundingRate"]),
                "holdVol": hold,
                "amount24": float(r["amount24"]),
                "bid1": bid,
                "ask1": ask,
                "oi_usd": hold * fair,
                "spread": (ask - bid) / bid if bid > 0 else 9.9,
                "basis": (fair - idx) / idx if idx > 0 else 0.0,
            }
            out.append(row)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    return out


# ─── FUNDING DETAY (collectCycle + bir sonraki ödeme zamanı) ────
def funding_detail(symbol):
    d = get(f"/api/v1/contract/funding_rate/{symbol}")
    if not d:
        return None
    return {
        "fundingRate": float(d.get("fundingRate", 0.0)),
        "collectCycle": int(d.get("collectCycle", 8) or 8),
        "nextSettleTime": int(d.get("nextSettleTime", 0) or 0),
        "maxFundingRate": float(d.get("maxFundingRate", 0.0) or 0.0),
        "minFundingRate": float(d.get("minFundingRate", 0.0) or 0.0),
    }


# ─── FUNDING GEÇMİŞİ + İSTATİSTİK (kendi geçmişine göre z-skor) ─
def funding_stats(symbol, pages=6):
    """
    Coinin funding geçmişini çeker, her kaydı 8 saate normalize eder,
    ortalama/std/collectCycle döner. Yetersiz veri varsa None.
    """
    rows = []
    total_page = 1
    for p in range(1, pages + 1):
        d = get("/api/v1/contract/funding_rate/history",
                symbol=symbol, page_num=p, page_size=100)
        if not d or not d.get("resultList"):
            break
        rows += d["resultList"]
        total_page = d.get("totalPage", 1)
        time.sleep(0.08)
        if p >= total_page:
            break
    if len(rows) < 5:
        return None

    # Her kaydı kendi collectCycle'ına göre 8s'e normalize et
    norm = []
    last_cycle = 8
    for r in rows:
        try:
            cyc = int(r.get("collectCycle", 8) or 8)
            last_cycle = cyc
            norm.append(float(r["fundingRate"]) * (8.0 / cyc))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    if len(norm) < 5:
        return None

    n = len(norm)
    mean = sum(norm) / n
    var = sum((x - mean) ** 2 for x in norm) / n
    std = math.sqrt(var)
    return {
        "mean_norm": mean,
        "std_norm": std + 1e-9,
        "n": n,
        "cycle": last_cycle,   # en güncel kaydın döngüsü (funding_detail'e gerek kalmaz)
    }


# ─── KLINE (yön kararı için) ────────────────────────────────────
def klines(symbol, minutes=600, interval="Min1"):
    """
    Son `minutes` dakikalık mumları döner: list of dicts (t,o,h,l,c,vol,amount).
    Eski->yeni sıralı.
    """
    end = int(time.time())
    start = end - minutes * 60
    d = get(f"/api/v1/contract/kline/{symbol}",
            interval=interval, start=start, end=end)
    if not d or not d.get("time"):
        return []
    t = d["time"]; o = d["open"]; h = d["high"]
    lo = d["low"]; c = d["close"]; v = d.get("vol", [0] * len(t))
    am = d.get("amount", [0] * len(t))
    out = []
    for i in range(len(t)):
        out.append({
            "t": int(t[i]), "o": float(o[i]), "h": float(h[i]),
            "l": float(lo[i]), "c": float(c[i]),
            "vol": float(v[i]), "amount": float(am[i]),
        })
    return out


def current_oi_usd(symbol):
    """Tek sembol için anlık açık pozisyon (USD). Çıkışta OI izlemek için."""
    d = get("/api/v1/contract/ticker", symbol=symbol)
    if not d:
        return None
    try:
        cs = contract_sizes().get(symbol, 1.0)
        return float(d["holdVol"]) * cs * float(d["fairPrice"])
    except (KeyError, TypeError, ValueError):
        return None
