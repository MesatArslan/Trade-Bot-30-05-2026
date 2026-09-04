import urllib.request, json, time, sys, pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
BASE="https://api.bybit.com"; DAYS=180; WORKERS=10
def get(path, params, retries=6):
    q="&".join(f"{k}={v}" for k,v in params.items())
    for i in range(retries):
        try:
            req=urllib.request.Request(f"{BASE}{path}?{q}",headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==retries-1: raise
            time.sleep(2*(i+1))
D=pickle.load(open("fund30.pkl","rb")); syms=list(D["fund"]); now=D["now"]; start=now-DAYS*86400000
def fetch_f(sym):
    out={}; end=now
    for _ in range(30):
        try: r=get("/v5/market/funding/history",{"category":"linear","symbol":sym,"startTime":start,"endTime":end,"limit":200})
        except: break
        lst=r["result"]["list"]
        if not lst: break
        for rec in lst: out[int(rec["fundingRateTimestamp"])]=float(rec["fundingRate"])
        if len(lst)<200: break
        end=min(int(x["fundingRateTimestamp"]) for x in lst)-1
        if end<start: break
    return sym,sorted(out.items())
def fetch_k(sym):
    out={}; end=now
    for _ in range(8):
        try: r=get("/v5/market/kline",{"category":"linear","symbol":sym,"interval":"60","start":start-86400000,"end":end,"limit":1000})
        except: break
        lst=r["result"]["list"]
        if not lst: break
        for k in lst: out[int(k[0])]=(float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[6]))
        if len(lst)<1000: break
        end=min(int(k[0]) for k in lst)-1
        if end<start-86400000: break
    return sym,out
fund={};kl={}
with ThreadPoolExecutor(WORKERS) as ex:
    for i,f in enumerate(as_completed([ex.submit(fetch_f,s) for s in syms])):
        s,o=f.result(); fund[s]=o
        if i%50==0: print("f",i,file=sys.stderr,flush=True)
with ThreadPoolExecutor(WORKERS) as ex:
    for i,f in enumerate(as_completed([ex.submit(fetch_k,s) for s in syms])):
        s,o=f.result(); kl[s]=o
        if i%50==0: print("k",i,file=sys.stderr,flush=True)
pickle.dump({"fund":fund,"kl":kl,"now":now},open("fund180.pkl","wb"))
print("DONE",file=sys.stderr,flush=True)
