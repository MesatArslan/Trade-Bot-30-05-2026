import pickle, datetime as dt, statistics as st, bisect, json
D=pickle.load(open("fund30.pkl","rb")); fund=D["fund"]; kl=D["kl"]; now=D["now"]
TR=dt.timezone(dt.timedelta(hours=3)); UTC=dt.timezone.utc
H=3600000
def trs(t): return dt.datetime.fromtimestamp(t/1000,TR)
FUND_H={0,8,16}   # UTC = TR 03/11/19
# her coin: settlement ts listesi + interval
ivl={}; tsl={}; rate={}
for s,recs in fund.items():
    if len(recs)<3 or not kl.get(s): continue
    ts=[t for t,_ in recs]; tsl[s]=ts; rate[s]=dict(recs)
    d=[(ts[i+1]-ts[i])/H for i in range(len(ts)-1)]; ivl[s]=round(st.median(d))
def shown(s,h):
    """saat h'de Coinglass'ta gorunen oran ~ bir sonraki settlement'in orani"""
    ts=tsl[s]; i=bisect.bisect_left(ts,h)
    if i>=len(ts): return None
    if ts[i]-h>ivl[s]*H: return None
    return rate[s][ts[i]]
def settled(s,h):
    return rate[s].get(h)
def mv(s,h):
    k=kl[s]; b=k.get(h)
    if not b: return None
    p=b[0]; o={}
    for lab,n in (("p24",-24),("p4",-4),("p1",-1)):
        c=k.get(h+n*H); o[lab]=(p/c[0]-1)*100 if c else None
    for lab,n in (("h1",1),("h4",4),("h8",8),("h24",24)):
        c=k.get(h+(n-1)*H); o[lab]=(c[3]/p-1)*100 if c else None
    hi=lo=p
    for i in range(24):
        c=k.get(h+i*H)
        if c: hi=max(hi,c[1]); lo=min(lo,c[2])
    o["up"]=(hi/p-1)*100; o["dn"]=(lo/p-1)*100
    hi=lo=p
    for i in range(4):
        c=k.get(h+i*H)
        if c: hi=max(hi,c[1]); lo=min(lo,c[2])
    o["up4"]=(hi/p-1)*100; o["dn4"]=(lo/p-1)*100
    return o
