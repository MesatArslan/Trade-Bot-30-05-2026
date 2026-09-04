import pickle, datetime as dt, statistics as st, random
D=pickle.load(open("fund180.pkl","rb")); fund={s:v for s,v in D["fund"].items() if len(v)>10 and D["kl"].get(s)}; kl=D["kl"]
H=3600000; UTC=dt.timezone.utc; TR=dt.timezone(dt.timedelta(hours=3)); FEE=0.10
rate={s:dict(v) for s,v in fund.items()}
ivl={}
for s,v in fund.items():
    ts=[t for t,_ in v]; ivl[s]=round(st.median([(ts[i+1]-ts[i])/H for i in range(len(ts)-1)]))
print("coin:",len(fund),"funding kayit:",sum(len(v) for v in fund.values()))
allts=sorted(set(t for v in fund.values() for t,_ in v))
def build(rank=0,hold=1,excl1h=True):
    res=[]
    for t in allts:
        cand=sorted([(rate[s][t],s) for s in fund if t in rate[s]])
        if excl1h: cand=[c for c in cand if ivl[c[1]]>=4]
        if len(cand)<30 or len(cand)<=rank: continue
        fr,s=cand[rank]; k=kl[s]; o=k.get(t); c=k.get(t+(hold-1)*H)
        if not o or not c: continue
        paid=sum(-rate[s][tt]*100 for tt in rate[s] if t<tt<=t+hold*H)
        pnl=-(c[3]/o[0]-1)*100-FEE-paid
        pre=k.get(t-H); pre1=(o[0]/pre[0]-1)*100 if pre else None
        res.append(dict(t=t,s=s,fr=fr,pnl=pnl,n=len(cand),pre1=pre1,mon=dt.datetime.fromtimestamp(t/1000,UTC).strftime("%Y-%m"),hr=dt.datetime.fromtimestamp(t/1000,TR).hour))
    return res
def show(lab,rows):
    if not rows: print(f"  {lab:34s} n=0"); return
    p=[r["pnl"] for r in rows]; gp=sum(x for x in p if x>0); gl=-sum(x for x in p if x<0)
    print(f"  {lab:34s} n={len(p):4d} isabet%{100*sum(1 for x in p if x>0)/len(p):3.0f} ort{st.mean(p):+6.2f} med{st.median(p):+6.2f} top{sum(p):+7.1f} PF{(gp/gl if gl else 99):4.2f} enkotu{min(p):+6.1f}")
res=build()
print("\n=== 180 GUN, 288 coin evreni: settlement aninda EN DUSUK funding'li (4s/8s) coini SHORT, 1 saat tut, komisyon %0.1 ===")
show("TUMU",res)
mons=sorted(set(r["mon"] for r in res))
for m in mons: show(m,[r for r in res if r["mon"]==m])
print(" -- oran esigi --")
for th in (-0.001,-0.002,-0.003,-0.005,-0.01): show(f"oran<={th*100:.1f}%",[r for r in res if r["fr"]<=th])
print(" -- oran<=-0.3% ay ay --")
for m in mons: show(f"{m} <=-0.3%",[r for r in res if r["mon"]==m and r["fr"]<=-0.003])
print(" -- saat (TR) --")
for hh in sorted(set(r["hr"] for r in res)): show(f"{hh:02d}:00 TR",[r for r in res if r["hr"]==hh])
print(" -- saat (TR), oran<=-0.3% --")
for hh in sorted(set(r["hr"] for r in res)): show(f"{hh:02d}:00 TR <=-0.3%",[r for r in res if r["hr"]==hh and r["fr"]<=-0.003])
print(" -- onceki saat yesil mi (funding oncesi yukselis) --")
show("onceki 1s > +1%",[r for r in res if r["pre1"] is not None and r["pre1"]>1])
show("onceki 1s 0..+1%",[r for r in res if r["pre1"] is not None and 0<=r["pre1"]<=1])
show("onceki 1s < 0",[r for r in res if r["pre1"] is not None and r["pre1"]<0])
print(" -- tutma suresi (oran<=-0.3%) --")
for hold in (1,2,4):
    show(f"tut {hold}s",[r for r in build(0,hold) if r["fr"]<=-0.003])
print(" -- sira (oran<=-0.3%) --")
for rk in (1,2): show(f"{rk+1}. en dusuk",[r for r in build(rk,1) if r["fr"]<=-0.003])
print(" -- plasebo: ayni anda rastgele coin short --")
random.seed(3); pl=[]
for r in res:
    for _ in range(3):
        s2=random.choice(list(fund)); c=kl[s2].get(r["t"])
        if c: pl.append(dict(pnl=-(c[3]/c[0]-1)*100-FEE))
show("plasebo",pl)
print(" -- ayna: en YUKSEK funding'li coini LONG, 1s --")
resL=[]
for t in allts:
    cand=sorted([(rate[s][t],s) for s in fund if t in rate[s] and ivl[s]>=4],reverse=True)
    if len(cand)<30: continue
    fr,s=cand[0]; c=kl[s].get(t)
    if c and fr>0: resL.append(dict(pnl=(c[3]/c[0]-1)*100-FEE,fr=fr))
show("en yuksek long",resL); show("en yuksek long, oran>=+0.1%",[r for r in resL if r["fr"]>=0.001])
pickle.dump(res,open("oos288_res.pkl","wb"))
