#!/usr/bin/env python3
"""FUNDING SQUEEZE TARAYICI (Bybit) — en negatif funding'li squeeze adaylarini bulur.
   Kullanim: funding saatinde (03:15 / 11:15 / 19:15 TR) calistir."""
import urllib.request, json, time
BASE="https://api.bybit.com"
def get(path,params):
    q="&".join(f"{k}={v}" for k,v in params.items())
    req=urllib.request.Request(f"{BASE}{path}?{q}",headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)

now=int(get("/v5/market/time",{})["time"])
d=get("/v5/market/tickers",{"category":"linear"})
rows=[]
for t in d["result"]["list"]:
    s=t["symbol"]
    if not s.endswith("USDT"): continue
    try:
        fr=float(t.get("fundingRate") or 0)
        price=float(t.get("lastPrice") or 0)
        ch=float(t.get("price24hPcnt") or 0)*100
        turn=float(t.get("turnover24h") or 0)
        nft=int(t.get("nextFundingTime") or 0)
    except: continue
    if turn<3_000_000: continue          # likidite filtresi ($3M+ hacim)
    rows.append((fr,s,price,ch,turn,nft))

# En negatif funding basta
neg=sorted([r for r in rows if r[0]<0], key=lambda x:x[0])[:15]
pos=sorted([r for r in rows if r[0]>0], key=lambda x:-x[0])[:8]

def cd(nft):
    if not nft: return "?"
    sec=(nft-now)//1000
    return f"{sec//3600}s {sec%3600//60}dk" if sec>0 else "gecti"

def score(fr,ch):
    # squeeze skoru: funding ne kadar negatif + coin ne kadar dipte (24s eksi)
    sc=abs(fr)*10000  # funding katkisi (bps)
    if ch<0: sc+=min(abs(ch),20)*0.5  # dipteyse bonus
    return round(sc,1)

def durum(fr,ch):
    if ch>=25:  return "PATLADI-short?"   # zaten cok yukari, squeeze bitti
    if fr<=-0.003 and ch<=5: return "TAZE-LONG*"  # cok negatif + henuz yukselmemis
    if fr<=-0.001: return "izle"
    return "-"

print(f"\n⏰ {time.strftime('%Y-%m-%d %H:%M',time.gmtime(now/1000))} UTC "
      f"({time.strftime('%H:%M',time.gmtime(now/1000+3*3600))} TR)\n")
print("🔴 EN NEGATIF FUNDING (SHORT SQUEEZE / LONG adaylari):")
print(f"{'COIN':14s}{'FUNDING%':>10s}{'24s%':>8s}{'HACIM$M':>9s}{'Fund':>9s}{'SQZ':>6s}  DURUM")
for fr,s,price,ch,turn,nft in neg:
    print(f"{s:14s}{fr*100:>9.4f}%{ch:>7.1f}%{turn/1e6:>8.1f}M{cd(nft):>9s}{score(fr,ch):>6}  {durum(fr,ch)}")
print("\n🟢 EN POZITIF FUNDING (SHORT adaylari, referans):")
for fr,s,price,ch,turn,nft in pos:
    print(f"{s:14s}{fr*100:>9.4f}%{ch:>7.1f}%{turn/1e6:>8.1f}M{cd(nft):>12s}")
print("\nSQZ = squeeze skoru (yuksek = daha guclu aday: cok negatif funding + coin dipte).")
print("DURUM: TAZE-LONG* = cok negatif funding + henuz yukselmemis (izlenecek squeeze adayi)")
print("       PATLADI-short? = zaten cok yukari firlamis, squeeze bitmis (long kovalamA, ters short adayi)")
print("NOT: Sinyal degil, izleme listesi. Giris icin dip teyidi (higher-low/reclaim) + dar stop (~%2.5) sart.")