start=(now//H)*H-30*24*H; end=(now//H)*H-24*H
FH=[]; NH=[]   # kayitlar
for h in range(start,end,H):
    u=trs(h).astimezone(UTC).hour
    if u in FUND_H:
        cand=[(settled(s,h),s) for s in tsl if settled(s,h) is not None]
    else:
        cand=[(shown(s,h),s) for s in tsl if shown(s,h) is not None]
    cand=[c for c in cand if c[0] is not None]
    if len(cand)<50: continue
    cand.sort()
    med=st.median([c[0] for c in cand])
    top=cand[:3]
    rec=dict(h=h,u=u,top=top,n=len(cand),med=med)
    (FH if u in FUND_H else NH).append(rec)
def f(v,w=6): return f"{v:+{w}.1f}" if isinstance(v,(int,float)) else "-".rjust(w)
out=[]
P=out.append
P("="*110)
P("A) FUNDING SAATLERI (TR 03:00 / 11:00 / 19:00) — o anda settle olan EN DUSUK funding'li coin (tum evren, 288 coin)")
P("   sutunlar: coin, oran (o settlement), 2. ve 3. coin | onceki24s | +1s +4s +8s +24s kapanis | 24s max yukari / max asagi")
P("="*110)
lastday=None
for r in FH:
    d=trs(r["h"]); 
    if d.date()!=lastday: P(""); lastday=d.date()
    (fr,s)=r["top"][0]; m=mv(s,r["h"]) or {}
    others="  ".join(f"{x[1][:-4]}:{x[0]*100:+.2f}" for x in r["top"][1:])
    P(f"{d.strftime('%m-%d %a %H:%M')}  {s[:-4]:10s}{fr*100:+7.2f}% [{ivl[s]}s]  {others:34s}| {f(m.get('p24'))} | {f(m.get('h1'))} {f(m.get('h4'))} {f(m.get('h8'))} {f(m.get('h24'))} | {f(m.get('up'))} {f(m.get('dn'))}")
P("")
P("="*110)
P("B) NORMAL SAATLER (diger 21 saat) — o saatte Coinglass'ta gorunen EN DUSUK oran (1s/4s coinlerde o an settle olan, 8s coinlerde bir sonraki)")
P("   gun gun, saat araligi -> coin(oran): sonrasi +4s / +24s (aralik basindan)")
P("="*110)
# gun bazinda sikistir: ardisik ayni coin -> aralik
byday={}
for r in NH: byday.setdefault(trs(r["h"]).date(),[]).append(r)
for d in sorted(byday):
    segs=[]
    for r in byday[d]:
        fr,s=r["top"][0]; hh=trs(r["h"]).hour
        if segs and segs[-1][0]==s: segs[-1][2]=hh; segs[-1][3]=min(segs[-1][3],fr)
        else: segs.append([s,hh,hh,fr,r["h"]])
    line=[]
    for s,a,b,fr,h0 in segs:
        m=mv(s,h0) or {}
        rng=f"{a:02d}" if a==b else f"{a:02d}-{b:02d}"
        line.append(f"{rng} {s[:-4]}({fr*100:+.2f}%[{ivl[s]}s]) +4s{f(m.get('h4'),5)} +24s{f(m.get('h24'),5)}")
    P(f"{d.strftime('%m-%d %a')}: " + " | ".join(line))
# ---- istatistik ----
def stats(recs, lab, key=lambda r:r["top"][0]):
    rows=[]
    for r in recs:
        fr,s=key(r); m=mv(s,r["h"])
        if m and m["h24"] is not None: rows.append((r,s,fr,m))
    if not rows: return
    P(f"\n--- {lab}  (n={len(rows)}) ---")
    for k in ("p24","p1","h1","h4","h8","h24","up4","dn4","up","dn"):
        v=[m[k] for _,_,_,m in rows if m[k] is not None]
        P(f"  {k:4s} medyan {st.median(v):+6.2f}%  ort {st.mean(v):+6.2f}%  yukari% {100*sum(1 for a in v if a>0)/len(v):3.0f}")
    return rows
P("\n"+"="*110); P("C) DAVRANIS ANALIZI"); P("="*110)
fr_rows=stats(FH,"Funding saati en dusuk coin (03/11/19)")
nh_rows=stats(NH,"Normal saat en dusuk coin (21 saat)")
# slot bazli
for u,lab in ((0,"03:00 TR"),(8,"11:00 TR"),(16,"19:00 TR")):
    stats([r for r in FH if r["u"]==u],f"Funding saati {lab}")
# 2. ve 3. coin
stats(FH,"Funding saati 2. en dusuk coin",key=lambda r:r["top"][1])
stats(FH,"Funding saati 3. en dusuk coin",key=lambda r:r["top"][2])
# oran kovasi
for lo,hi in ((-1,-0.01),(-0.01,-0.005),(-0.005,-0.002),(-0.002,0)):
    stats([r for r in FH if lo<=r["top"][0][0]<hi],f"Funding saati oran {lo*100:+.1f}%..{hi*100:+.1f}%")
# 1s interval mi
stats([r for r in FH if ivl[r["top"][0][1]]==1],"Funding saati, en dusuk coin 1s-aralikli (ONG/COTI tipi)")
stats([r for r in FH if ivl[r["top"][0][1]]>=4],"Funding saati, en dusuk coin 4s/8s-aralikli")
# once pump var mi
def pre(r):
    m=mv(r["top"][0][1],r["h"]); return m and m["p24"] is not None and m["p24"]
stats([r for r in FH if pre(r) and pre(r)>20],"Funding saati, onceki 24s > +20% (pump sonrasi)")
stats([r for r in FH if pre(r) is not None and pre(r) is not False and pre(r)<=20],"Funding saati, onceki 24s <= +20%")
# ilk kez mi / tekrar mi
seen={}; first=[]; rep=[]
for r in FH:
    s=r["top"][0][1]; (first if s not in seen else rep).append(r); seen[s]=1
stats(first,"Funding saati, coin ILK KEZ en dusuk"); stats(rep,"Funding saati, coin TEKRAR en dusuk (onceki slotlarda da vardi)")
# persistans
P("\n--- Sureklilik: en dusuk coin slotlar arasinda ne kadar ayni kaliyor ---")
same=0; tot=0
for a,b in zip(FH,FH[1:]):
    tot+=1; same+= a["top"][0][1]==b["top"][0][1]
P(f"  ardisik funding slotunda ayni coin: {same}/{tot} = %{100*same/tot:.0f}")
cnt={}
for r in FH: cnt[r["top"][0][1]]=cnt.get(r["top"][0][1],0)+1
P("  en cok 'en dusuk' olan coinler (funding saati): "+", ".join(f"{s[:-4]}×{c}" for s,c in sorted(cnt.items(),key=lambda x:-x[1])[:10]))
cnt={}
for r in NH: cnt[r["top"][0][1]]=cnt.get(r["top"][0][1],0)+1
P("  en cok 'en dusuk' olan coinler (normal saat): "+", ".join(f"{s[:-4]}×{c}" for s,c in sorted(cnt.items(),key=lambda x:-x[1])[:10]))
# evren medyan coin baz cizgisi
P("\n--- Baz cizgi: ayni saatte evrendeki MEDYAN coinin hareketi (karsilastirma) ---")
import random; random.seed(1)
base=[]
for r in FH:
    for _ in range(3):
        s=random.choice(list(tsl)); m=mv(s,r["h"])
        if m and m["h24"] is not None: base.append(m)
for k in ("h1","h4","h24","up","dn"):
    v=[m[k] for m in base if m[k] is not None]
    P(f"  {k:4s} medyan {st.median(v):+6.2f}%  ort {st.mean(v):+6.2f}%  yukari% {100*sum(1 for a in v if a>0)/len(v):3.0f}")
# settlement etrafi saatlik profil: en dusuk coin icin H-3..H+3 saatlik getiriler
P("\n--- Settlement etrafi saatlik profil (funding saati en dusuk coin): saat bazinda medyan getiri ---")
prof={}
for r in FH:
    s=r["top"][0][1]; k=kl[s]
    for off in range(-4,5):
        c=k.get(r["h"]+off*H)
        if c: prof.setdefault(off,[]).append((c[3]/c[0]-1)*100)
for off in sorted(prof):
    v=prof[off]; P(f"  saat {off:+d}: medyan {st.median(v):+5.2f}%  ort {st.mean(v):+5.2f}%  yesil% {100*sum(1 for a in v if a>0)/len(v):3.0f}  (n={len(v)})")
txt="\n".join(out); open("saat.txt","w").write(txt); print(txt)
