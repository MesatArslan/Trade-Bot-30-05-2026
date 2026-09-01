"""
Binance USDⓈ-M funding — çapraz borsa teyidi için.
MEXC funding'i tek başına yanıltıcı olabilir (ince emir defteri, MEXC'e özel
listeler). Binance en büyük borsa; ikisi aynı yönde uç ise sinyal gerçektir.

Coinglass'ın yaptığı çok-borsalı görünümün ücretsiz, temel hali.
Sadece okuma yapar.
"""
import time
import requests

BASE = "https://fapi.binance.com"
_S = requests.Session()
_S.headers.update({"User-Agent": "funding-trader/1.0"})
_CACHE = {"ts": 0, "map": {}}


def _mexc_to_binance(sym):
    """BTC_USDT -> BTCUSDT"""
    return sym.replace("_", "").upper()


def funding_map(ttl=30):
    """
    Tüm Binance perp'leri için {BINANCE_SYMBOL: {funding, mark, index, basis}}.
    Binance funding aralığı çoğu coinde 8s (bazıları 4s) — sadece işaret ve
    kaba büyüklük teyidi için kullanıyoruz, o yüzden ham değeri veriyoruz.
    """
    now = time.time()
    if _CACHE["map"] and now - _CACHE["ts"] < ttl:
        return _CACHE["map"]
    try:
        r = _S.get(BASE + "/fapi/v1/premiumIndex", timeout=12)
        data = r.json()
    except Exception:
        return _CACHE["map"]  # başarısızsa eski cache (veya boş)
    if not isinstance(data, list):
        return _CACHE["map"]
    m = {}
    for x in data:
        try:
            mark = float(x["markPrice"]); idx = float(x["indexPrice"])
            m[x["symbol"]] = {
                "funding": float(x["lastFundingRate"]),
                "mark": mark,
                "index": idx,
                "basis": (mark - idx) / idx if idx > 0 else 0.0,
            }
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    _CACHE["map"] = m
    _CACHE["ts"] = now
    return m


def lookup(mexc_symbol):
    """MEXC sembolü için Binance funding verisi (yoksa None)."""
    return funding_map().get(_mexc_to_binance(mexc_symbol))


def cross_check(mexc_symbol, mexc_funding_norm):
    """
    Çapraz borsa teyidi döner:
      status: 'confirmed' (aynı yön, ikisi de anlamlı)
            | 'diverge'   (ters yön — dikkat)
            | 'mexc_only' (Binance'te yok)
            | 'weak'      (Binance'te var ama funding zayıf)
      binance_funding: ham Binance funding (8s varsayımı)
    """
    b = lookup(mexc_symbol)
    if not b:
        return {"status": "mexc_only", "binance_funding": None,
                "binance_basis": None}
    bf = b["funding"]
    same_sign = (bf > 0) == (mexc_funding_norm > 0)
    both_strong = abs(bf) >= 0.0005 and abs(mexc_funding_norm) >= 0.0005
    if not both_strong:
        status = "weak"
    elif same_sign:
        status = "confirmed"
    else:
        status = "diverge"
    return {"status": status, "binance_funding": round(bf, 6),
            "binance_basis": round(b["basis"], 5)}
