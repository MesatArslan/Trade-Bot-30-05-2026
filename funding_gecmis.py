#!/usr/bin/env python3
"""FUNDING GECMIS ANALIZ (Bybit) — son N gunde her settlement saatinde
   (TR 03:00 / 11:00 / 19:00 = UTC 00:00 / 08:00 / 16:00) en negatif ve en pozitif
   funding'li coinleri listeler.

   Kullanim:
       python3 funding_gecmis.py            # son 30 gun, her slot top-1
       python3 funding_gecmis.py 14 3       # son 14 gun, her slot top-3
"""
import urllib.request, json, time, sys, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://api.bybit.com"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
TOPN = int(sys.argv[2]) if len(sys.argv) > 2 else 1
MIN_TURN = 5_000_000          # $5M+ 24s hacim (olu coinleri ele)
SLOT_HOURS = {0, 8, 16}       # UTC settlement saatleri = TR 03/11/19
WORKERS = 16

def get(path, params, retries=4):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    for i in range(retries):
        try:
            req = urllib.request.Request(f"{BASE}{path}?{q}", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1: raise
            time.sleep(1.2 * (i + 1))

# 1) Evren: likit USDT perp'ler
print(f"Evren cekiliyor (Bybit linear, hacim >= ${MIN_TURN/1e6:.0f}M)...", file=sys.stderr)
d = get("/v5/market/tickers", {"category": "linear"})
symbols = []
for t in d["result"]["list"]:
    s = t["symbol"]
    if not s.endswith("USDT"): continue
    try:
        turn = float(t.get("turnover24h") or 0)
    except Exception:
        continue
    if turn >= MIN_TURN:
        symbols.append(s)
print(f"{len(symbols)} coin taranacak, {DAYS} gun funding gecmisi cekiliyor...", file=sys.stderr)

now_ms = int(time.time() * 1000)
start_ms = now_ms - DAYS * 86400_000

def fetch(sym):
    """Bir coin icin son DAYS gunun funding gecmisi -> [(ts_ms, rate), ...]"""
    try:
        r = get("/v5/market/funding/history",
                {"category": "linear", "symbol": sym, "startTime": start_ms,
                 "endTime": now_ms, "limit": 200})
        out = []
        for rec in r["result"]["list"]:
            ts = int(rec["fundingRateTimestamp"])
            fr = float(rec["fundingRate"])
            out.append((ts, fr))
        return sym, out
    except Exception:
        return sym, []

# 2) Paralel cek, settlement anina gore grupla
# slots[ts_ms] = [(rate, sym), ...]
slots = {}
done = 0
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = [ex.submit(fetch, s) for s in symbols]
    for f in as_completed(futs):
        sym, recs = f.result()
        done += 1
        if done % 50 == 0:
            print(f"  {done}/{len(symbols)}...", file=sys.stderr)
        for ts, fr in recs:
            d = dt.datetime.utcfromtimestamp(ts / 1000)
            if d.hour not in SLOT_HOURS:      # sadece 00/08/16 UTC
                continue
            slots.setdefault(ts, []).append((fr, sym))

if not slots:
    print("Veri bulunamadi.", file=sys.stderr); sys.exit(1)

# 3) TR saatine cevir, saat etiketle
TR = dt.timezone(dt.timedelta(hours=3))
HOUR_LABEL = {0: "03:00 TR", 8: "11:00 TR", 16: "19:00 TR"}

def tr_str(ts):
    return dt.datetime.fromtimestamp(ts / 1000, TR)

# saat -> [(ts, en_neg_liste, en_poz_liste)]
by_hour = {0: [], 8: [], 16: []}
for ts, lst in slots.items():
    utch = dt.datetime.utcfromtimestamp(ts / 1000).hour
    if utch not in by_hour: continue
    neg = sorted([x for x in lst if x[0] < 0])[:TOPN]              # en negatif
    pos = sorted([x for x in lst if x[0] > 0], reverse=True)[:TOPN]  # en pozitif
    by_hour[utch].append((ts, neg, pos, len(lst)))

def fmt(items):
    if not items: return "-"
    return "  ".join(f"{s}:{fr*100:+.4f}%" for fr, s in items)

lines = []
lines.append(f"# BYBIT FUNDING GECMISI — son {DAYS} gun  (hacim >= ${MIN_TURN/1e6:.0f}M, her slot top-{TOPN})")
lines.append(f"# Slot saatleri: TR 03:00 / 11:00 / 19:00  (= UTC 00:00 / 08:00 / 16:00)")
lines.append(f"# EN DUSUK = en negatif funding (shortlar oder -> LONG/squeeze adayi)")
lines.append(f"# EN YUKSEK = en pozitif funding (longlar oder -> SHORT adayi)")

for utch in (8, 16, 0):  # 11:00, 19:00, 03:00 sirasiyla
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"⏰  {HOUR_LABEL[utch]}  —  son {DAYS} gun")
    lines.append("=" * 78)
    lines.append(f"{'TARIH':12s} {'🔴 EN DUSUK (negatif)':34s} {'🟢 EN YUKSEK (pozitif)'}")
    for ts, neg, pos, n in sorted(by_hour[utch], reverse=True):
        gun = tr_str(ts).strftime("%Y-%m-%d")
        lines.append(f"{gun:12s} {fmt(neg):34s} {fmt(pos)}")

out = "\n".join(lines)
print(out)

# dosyaya da yaz
import os
fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "funding_gecmis_rapor.txt")
with open(fpath, "w") as fp:
    fp.write(out + "\n")
print(f"\n[Rapor kaydedildi: {fpath}]", file=sys.stderr)
