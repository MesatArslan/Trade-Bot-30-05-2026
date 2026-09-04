import pickle, datetime as dt, statistics as st, json
D=pickle.load(open("fund30.pkl","rb")); fund=D["fund"]; kl=D["kl"]; tick=D["tick"]; now=D["now"]
TR=dt.timezone(dt.timedelta(hours=3))
def ts(t): return dt.datetime.fromtimestamp(t/1000,TR).strftime("%m-%d %H:%M")
print("coin:",len(fund),"funding kayit:",sum(len(v) for v in fund.values()),"kline coin:",sum(1 for v in kl.values() if v))
# funding araligi (saat) per coin
def interval_h(recs):
    if len(recs)<2: return None
    d=[(recs[i+1][0]-recs[i][0])/3.6e6 for i in range(len(recs)-1)]
    return st.median(d)
rows=[]
for s,recs in fund.items():
    if not recs or not kl.get(s): continue
    ih=interval_h(recs) or 8
    for t,fr in recs:
        rows.append((s,t,fr,ih))
# daglim
rates=[r[2] for r in rows]
rates.sort()
n=len(rates)
print("n=",n,"p0.5%",rates[int(n*0.005)],"p1%",rates[int(n*0.01)],"p5%",rates[int(n*0.05)],"p95",rates[int(n*0.95)],"p99",rates[int(n*0.99)],"p99.5",rates[int(n*0.995)])
def px(s,t):  # 1h kline close at hour containing t
    k=kl[s]; h=t-(t%3600000)
    return k.get(h)
def move(s,t):
    """funding anindan itibaren: +1h,+4h,+8h,+24h,+72h kapanis %; 24h icinde max yukari/asagi fitil %; onceki 24h %"""
    k=kl[s]; h=t-(t%3600000)
    base=k.get(h); 
    if not base: return None
    p0=base[0]
    out={}
    for lab,hh in (("1h",1),("4h",4),("8h",8),("24h",24),("72h",72)):
        c=k.get(h+hh*3600000)
        out[lab]=(c[3]/p0-1)*100 if c else None
    hi=lo=p0; ok=False
    for i in range(24):
        c=k.get(h+i*3600000)
        if c: hi=max(hi,c[1]); lo=min(lo,c[2]); ok=True
    out["mfe24"]=(hi/p0-1)*100 if ok else None; out["mae24"]=(lo/p0-1)*100 if ok else None
    pb=k.get(h-24*3600000)
    out["prev24"]=(p0/pb[3]-1)*100 if pb else None
    return out
# Extreme events
NEG=-0.003; POS=0.003  # per-settlement esik (%0.3)
neg=[r for r in rows if r[2]<=NEG]; pos=[r for r in rows if r[2]>=POS]
print("neg olay",len(neg),"pos olay",len(pos))
def coin_summary(evs, sign):
    byc={}
    for s,t,fr,ih in evs: byc.setdefault(s,[]).append((t,fr,ih))
    out=[]
    for s,lst in byc.items():
        lst.sort()
        ext=min(lst,key=lambda x:x[1]) if sign<0 else max(lst,key=lambda x:x[1])
        m=move(s,ext[0]) or {}
        k=kl[s]; ks=sorted(k)
        first=k[ks[0]][0]; last=k[ks[-1]][3]
        # aylik fiyat degisimi & funding gunluk toplam
        recs=fund[s]; total=sum(fr for _,fr in recs)*100
        # asiri gun sayisi
        days=len(set(dt.datetime.utcfromtimestamp(t/1000).date() for t,_,_ in lst))
        # olaylarin ortalama sonrasi
        mv=[move(s,t) for t,_,_ in lst]; mv=[x for x in mv if x]
        avg24=st.mean([x["24h"] for x in mv if x["24h"] is not None]) if mv else None
        avg4=st.mean([x["4h"] for x in mv if x["4h"] is not None]) if mv else None
        mfe=st.mean([x["mfe24"] for x in mv if x["mfe24"] is not None]) if mv else None
        mae=st.mean([x["mae24"] for x in mv if x["mae24"] is not None]) if mv else None
        out.append(dict(sym=s,n=len(lst),days=days,ext=ext[1]*100,ext_t=ts(ext[0]),ih=ext[2],
            first_t=ts(lst[0][0]),last_t=ts(lst[-1][0]),
            prev24=m.get("prev24"),h1=m.get("1h"),h4=m.get("4h"),h8=m.get("8h"),h24=m.get("24h"),h72=m.get("72h"),
            mfe24=m.get("mfe24"),mae24=m.get("mae24"),avg4=avg4,avg24=avg24,amfe=mfe,amae=mae,
            month=(last/first-1)*100,ftotal=total,turn=float(tick[s].get("turnover24h",0))/1e6,
            pc24=float(tick[s].get("price24hPcnt",0))*100, fnow=float(tick[s].get("fundingRate",0))*100))
    out.sort(key=lambda x:x["ext"]*(1 if sign>0 else -1),reverse=True)
    return out
