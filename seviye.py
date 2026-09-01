#!/usr/bin/env python3
"""KISA VADELI SEVIYE ANALIZI (Bybit) — destek/direnc + yon onerisi.
   Kullanim: python3 seviye.py TUTUSDT   (veya sadece TUT)"""
import urllib.request, json, time, sys, statistics as st
BASE="https://api.bybit.com"
def get(path,params):
    q="&".join(f"{k}={v}" for k,v in params.items())
    req=urllib.request.Request(f"{BASE}{path}?{q}",headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)

def kl(sym,interval,limit):
    d=get("/v5/market/kline",{"category":"linear","symbol":sym,"interval":interval,"limit":limit})
    return sorted(d["result"]["list"],key=lambda x:int(x[0]))  # [ts,o,h,l,c,vol,turn]

sym=sys.argv[1].upper() if len(sys.argv)>1 else "TUTUSDT"
if not sym.endswith("USDT"): sym+="USDT"

# Ticker (fiyat, funding, 24s)
tk=get("/v5/market/tickers",{"category":"linear","symbol":sym})["result"]["list"][0]
price=float(tk["lastPrice"]); ch=float(tk["price24hPcnt"])*100
fr=float(tk.get("fundingRate") or 0); nft=int(tk.get("nextFundingTime") or 0)
now=int(get("/v5/market/time",{})["time"])

k15=kl(sym,"15",192)   # ~2 gun
k5 =kl(sym,"5",144)    # ~12 saat
highs=[float(k[2]) for k in k15]; lows=[float(k[3]) for k in k15]; closes=[float(k[4]) for k in k15]

# ATR(14) 15m
trs=[]
for i in range(1,len(k15)):
    h=float(k15[i][2]); l=float(k15[i][3]); pc=float(k15[i][4-1] if False else k15[i-1][4])
    trs.append(max(h-l, abs(h-pc), abs(l-pc)))
atr=st.mean(trs[-14:]) if len(trs)>=14 else st.mean(trs)

# Swing high/low (fractal, pencere=3) -> seviye kumeleme
def swings(data, mode):
    w=3; out=[]
    for i in range(w,len(data)-w):
        seg=data[i-w:i+w+1]; v=data[i]
        if mode=="high" and v==max(seg): out.append(v)
        if mode=="low"  and v==min(seg): out.append(v)
    return out
sh=swings(highs,"high"); sl=swings(lows,"low")

def cluster(levels, tol):  # yakin seviyeleri birlestir
    levels=sorted(levels); res=[]
    for v in levels:
        if res and abs(v-res[-1][0])/res[-1][0]<=tol:
            res[-1][1].append(v); res[-1][0]=st.mean(res[-1][1])
        else: res.append([v,[v]])
    return [(round(m,8),len(g)) for m,g in res]  # (seviye, kac_kez_test)
tol=max(atr/price*0.5, 0.0015)
res_levels=[(lvl,n) for lvl,n in cluster(sh,tol) if lvl>price]      # direncler (ustte)
sup_levels=[(lvl,n) for lvl,n in cluster(sl,tol) if lvl<price]      # destekler (altta)
res_levels.sort(key=lambda x:x[0]); sup_levels.sort(key=lambda x:-x[0])

# Gunluk pivot (son 24s H/L/C)
day=[k for k in k15 if int(k[0])>=now-24*3600*1000]
H=max(float(k[2]) for k in day); L=min(float(k[3]) for k in day); C=closes[-1]
PP=(H+L+C)/3; R1=2*PP-L; S1=2*PP-H; R2=PP+(H-L); S2=PP-(H-L)

# Konum: 24s aralikta neredeyiz
pos=(price-L)/(H-L)*100 if H>L else 50

def f(x): return f"{x:.6g}"
sec=(nft/1000-now)
kalan=f"{int(sec//3600)}s{int(sec%3600//60)}dk" if sec>0 else "-"
print(f"\n══════ {sym}  —  {time.strftime('%H:%M',time.gmtime(now/1000+3*3600))} TR ══════")
print(f"Fiyat: {f(price)}   24s: {ch:+.1f}%   Funding: {fr*100:+.4f}% (sonraki {kalan})")
print(f"24s aralik: {f(L)} — {f(H)}   |  konum: %{pos:.0f} (0=dip,100=tepe)   ATR15m: {f(atr)} (~%{atr/price*100:.2f})")

print("\n🔺 DIRENCLER (uste):")
for lvl,n in res_levels[:4]:
    print(f"   {f(lvl)}   (+%{(lvl-price)/price*100:.2f})  test:{n}")
print(f"   pivot R1 {f(R1)} (+%{(R1-price)/price*100:.2f}) | R2 {f(R2)} (+%{(R2-price)/price*100:.2f})")
print("\n🔻 DESTEKLER (altta):")
for lvl,n in sup_levels[:4]:
    print(f"   {f(lvl)}   (-%{(price-lvl)/price*100:.2f})  test:{n}")
print(f"   pivot S1 {f(S1)} (-%{(price-S1)/price*100:.2f}) | S2 {f(S2)} (-%{(price-S2)/price*100:.2f})")

# Yon egilimi (funding + konum + trend)
ema=lambda arr,p: (lambda k: [arr[0]] and __import__('functools').reduce(lambda a,x:a+[a[-1]*(1-k)+x*k],arr[1:],[arr[0]]))(2/(p+1))
e20=ema(closes,20)[-1]; e50=ema(closes,50)[-1]
trend="yukari" if e20>e50 else "asagi"
bias=[]
if fr<=-0.004: bias.append("Asiri NEGATIF funding → short-squeeze LONG egilimi (dip teyidiyle)")
elif fr>=0.004: bias.append("Asiri POZITIF funding → longlar kalabalik, SHORT egilimi (tepe zayifliginda)")
bias.append(f"Trend (EMA20/50 15m): {trend}")
if pos<25: bias.append("Fiyat 24s dibine yakin → destekten LONG tepki/short-squeeze riski yukari")
elif pos>75: bias.append("Fiyat 24s tepesine yakin → direncte SHORT tepki riski")
print("\n🧭 YON EGILIMI:")
for b in bias: print("   • "+b)
n1=sup_levels[0][0] if sup_levels else L; n2=res_levels[0][0] if res_levels else H
print(f"\n📌 PLAN: en yakin destek {f(n1)} / direnc {f(n2)}. "
      f"Kirilim yonunde gir, stop ~1xATR ({f(atr)}). Bu analiz, sinyal degil.")
