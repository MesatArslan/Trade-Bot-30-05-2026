#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEXC Futures ÖZEL (imzalı) API — canlı emir çalıştırıcı.

⚠️  Bu dosya GERÇEK PARA ile emir gönderebilir. Varsayılan mod DRY-RUN'dır:
    hiçbir emir gönderilmez, sadece "ne gönderilecekti" loglanır.
    Canlıya geçmek için .env içinde MEXC_DRY_RUN=0 yap.

Kimlik: .env dosyasından okunur (bu klasörde). Örnek .env.example'a bak.
    MEXC_API_KEY=...
    MEXC_API_SECRET=...
    MEXC_DRY_RUN=1

İmza (MEXC contract v1):
    signature = HMAC_SHA256(secret, accessKey + timestamp_ms + paramString)
    GET/DELETE  -> paramString = alfabetik sıralı "k=v&k=v"
    POST        -> paramString = gövdenin ham JSON metni (gönderilenle birebir)
Header'lar: ApiKey, Request-Time (ms), Signature, Content-Type: application/json
"""
import os
import time
import json
import hmac
import hashlib
import threading
import requests

import mexc  # public okuma (contract_sizes, get) — aynı contract.mexc.com host'u

BASE = "https://contract.mexc.com"

# ─────────────────────────────────────────────────────────────
# .env yükleyici (harici bağımlılık yok — basit satır ayrıştırma)
# ─────────────────────────────────────────────────────────────
def _load_env():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            # Ortamda zaten varsa dosya ezmesin
            os.environ.setdefault(k, v)


_load_env()

API_KEY    = os.environ.get("MEXC_API_KEY", "")
API_SECRET = os.environ.get("MEXC_API_SECRET", "")
# Varsayılan: DRY-RUN AÇIK. Sadece açıkça "0"/"false" ile kapanır.
DRY_RUN = os.environ.get("MEXC_DRY_RUN", "1").strip().lower() not in ("0", "false", "no")

_S = requests.Session()
_S.headers.update({"User-Agent": "funding-trader-live/1.0"})

# side kodları (MEXC): 1=long aç, 2=short kapat, 3=short aç, 4=long kapat
SIDE_OPEN  = {"LONG": 1, "SHORT": 3}
SIDE_CLOSE = {"LONG": 4, "SHORT": 2}
TYPE_MARKET = 5
TYPE_LIMIT  = 1
OPEN_ISOLATED = 1
OPEN_CROSS    = 2


class MexcError(Exception):
    pass


# ─── kontrat meta (yuvarlama için) ──────────────────────────────
_META = {}
_META_LOCK = threading.Lock()


def _meta(symbol):
    """symbol -> {contractSize, priceUnit, volUnit, minVol, maxLev} (cache)."""
    with _META_LOCK:
        if not _META:
            d = mexc.get("/api/v1/contract/detail") or []
            for x in d:
                try:
                    _META[x["symbol"]] = {
                        "cs": float(x["contractSize"]),
                        "priceUnit": float(x.get("priceUnit", 0) or 0),
                        "volUnit": float(x.get("volUnit", 1) or 1),
                        "minVol": float(x.get("minVol", 1) or 1),
                        "maxLev": int(x.get("countryConfigContractMaxLeverage",
                                            x.get("maxLeverage", 20)) or 20),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
        return _META.get(symbol)


def _round_price(symbol, px):
    m = _meta(symbol)
    unit = m["priceUnit"] if m else 0
    if unit and unit > 0:
        return round(round(px / unit) * unit, 10)
    return px


def contracts_for(symbol, margin_usdt, leverage, price):
    """USDT teminatı -> tam sayı KONTRAT adedi.
    notional = margin*leverage ; coin = notional/price ; vol = coin/contractSize.
    minVol'ün altına düşerse minVol'e yuvarlar (uyarı ile)."""
    m = _meta(symbol)
    if not m or price <= 0:
        return 0, {}
    notional = margin_usdt * leverage
    coin = notional / price
    raw = coin / m["cs"]
    step = m["volUnit"] or 1
    vol = int(round(raw / step) * step)
    bumped = False
    if vol < m["minVol"]:
        vol = int(m["minVol"]); bumped = True
    real_notional = vol * m["cs"] * price
    return vol, {
        "coin": coin, "raw_contracts": raw, "min_vol": m["minVol"],
        "bumped_to_min": bumped, "real_notional": real_notional,
        "real_margin": real_notional / leverage if leverage else 0,
        "contractSize": m["cs"],
    }


# ─── imza + istek ───────────────────────────────────────────────
def _sign(ts, param_str):
    target = API_KEY + ts + param_str
    return hmac.new(API_SECRET.encode(), target.encode(), hashlib.sha256).hexdigest()


def _private(method, path, params=None, dry_label=""):
    """İmzalı özel istek. DRY_RUN'da göndermez, ne göndereceğini döner."""
    params = params or {}
    ts = str(int(time.time() * 1000))
    if method == "POST":
        body = json.dumps(params, separators=(",", ":"))
        param_str = body
    else:
        items = sorted((k, v) for k, v in params.items() if v is not None)
        param_str = "&".join(f"{k}={v}" for k, v in items)
        body = None

    headers = {
        "ApiKey": API_KEY,
        "Request-Time": ts,
        "Signature": _sign(ts, param_str),
        "Content-Type": "application/json",
    }
    plan = {"method": method, "url": BASE + path, "params": params,
            "param_str": param_str, "label": dry_label}

    if DRY_RUN:
        return {"success": True, "dry_run": True, "plan": plan, "data": None}

    if not API_KEY or not API_SECRET:
        raise MexcError("CANLI mod ama MEXC_API_KEY/SECRET boş — .env'i doldur.")

    url = BASE + path
    for i in range(3):
        try:
            if method == "POST":
                r = _S.post(url, data=body, headers=headers, timeout=15)
            elif method == "GET":
                r = _S.get(url, params=params, headers=headers, timeout=15)
            else:  # DELETE
                r = _S.delete(url, params=params, headers=headers, timeout=15)
            j = r.json()
            if j.get("success"):
                return {"success": True, "dry_run": False, "plan": plan, "data": j.get("data")}
            # başarısız cevap — hata koduyla döndür
            return {"success": False, "dry_run": False, "plan": plan,
                    "error": j.get("code"), "message": j.get("message") or j}
        except Exception as e:
            if i == 2:
                return {"success": False, "dry_run": False, "plan": plan, "message": str(e)}
            time.sleep(0.5 * (i + 1))


