"""
Yön kararı (Aşama 3) + işlem planı.
Kalabalığın konumu ile fiyatın yönü çelişiyorsa işleme gir, fiyatın yönünde.
Funding kesiminden SONRA çalıştırılır.
"""
import mexc
import config as C


def _atr_pct(k, window=15):
    """Son `window` mumun ortalama (high-low) / son fiyat."""
    if len(k) < window + 1:
        return None
    trs = [k[i]["h"] - k[i]["l"] for i in range(len(k) - window, len(k))]
    atr = sum(trs) / window
    last = k[-1]["c"]
    return atr / last if last > 0 else None


def decide(cand):
    """
    Aday için yön + kurulum döner, ya da None (tradeable değilse).
    Dört çeyrekten sadece 'kalabalık yanılıyor' olan ikisi işlem üretir.
    """
    k = mexc.klines(cand["symbol"], minutes=600, interval="Min1")
    if len(k) < 500:
        return None
    c = [x["c"] for x in k]
    ret_8h = c[-1] / c[-480] - 1
    ret_1h = c[-1] / c[-60] - 1
    atr_pct = _atr_pct(k, 15)
    if not atr_pct or atr_pct <= 0:
        return None

    side = None
    setup = None
    # SHORT kalabalık (funding negatif) + fiyat yukarı -> shortlar sıkışmış -> LONG
    if cand["crowd"] == "SHORT" and ret_8h > C.SQUEEZE_RET8H_MULT * atr_pct:
        side, setup = "LONG", "short_squeeze"
    # LONG kalabalık (funding pozitif) + parabolik + momentum kırılmış -> SHORT
    elif (cand["crowd"] == "LONG"
          and ret_8h > C.FLUSH_RET8H_MULT * atr_pct
          and ret_1h < 0):
        side, setup = "SHORT", "long_flush"

    if side is None:
        return None

    return {
        "symbol": cand["symbol"],
        "side": side,
        "setup": setup,
        "ret_8h": round(ret_8h, 4),
        "ret_1h": round(ret_1h, 4),
        "atr_pct": round(atr_pct, 4),
        "last": c[-1],
    }


def position_plan(decision, ref_high, ref_low):
    """
    Referans mum verildikten sonra somut giriş/stop/hedef/boyut planı üretir.
    ref_high / ref_low = T+0..T+5 referans mumunun yüksek/düşük değeri.
    """
    side = decision["side"]
    atr = decision["atr_pct"] * decision["last"]
    stop_pad = C.STOP_ATR_MULT * atr

    if side == "LONG":
        entry = ref_high                       # kırılım + geri çekiliş
        stop = ref_low - stop_pad
    else:
        entry = ref_low
        stop = ref_high + stop_pad

    stop_dist = abs(entry - stop)
    stop_pct = stop_dist / entry

    # Kâr hedefleri
    if side == "LONG":
        tp1 = entry + stop_dist                 # 1R
        tp2 = entry + C.TP_ATR_MULT * atr       # 2*ATR
    else:
        tp1 = entry - stop_dist
        tp2 = entry - C.TP_ATR_MULT * atr

    # ── Pozisyon boyutu: risk tabanlı ──
    risk_usd = C.BALANCE_USDT * C.RISK_PER_TRADE
    notional = risk_usd / stop_pct if stop_pct > 0 else 0.0
    # Kaldıraç: notional'ı MAX_LEVERAGE ile sınırla, gereken marjini hesapla
    lev = min(C.MAX_LEVERAGE, max(1.0, notional / (C.BALANCE_USDT * 0.5)))
    # Stop mesafesine göre "güvenli" kaldıraç önerisi (likidasyondan uzak)
    safe_lev = min(C.MAX_LEVERAGE, 0.5 / stop_pct) if stop_pct > 0 else 1.0
    margin = notional / safe_lev if safe_lev > 0 else notional

    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "stop_pct": stop_pct,
        "tp1": tp1,
        "tp2": tp2,
        "risk_usd": risk_usd,
        "notional_usd": notional,
        "suggested_leverage": round(safe_lev, 1),
        "margin_usd": margin,
        "qty_coin": notional / entry if entry > 0 else 0.0,
    }
