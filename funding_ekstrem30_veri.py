#!/usr/bin/env python3
"""Son 30 gun: Bybit linear USDT perp evreninde asiri dusuk / asiri yuksek funding
olaylari + sonrasindaki fiyat hareketi (1h klines)."""
import urllib.request, json, time, sys, datetime as dt, pickle, os
from concurrent.futures import ThreadPoolExecutor, as_completed
BASE="https://api.bybit.com"; DAYS=30; MIN_TURN=1_000_000; WORKERS=12
def get(path, params, retries=5):
    q="&".join(f"{k}={v}" for k,v in params.items())
    for i in range(retries):
        try:
            req=urllib.request.Request(f"{BASE}{path}?{q}",headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception as e:
            if i==retries-1: raise
            time.sleep(1.5*(i+1))
tk=get("/v5/market/tickers",{"category":"linear"})["result"]["list"]
syms=[];tick={}
for t in tk:
    s=t["symbol"]
    if not s.endswith("USDT"): continue
    try: turn=float(t.get("turnover24h") or 0)
    except: continue
    if turn>=MIN_TURN: syms.append(s); tick[s]=t
print(f"{len(syms)} coin",file=sys.stderr)
now_ms=int(time.time()*1000); start_ms=now_ms-DAYS*86400_000
def fetch_f(sym):
    out=[]; end=now_ms
    for _ in range(4):
        try: r=get("/v5/market/funding/history",{"category":"linear","symbol":sym,"startTime":start_ms,"endTime":end,"limit":200})
        except: break
        lst=r["result"]["list"]
        if not lst: break
        for rec in lst: out.append((int(rec["fundingRateTimestamp"]),float(rec["fundingRate"])))
        if len(lst)<200: break
        end=min(int(x["fundingRateTimestamp"]) for x in lst)-1
    return sym,sorted(set(out))
def fetch_k(sym):
    # 1h klines son 31 gun (744 mum) -> 2 sayfa
    out={}; end=now_ms
    for _ in range(2):
        try: r=get("/v5/market/kline",{"category":"linear","symbol":sym,"interval":"60","start":start_ms-86400_000,"end":end,"limit":1000})
        except: break
        lst=r["result"]["list"]
        if not lst: break
        for k in lst: out[int(k[0])]=(float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[6]))
        if len(lst)<1000: break
        end=min(int(k[0]) for k in lst)-1
    return sym,out
fund={};kl={}
with ThreadPoolExecutor(WORKERS) as ex:
    for i,f in enumerate(as_completed([ex.submit(fetch_f,s) for s in syms])):
        s,o=f.result(); fund[s]=o
        if i%100==0: print("f",i,file=sys.stderr)
with ThreadPoolExecutor(WORKERS) as ex:
    for i,f in enumerate(as_completed([ex.submit(fetch_k,s) for s in syms])):
        s,o=f.result(); kl[s]=o
        if i%100==0: print("k",i,file=sys.stderr)
pickle.dump({"fund":fund,"kl":kl,"tick":tick,"now":now_ms},open("fund30.pkl","wb"))
print("kaydedildi",file=sys.stderr)