# ─── kamuya açık yardımcılar (canlı fiyat / hesap) ──────────────
def mark_price(symbol):
    d = mexc.get("/api/v1/contract/ticker", symbol=symbol)
    if not d:
        return None
    try:
        return float(d["fairPrice"])
    except (KeyError, TypeError, ValueError):
        return None


def assets():
    """Cüzdan varlıkları (gerçek bakiye). SADECE OKUMA — emir göndermez, bu
    yüzden DRY-RUN'da da çalışır (anahtar/imza doğrulaması için kullanılır)."""
    if not API_KEY:
        return None
    # Okuma isteğini DRY_RUN'dan bağımsız gerçekten gönder:
    ts = str(int(time.time() * 1000))
    headers = {"ApiKey": API_KEY, "Request-Time": ts,
               "Signature": _sign(ts, ""), "Content-Type": "application/json"}
    try:
        r = _S.get(BASE + "/api/v1/private/account/assets", headers=headers, timeout=15)
        j = r.json()
        return j.get("data") if j.get("success") else {"_error": j.get("code"), "_msg": j.get("message")}
    except Exception as e:
        return {"_error": "exc", "_msg": str(e)}


def open_positions():
    """Açık pozisyonlar — SADECE OKUMA (DRY-RUN'da da çalışır)."""
    if not API_KEY:
        return None
    ts = str(int(time.time() * 1000))
    headers = {"ApiKey": API_KEY, "Request-Time": ts,
               "Signature": _sign(ts, ""), "Content-Type": "application/json"}
    try:
        r = _S.get(BASE + "/api/v1/private/position/open_positions", headers=headers, timeout=15)
        j = r.json()
        return j.get("data") if j.get("success") else {"_error": j.get("code"), "_msg": j.get("message")}
    except Exception as e:
        return {"_error": "exc", "_msg": str(e)}


