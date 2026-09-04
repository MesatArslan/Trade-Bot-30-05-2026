#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Her dakika tüm Bybit USDT perp'lerin anlık funding/fiyat/OI'sini kaydeder.
Çıktı: data/funding_dakika/funding_dakika_YYYYMMDD.jsonl  (satır = 1 dakika, {ts, coins:{SYM:[funding, last, mark, index, oiUsd, turnover24h, nextFundingTime]}})
Durdur: kill $(pgrep -f funding_dakika_kayit.py)"""
import json, time, os, datetime as dt, urllib.request
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "funding_dakika"); os.makedirs(OUT, exist_ok=True)
MIN_TURN = 500_000  # 24s hacmi $500k altındaki coinleri atla
def get():
    r = urllib.request.urlopen(urllib.request.Request("https://api.bybit.com/v5/market/tickers?category=linear", headers={"User-Agent": "curl/8"}), timeout=20)
    return json.load(r)["result"]["list"]
while True:
    t0 = time.time()
    try:
        rows = {}
        for t in get():
            s = t["symbol"]
            if not s.endswith("USDT") or float(t.get("turnover24h") or 0) < MIN_TURN: continue
            rows[s] = [float(t["fundingRate"] or 0), float(t["lastPrice"]), float(t["markPrice"]), float(t["indexPrice"]), round(float(t["openInterestValue"] or 0)), round(float(t["turnover24h"])), int(t["nextFundingTime"] or 0), int(t.get("fundingIntervalHour") or 8)]
        ts = int(t0*1000); day = dt.datetime.fromtimestamp(t0, dt.UTC).strftime("%Y%m%d")
        with open(os.path.join(OUT, f"funding_dakika_{day}.jsonl"), "a") as f:
            f.write(json.dumps({"ts": ts, "coins": rows}, separators=(",", ":")) + "\n")
    except Exception as e:
        with open(os.path.join(OUT, "hata.log"), "a") as f: f.write(f"{dt.datetime.now()} {e}\n")
    time.sleep(max(1, 60 - (time.time() - t0)))