NS=coin_summary(neg,-1); PS=coin_summary(pos,1)
json.dump({"neg":NS,"pos":PS},open("summary.json","w"),default=str)
def f(v,w=7): return f"{v:+{w}.1f}" if isinstance(v,(int,float)) else " "*(w-1)+"-"
print("\n=== EN NEGATIF FUNDING (<= -0.30%/settlement) — coin bazinda en ekstrem olay ===")
print(f"{'COIN':14s}{'ext%':>8s} {'tarih(TR)':12s}{'n':>3s}{'gun':>4s} | {'once24':>7s} {'+1h':>7s} {'+4h':>7s} {'+8h':>7s} {'+24h':>7s} {'+72h':>7s} | {'maxUp24':>8s} {'maxDn24':>8s} | {'ort+4h':>7s} {'ort+24h':>7s} | {'ay%':>7s} {'fund_top%':>9s} {'hacimM':>7s}")
for r in NS:
    print(f"{r['sym']:14s}{r['ext']:+8.3f} {r['ext_t']:12s}{r['n']:3d}{r['days']:4d} | {f(r['prev24'])} {f(r['h1'])} {f(r['h4'])} {f(r['h8'])} {f(r['h24'])} {f(r['h72'])} | {f(r['mfe24'],8)} {f(r['mae24'],8)} | {f(r['avg4'])} {f(r['avg24'])} | {f(r['month'])} {f(r['ftotal'],9)} {r['turn']:7.1f}")
print("\n=== EN POZITIF FUNDING (>= +0.30%/settlement) ===")
for r in PS:
    print(f"{r['sym']:14s}{r['ext']:+8.3f} {r['ext_t']:12s}{r['n']:3d}{r['days']:4d} | {f(r['prev24'])} {f(r['h1'])} {f(r['h4'])} {f(r['h8'])} {f(r['h24'])} {f(r['h72'])} | {f(r['mfe24'],8)} {f(r['mae24'],8)} | {f(r['avg4'])} {f(r['avg24'])} | {f(r['month'])} {f(r['ftotal'],9)} {r['turn']:7.1f}")
# Toplu istatistik
def agg(evs,lab):
    mv=[move(s,t) for s,t,_,_ in evs]; mv=[x for x in mv if x and x["24h"] is not None]
    if not mv: return
    for key in ("1h","4h","8h","24h","72h","mfe24","mae24","prev24"):
        v=[x[key] for x in mv if x[key] is not None]
        print(f"{lab} {key:6s} n={len(v):3d} med={st.median(v):+6.2f}% ort={st.mean(v):+6.2f}% pozitif%={100*sum(1 for a in v if a>0)/len(v):4.0f}")
print("\n=== TOPLU: tum negatif olaylar ===");agg(neg,"NEG")
print("=== TOPLU: tum pozitif olaylar ===");agg(pos,"POS")
# daha yumusak esik: en ekstrem 40 olay her yonde
print("\n=== TEK OLAY SIRALAMASI: en negatif 25 settlement ===")
for s,t,fr,ih in sorted(rows,key=lambda r:r[2])[:25]:
    m=move(s,t) or {}
    print(f"{s:14s}{fr*100:+8.3f} {ts(t)} int={ih:.0f}h | +4h {f(m.get('4h'))} +24h {f(m.get('24h'))} maxUp {f(m.get('mfe24'))} maxDn {f(m.get('mae24'))} once24 {f(m.get('prev24'))}")