# ─── kaldıraç / emir ────────────────────────────────────────────
def set_leverage(symbol, leverage, open_type=OPEN_ISOLATED):
    m = _meta(symbol)
    if m and leverage > m["maxLev"]:
        leverage = m["maxLev"]
    params = {"symbol": symbol, "leverage": int(leverage), "openType": open_type}
    return _private("POST", "/api/v1/private/position/change_leverage",
                    params, dry_label=f"set_leverage {symbol} {leverage}x")


def open_market(symbol, direction, margin_usdt, leverage,
                ref_price, stop_price=None, tp_price=None, open_type=OPEN_ISOLATED):
    """Piyasa emriyle pozisyon aç. direction: 'LONG' | 'SHORT'.
    stop_price/tp_price verilirse borsa tarafında koruyucu SL/TP olarak eklenir
    (bot ayrıca kendi TP1/TP2/breakeven yönetimini yapar)."""
    vol, info = contracts_for(symbol, margin_usdt, leverage, ref_price)
    if vol <= 0:
        return {"success": False, "message": f"{symbol}: kontrat adedi 0 (meta yok/fiyat 0)",
                "sizing": info}
    params = {
        "symbol": symbol,
        "price": _round_price(symbol, ref_price),   # market'te koruma/ref fiyat
        "vol": vol,
        "side": SIDE_OPEN[direction],
        "type": TYPE_MARKET,
        "openType": open_type,
        "leverage": int(leverage),
    }
    if stop_price is not None:
        params["stopLossPrice"] = _round_price(symbol, stop_price)
    if tp_price is not None:
        params["takeProfitPrice"] = _round_price(symbol, tp_price)
    r = _private("POST", "/api/v1/private/order/create", params,
                 dry_label=f"AÇ {direction} {symbol} vol={vol} lev={leverage}x")
    r["sizing"] = info
    r["vol"] = vol
    return r


def close_market(symbol, direction, vol, ref_price):
    """Piyasa emriyle pozisyonun bir kısmını/tamamını kapat. vol = kontrat adedi.
    direction = AÇILIŞ yönü ('LONG'/'SHORT'); kapatma side'ı otomatik seçilir."""
    vol = int(vol)
    if vol <= 0:
        return {"success": False, "message": f"{symbol}: kapatılacak vol 0"}
    params = {
        "symbol": symbol,
        "price": _round_price(symbol, ref_price),
        "vol": vol,
        "side": SIDE_CLOSE[direction],
        "type": TYPE_MARKET,
        "openType": OPEN_ISOLATED,
        "reduceOnly": True,
    }
    return _private("POST", "/api/v1/private/order/create", params,
                    dry_label=f"KAPAT {direction} {symbol} vol={vol}")


def status_line():
    mode = "🟢 CANLI" if not DRY_RUN else "🧪 DRY-RUN (emir gönderilmez)"
    keyed = "anahtar VAR" if (API_KEY and API_SECRET) else "anahtar YOK"
    return f"MEXC çalıştırıcı: {mode} | {keyed}"


if __name__ == "__main__":
    # Hızlı öz-test: imza + boyutlandırma (emir GÖNDERMEZ)
    print(status_line())
    px = mark_price("BTC_USDT")
    print("BTC_USDT fair:", px)
    if px:
        vol, info = contracts_for("BTC_USDT", margin_usdt=5, leverage=10, price=px)
        print("5 USDT teminat × 10x =>", vol, "kontrat", info)
    demo = open_market("BTC_USDT", "LONG", margin_usdt=5, leverage=10,
                       ref_price=px or 70000, stop_price=(px or 70000) * 0.99,
                       tp_price=(px or 70000) * 1.02)
    print("DRY plan:", json.dumps(demo["plan"], indent=2, default=str))
