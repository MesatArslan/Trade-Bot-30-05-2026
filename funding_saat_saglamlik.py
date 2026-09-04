import pickle, datetime as dt, statistics as st, bisect
exec(open("saat.py").read().split("def f(v,w=6)")[0])  # veri + FH/NH hazir
def f(v,w=6): return f"{v:+{w}.2f}" if isinstance(v,(int,float)) else "-".rjust(w)
FEE=0.10  # gidis-donus komisyon+kayma %
print("=== Funding saati en dusuk coin: coin bazinda +1s (settlement mumu) ===")
byc={}
for r in FH:
    fr,s=r["top"][0]; m=mv(s,r["h"])
    if m and m["h1"] is not None: byc.setdefault(s,[]).append(m["h1"])
for s,v in sorted(byc.items(),key=lambda x:-len(x[1])):
    print(f"  {s[:-4]:10s} n={len(v):2d} medyan {st.median(v):+6.2f}  ort {st.mean(v):+6.2f}  kirmizi% {100*sum(1 for a in v if a<0)/len(v):3.0f}")
print("\n=== Birini-cikar (leave-one-coin-out) +1s medyan / kirmizi% ===")
allv=[(s,m) for s,v in byc.items() for m in v]
for s in list(byc)[:8]:
    v=[m for c,m in allv if c!=s]
    print(f"  {s[:-4]:10s} haric: n={len(v)} medyan {st.median(v):+6.2f} ort {st.mean(v):+6.2f} kirmizi% {100*sum(1 for a in v if a<0)/len(v):3.0f}")
print("\n=== Haftalara gore (tutarlilik) ===")
wk={}
for r in FH:
    fr,s=r["top"][0]; m=mv(s,r["h"])
    if m and m["h1"] is not None: wk.setdefault(trs(r["h"]).isocalendar()[1],[]).append(m["h1"])
for w,v in sorted(wk.items()): print(f"  hafta {w}: n={len(v):2d} medyan {st.median(v):+6.2f} ort {st.mean(v):+6.2f} kirmizi% {100*sum(1 for a in v if a<0)/len(v):3.0f}")
print("\n=== STRATEJI SIMULASYONU: settlement aninda en dusuk coini SHORT, X saat sonra kapat (komisyon %0.1 g/d) ===")
print("    1s-aralikli coinlerde tutulan sure icindeki funding odemesi de dusuluyor (short oder)")
def sim(recs,rank=0,hold=1,only=None,minrate=None):
    pnl=[]
    for r in recs:
        if len(r["top"])<=rank: continue
        fr,s=r["top"][rank]
        if only=="4s8s" and ivl[s]<4: continue
        if only=="1s" and ivl[s]!=1: continue
        if minrate is not None and fr>minrate: continue
        k=kl[s]; o=k.get(r["h"]); c=k.get(r["h"]+(hold-1)*H)
        if not o or not c: continue
        ret=-(c[3]/o[0]-1)*100 - FEE
        # tutma suresinde gerceklesen settlementlarin (r.h haric, sonrasi) funding'i: short negatif funding oderse kaybeder
        paid=sum(-rate[s][t]*100 for t in tsl[s] if r["h"]<t<=r["h"]+hold*H)
        pnl.append(ret-paid)
    if not pnl: return
    w=sum(1 for p in pnl if p>0); tot=sum(pnl)
    gp=sum(p for p in pnl if p>0); gl=-sum(p for p in pnl if p<0)
    return dict(n=len(pnl),hit=100*w/len(pnl),ort=st.mean(pnl),med=st.median(pnl),tot=tot,pf=gp/gl if gl else 99,worst=min(pnl),best=max(pnl))
def show(lab,d):
    if d: print(f"  {lab:44s} n={d['n']:3d} isabet%{d['hit']:3.0f} ort{d['ort']:+6.2f} med{d['med']:+6.2f} top{d['tot']:+7.1f} PF{d['pf']:4.2f} enkotu{d['worst']:+6.1f} eniyi{d['best']:+6.1f}")
for hold in (1,2,4,8):
    show(f"en dusuk, tut {hold}s, tum coinler",sim(FH,0,hold))
    show(f"en dusuk, tut {hold}s, sadece 4s/8s aralikli",sim(FH,0,hold,"4s8s"))
print()
for hold in (1,2,4):
    show(f"2. en dusuk, tut {hold}s, 4s/8s",sim(FH,1,hold,"4s8s"))
    show(f"3. en dusuk, tut {hold}s, 4s/8s",sim(FH,2,hold,"4s8s"))
print()
for mr in (-0.002,-0.005,-0.01):
    show(f"en dusuk, oran<= {mr*100:.1f}%, tut 1s, 4s/8s",sim(FH,0,1,"4s8s",mr))
print()
for u,lab in ((0,"03:00"),(8,"11:00"),(16,"19:00")):
    show(f"en dusuk {lab} TR, tut 1s, 4s/8s",sim([r for r in FH if r['u']==u],0,1,"4s8s"))
    show(f"en dusuk {lab} TR, tut 4s, 4s/8s",sim([r for r in FH if r['u']==u],0,4,"4s8s"))
print("\n=== Normal saatlerde ayni sey (karsilastirma): o saat en dusuk gorunen coini short, 1s ===")
show("normal saat en dusuk, tut 1s, 4s/8s",sim(NH,0,1,"4s8s"))
show("normal saat en dusuk, tut 4s, 4s/8s",sim(NH,0,4,"4s8s"))
# 4s araliklilarin kendi settlement saatleri (UTC 4,12,20 = TR 07/15/23) normal saatlere dusuyor: orada da etki var mi?
print("\n=== 4s-aralikli coinlerin ARA settlement saatleri (TR 07/15/23): o an settle olan en dusuk 4s coin ===")
mid=[]
for h in range(start,end,H):
    u=trs(h).astimezone(UTC).hour
    if u not in (4,12,20): continue
    cand=sorted([(settled(s,h),s) for s in tsl if ivl[s]==4 and settled(s,h) is not None])
    if cand: mid.append(dict(h=h,u=u,top=cand[:3],n=len(cand)))
show("ara settlement en dusuk 4s coin, tut 1s",sim(mid,0,1))
show("ara settlement en dusuk 4s coin, tut 4s",sim(mid,0,4))
v=[mv(r["top"][0][1],r["h"])["h1"] for r in mid if mv(r["top"][0][1],r["h"])]
print(f"  ara settlement +1s: n={len(v)} medyan {st.median(v):+.2f} kirmizi% {100*sum(1 for a in v if a<0)/len(v):.0f}")