print("\n=== TEK OLAY SIRALAMASI: en pozitif 25 settlement ===")
for s,t,fr,ih in sorted(rows,key=lambda r:-r[2])[:25]:
    m=move(s,t) or {}
    print(f"{s:14s}{fr*100:+8.3f} {ts(t)} int={ih:.0f}h | +4h {f(m.get('4h'))} +24h {f(m.get('24h'))} maxUp {f(m.get('mfe24'))} maxDn {f(m.get('mae24'))} once24 {f(m.get('prev24'))}")

# ---- gunluk toplam funding bazinda siralama (interval farkini normalize eder) ----
print("\n=== GUNLUK TOPLAM FUNDING (tum settlementlerin gunluk toplami) — en dusuk 25 coin-gun ===")
daily={}
for s,t,fr,ih in rows:
    d=dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).date()
    daily[(s,d)]=daily.get((s,d),0)+fr
dl=sorted(daily.items(),key=lambda x:x[1])
def daymove(s,d):
    t0=int(dt.datetime(d.year,d.month,d.day,tzinfo=dt.timezone.utc).timestamp()*1000)
    k=kl[s]; o=k.get(t0); c=k.get(t0+23*3600000); n1=k.get(t0+47*3600000); n3=k.get(t0+95*3600000); p=k.get(t0-24*3600000)
    if not o: return None
    hi=max(k[t0+i*3600000][1] for i in range(24) if t0+i*3600000 in k); lo=min(k[t0+i*3600000][2] for i in range(24) if t0+i*3600000 in k)
    return dict(gun=(c[3]/o[0]-1)*100 if c else None, ertesi=(n1[3]/o[0]-1)*100 if n1 else None, uc=(n3[3]/o[0]-1)*100 if n3 else None,
                hi=(hi/o[0]-1)*100, lo=(lo/o[0]-1)*100, onceki=(o[0]/p[0]-1)*100 if p else None)
print(f"{'COIN':14s}{'gun':>11s}{'fund/gun%':>10s} | {'onceki gun':>10s} {'o gun':>7s} {'maxUp':>7s} {'maxDn':>7s} {'+2gun':>7s} {'+4gun':>7s}")
for (s,d),v in dl[:25]:
    m=daymove(s,d) or {}
    print(f"{s:14s}{str(d):>11s}{v*100:+10.2f} | {f(m.get('onceki'),10)} {f(m.get('gun'))} {f(m.get('hi'))} {f(m.get('lo'))} {f(m.get('ertesi'))} {f(m.get('uc'))}")
print("\n=== GUNLUK TOPLAM FUNDING — en yuksek 20 coin-gun ===")
print(f"{'COIN':14s}{'gun':>11s}{'fund/gun%':>10s} | {'onceki gun':>10s} {'o gun':>7s} {'maxUp':>7s} {'maxDn':>7s} {'+2gun':>7s} {'+4gun':>7s}")
for (s,d),v in dl[::-1][:20]:
    m=daymove(s,d) or {}
    print(f"{s:14s}{str(d):>11s}{v*100:+10.2f} | {f(m.get('onceki'),10)} {f(m.get('gun'))} {f(m.get('hi'))} {f(m.get('lo'))} {f(m.get('ertesi'))} {f(m.get('uc'))}")

# ---- haber eslestirme ----
news=json.load(open("/Users/mea/Desktop/Funding trader/news_cache.json"))
items=list(news.values())[0] if isinstance(news,dict) else news
if isinstance(items,dict): items=list(items.values())
ext_coins=sorted(set([r["sym"] for r in NS]+[r["sym"] for r in PS]+[s for (s,d),_ in dl[:25]]+[s for (s,d),_ in dl[::-1][:20]]))
print("\n=== HABER/DUYURU ESLESMESI (news_cache.json, son 30g) ===")
for s in ext_coins:
    base=s[:-4]
    hits=[]
    for it in items:
        if not isinstance(it,dict): continue
        t=it.get("ts") or it.get("event_ts") or 0
        if t<now-31*86400000: continue
        syms=[x.upper() for x in it.get("symbols",[])]
        title=it.get("title","")
        if base in syms or s in syms or f" {base} " in f" {title.upper()} " or f"({base})" in title.upper():
            hits.append((t,it.get("source"),it.get("kind"),title))
    if hits:
        print(f"\n{s}:")
        for t,src,kind,title in sorted(hits)[:6]:
            print(f"   {ts(t)} [{src}/{kind}] {title[:110]}")
