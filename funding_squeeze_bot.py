#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FUNDING SQUEEZE PAPER BOT — gerçek edge: funding-ekstrem pozisyon dengesizliği.

Açık: aşırı NEGATIF funding = herkes short = kalabalık kırılgan. Küçük yukarı hareket
→ short'lar cover (buy) → likidasyon zinciri → şiddetli yukarı SQUEEZE.

Bu bot:
  1) Evren: funding ≤ -0.3%, hacim ≥ $5M, 24s ≤ +%5 (henüz fırlamamış), OI ARTIYOR
  2) Giriş: düşen bıçağı TUTMAZ — dönüş TEYİDİ bekler (higher-low + yeşil momentum + hacim)
  3) Stop: son swing dibinin altı (tez geçersizleşmesi), risk = equity'nin %1.5'i
  4) Çıkış: +1R'de %30, +2R'de %30, kalan trailing | funding 0'a dönerse tam çık | zaman stop
Paper: $500 sanal, canlı Bybit fiyatları. Çıktı: konsol + rapor.txt + trades.json
"""
import sys, os, time, json, statistics as stx, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import _bybit_get, _bybit_kl, _BYBIT

# ───────── AYARLAR ─────────
START_EQUITY = 500.0
RISK_PCT     = 0.015      # işlem başı risk = equity'nin %1.5'i (stop'a göre boyut)
MAX_POS      = 3
MAKER_FEE    = 0.0002     # limit giriş / tp %0.02
TAKER_FEE    = 0.00055    # market çıkış (stop/trail/funding/zaman) %0.055
NOTIONAL_CAP = 3.0        # notional en fazla equity × 3 (aşırı boyut emniyeti)

# Evren filtreleri
FUND_MAX     = -0.003     # funding ≤ -0.3% (ekstrem kalabalık short)
MIN_TURN     = 5_000_000  # 24s hacim ≥ $5M
MAX_PC24     = 0.05       # 24s ≤ +%5 (zaten fırlamış olanı ele)
MIN_PC24     = -0.35      # 24s ≥ -%35 (serbest düşüşteki enkazı ele)

# Giriş teyit tetiği (5dk mumlar)
KL_INT       = "5"
SWING_LB     = 16         # son 16 mumda swing dibi
BOUNCE_MIN   = 0.002      # dipten en az %0.2 toparlamış
BOUNCE_MAX   = 0.06       # ama %6'dan fazla uzaklaşmamış (hâlâ dip yakını)
VOL_SPIKE    = 1.3        # dönüş mumu hacmi medyanın ≥1.3 katı

# Stop / hedef / çıkış
STOP_MIN_PCT = 0.02       # stop en az %2
STOP_MAX_PCT = 0.07       # stop en fazla %7 (daha genişse aday atla)
TP1_R, TP1_F = 1.0, 0.30  # +1R'de %30 al
TP2_R, TP2_F = 2.0, 0.30  # +2R'de %30 al
TRAIL_R      = 1.5        # kalan %40: en yüksekten 1.5R iz-süren
MAX_HOLD_MIN = 240        # 4 saat zaman stop
FUND_EXIT    = 0.0        # funding ≥ 0'a dönerse yakıt bitti → çık
SYM_COOLDOWN = 30 * 60

DURATION_MIN = 180
POLL_SEC     = 30

BASE = os.path.dirname(os.path.abspath(__file__))
REP_PATH = os.path.join(BASE, "funding_squeeze_report.txt")
LOG_PATH = os.path.join(BASE, "funding_squeeze_trades.json")
END_TS   = time.time() + DURATION_MIN * 60

equity=START_EQUITY; peak_eq=START_EQUITY; trough_eq=START_EQUITY; total_fees=0.0
open_pos={}; trades=[]; last_entry={}; _tid=0

def now(): return dt.datetime.now().strftime("%H:%M:%S")
def log(m): print(f"[{now()}] {m}", flush=True)

def tickers():
    tk=_bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    out={}
    for t in tk:
        s=t["symbol"]
        if not s.endswith("USDT"): continue
        try:
            out[s]={"price":float(t["lastPrice"]), "fr":float(t.get("fundingRate") or 0),
                    "oi":float(t.get("openInterestValue") or 0), "tov":float(t["turnover24h"]),
                    "pc":float(t["price24hPcnt"])}
        except (KeyError,ValueError,TypeError): continue
    return out

def oi_rising(sym):
    """Son ~1 saatte OI artıyor mu (yeni short yakıtı)."""
    try:
        d=_bybit_get(f"{_BYBIT}/v5/market/open-interest?category=linear&symbol={sym}&intervalTime=5min&limit=12")
        vals=[float(x["openInterest"]) for x in sorted(d["result"]["list"], key=lambda x:int(x["timestamp"]))]
        return len(vals)>=6 and vals[-1] > vals[-6]  # şimdi > ~30dk önce
    except Exception:
        return True  # veri yoksa filtreleme (elemE)

def confirm_long(sym, price):
    """Dönüş teyidi: yeni dip yapmıyor + hacimli yeşil momentum + dip yakını. → (ok, swing_low, atr)"""
    try:
        k=_bybit_kl(sym, KL_INT, 80)
        o=[float(x[1]) for x in k]; h=[float(x[2]) for x in k]
        l=[float(x[3]) for x in k]; c=[float(x[4]) for x in k]; v=[float(x[5]) for x in k]
    except Exception:
        return (False,None,None)
    if len(c)<SWING_LB+3: return (False,None,None)
    swing_low=min(l[-SWING_LB:-1])
    atr=sum(h[i]-l[i] for i in range(len(c)-14,len(c)))/14
    med_v=stx.median(v[-20:-1]) if len(v)>=21 else (sum(v[-5:])/5 or 1)
    higher_low = l[-1] >= swing_low*0.999 and l[-2] >= swing_low*0.997     # yeni dip YOK
    momentum   = c[-1] > o[-1] or c[-1] > h[-2]                            # yeşil / önceki tepeyi geri al
    volspike   = (v[-1] > VOL_SPIKE*med_v) or (v[-2] > VOL_SPIKE*med_v)    # hacim onayı
    bounce=(price-swing_low)/price if price else 0
    near_low = BOUNCE_MIN <= bounce <= BOUNCE_MAX                          # dipten toparladı ama hâlâ yakın
    ok = higher_low and momentum and volspike and near_low
    return (ok, swing_low, atr)

def scan(tk):
    """Evren filtresine uyan, funding en negatif adaylar (skorlu)."""
    cands=[]
    for s,d in tk.items():
        if d["fr"] > FUND_MAX: continue
        if d["tov"] < MIN_TURN: continue
        if not (MIN_PC24 <= d["pc"] <= MAX_PC24): continue
        cands.append((s,d))
    cands.sort(key=lambda x: x[1]["fr"])   # en negatif başta
    return cands[:12]

def open_trade(sym, price, swing_low, atr, fr):
    global equity,total_fees,_tid
    _tid+=1
    stop = min(swing_low*0.999, price*(1-STOP_MIN_PCT))
    stop = max(stop, price*(1-STOP_MAX_PCT))
    stop_dist = price-stop
    risk = equity*RISK_PCT
    qty = risk/stop_dist
    notional = qty*price
    cap = equity*NOTIONAL_CAP
    if notional>cap: notional=cap; qty=notional/price
    fee=notional*MAKER_FEE; total_fees+=fee; equity-=fee
    R=stop_dist
    tr={"id":_tid,"symbol":sym,"side":"LONG","entry":price,"qty":qty,"notional":round(notional,2),
        "stop0":stop,"stop":stop,"R":R,"peak":price,"stop_pct":round(stop_dist/price*100,2),
        "tp1":price+TP1_R*R,"tp2":price+TP2_R*R,"funding":round(fr,6),"atr_pct":round(atr/price*100,3),
        "opened_ts":time.time(),"opened":now(),"fee_open":fee,"tp1_done":False,"tp2_done":False,
        "closed":False,"qty_left":qty,"legs":[],"gross_pnl":0.0,"fee_close":0.0}
    open_pos[sym]=tr; trades.append(tr); last_entry[sym]=time.time()
    log(f"🟢 GİRİŞ #{_tid} LONG {sym:13} @ {price:<11g} funding {fr*100:+.3f}% | risk ${risk:.2f} "
        f"stop {tr['stop_pct']}% notional ${notional:.0f} qty {qty:.4g} | tp1 +1R tp2 +2R trail {TRAIL_R}R | kom ${fee:.3f}")

def realize(tr, px, frac, reason, rate=TAKER_FEE):
    global equity,total_fees,peak_eq,trough_eq
    q=tr["qty"]*frac
    pnl=(px-tr["entry"])*q
    fee=q*px*rate; total_fees+=fee; equity+=pnl-fee
    peak_eq=max(peak_eq,equity); trough_eq=min(trough_eq,equity)
    tr["qty_left"]-=q; tr["gross_pnl"]+=pnl; tr["fee_close"]+=fee
    hold=(time.time()-tr["opened_ts"])/60
    tr["legs"].append({"exit":px,"pct":round(frac*100),"reason":reason,"pnl":round(pnl,3),
                       "fee":round(fee,3),"t":now(),"hold_min":round(hold,1)})
    emo="✅" if pnl>=0 else "❌"
    log(f"   {emo} ÇIKIŞ #{tr['id']} {tr['symbol']:13} @ {px:<11g} %{round(frac*100)} [{reason}] "
        f"PnL ${pnl:+.3f} kom ${fee:.3f} | equity ${equity:.2f}")

def close_trade(tr):
    tr["closed"]=True; tr["closed_ts"]=time.time()
    tr["hold_min"]=round((tr["closed_ts"]-tr["opened_ts"])/60,1)
    tr["net"]=round(tr["gross_pnl"]-tr["fee_open"]-tr["fee_close"],3)
    tr["fee_total"]=round(tr["fee_open"]+tr["fee_close"],3)
    del open_pos[tr["symbol"]]

def manage(tk):
    for sym in list(open_pos.keys()):
        tr=open_pos[sym]; d=tk.get(sym)
        if not d: continue
        px=d["price"]; tr["peak"]=max(tr["peak"],px); R=tr["R"]
        # 1) stop / trailing
        if not tr["tp1_done"]:
            if px<=tr["stop"]: realize(tr,tr["stop"],1.0,"stop"); close_trade(tr); continue
        else:
            trail=tr["peak"]-TRAIL_R*R
            tr["stop"]=max(tr["stop"],trail)
            if px<=tr["stop"]:
                realize(tr,tr["stop"],tr["qty_left"]/tr["qty"],"trail"); close_trade(tr); continue
        # 2) hedefler
        if not tr["tp1_done"] and px>=tr["tp1"]:
            realize(tr,tr["tp1"],TP1_F,"tp1",MAKER_FEE); tr["tp1_done"]=True; tr["stop"]=tr["entry"]
            log(f"   ↳ #{tr['id']} {sym} tp1 → %{int(TP1_F*100)} alındı, stop breakeven")
        if tr["tp1_done"] and not tr["tp2_done"] and px>=tr["tp2"]:
            realize(tr,tr["tp2"],TP2_F,"tp2",MAKER_FEE); tr["tp2_done"]=True
            log(f"   ↳ #{tr['id']} {sym} tp2 → %{int(TP2_F*100)} alındı, kalan trailing")
        # 3) funding yakıtı bitti (0'a döndü) → tam çık
        if d["fr"]>=FUND_EXIT and tr["qty_left"]>1e-9:
            realize(tr,px,tr["qty_left"]/tr["qty"],"funding+"); close_trade(tr); continue
        # 4) zaman stop
        if time.time()-tr["opened_ts"]>MAX_HOLD_MIN*60 and tr["qty_left"]>1e-9:
            realize(tr,px,tr["qty_left"]/tr["qty"],"zaman"); close_trade(tr)

def try_enter(tk):
    cands=scan(tk)
    watch=[]
    for s,d in cands:
        if len(open_pos)>=MAX_POS: break
        if s in open_pos: continue
        if time.time()-last_entry.get(s,0)<SYM_COOLDOWN: continue
        if not oi_rising(s):
            watch.append(f"{s}(OI↓)"); continue
        ok,sl,atr=confirm_long(s,d["price"])
        if not ok:
            watch.append(f"{s}({d['fr']*100:+.2f}%)"); continue
        open_trade(s,d["price"],sl,atr,d["fr"])
    if watch:
        log(f"👀 izlemede (teyit yok): {', '.join(watch[:8])}")

def write_report(final=False):
    done=[t for t in trades if t["closed"]]; wins=[t for t in done if t["net"]>0]
    net=equity-START_EQUITY
    L=[]; P=L.append
    P("═"*70); P(f"  FUNDING SQUEEZE PAPER BOT — {'FİNAL' if final else 'ARA'} RAPOR ({now()})"); P("═"*70)
    P(f"  Başlangıç ${START_EQUITY:.2f} | Bitiş equity ${equity:.2f}")
    P(f"  ► NET ${net:+.2f} ({net/START_EQUITY*100:+.2f}%) | komisyon ${total_fees:.3f}")
    P(f"  Risk/işlem %{RISK_PCT*100:.1f} | pik ${peak_eq:.2f} dip ${trough_eq:.2f}")
    if done:
        P(f"  Kapanan {len(done)} | ✅ {len(wins)} ❌ {len(done)-len(wins)} | isabet %{100*len(wins)/len(done):.0f} | açık {len(open_pos)}")
    else:
        P(f"  Kapanan 0 | açık {len(open_pos)}")
    P("═"*70)
    for t in sorted(trades,key=lambda x:x["id"]):
        st="KAPALI" if t["closed"] else "AÇIK"
        net_t=t.get("net",round(t["gross_pnl"]-t["fee_open"]-t["fee_close"],3))
        emo="✅" if net_t>0 else ("❌" if net_t<0 else "➖")
        P(f"\n  #{t['id']} {emo} LONG {t['symbol']} [{st}]")
        P(f"     Giriş {t['entry']:g} @ {t['opened']} | funding {t['funding']*100:+.3f}% atr {t['atr_pct']}% stop %{t['stop_pct']}")
        P(f"     Notional ${t['notional']:.0f} qty {t['qty']:.4g}")
        for lg in t["legs"]:
            P(f"       → %{lg['pct']:>3} @ {lg['exit']:g} [{lg['reason']:8}] {lg['t']} ({lg['hold_min']}dk) PnL ${lg['pnl']:+.3f} kom ${lg['fee']:.3f}")
        if not t["legs"]: P("       → (henüz çıkış yok)")
        P(f"     ► NET ${net_t:+.3f}")
    P("\n"+"═"*70)
    txt="\n".join(L)
    with open(REP_PATH,"w") as f: f.write(txt)
    with open(LOG_PATH,"w") as f:
        json.dump({"start_equity":START_EQUITY,"equity":equity,"net":equity-START_EQUITY,
                   "total_fees":total_fees,"trades":trades}, f, indent=2, default=str)
    return txt

# ───────── ANA DÖNGÜ ─────────
log(f"🤖 Funding Squeeze bot başladı — ${START_EQUITY}, risk %{RISK_PCT*100:.1f}/işlem, {DURATION_MIN}dk, "
    f"funding≤{FUND_MAX*100:.1f}% hacim≥${MIN_TURN/1e6:.0f}M 24s≤+%{MAX_PC24*100:.0f}, max {MAX_POS} poz")
loop=0
while time.time()<END_TS:
    loop+=1
    try:
        tk=tickers()
        manage(tk)
        try_enter(tk)
        if loop%4==0:
            log(f"… döngü {loop} | equity ${equity:.2f} | açık {len(open_pos)} | kapanan {len([t for t in trades if t['closed']])} | kom ${total_fees:.3f} | kalan {int((END_TS-time.time())/60)}dk")
            write_report(False)
    except Exception as e:
        log(f"⚠️ hata: {e}")
    time.sleep(POLL_SEC)

log("⏹️ Süre doldu — açık pozisyonlar kapatılıyor…")
try:
    tk=tickers()
    for sym in list(open_pos.keys()):
        tr=open_pos[sym]; px=tk.get(sym,{}).get("price",tr["entry"])
        realize(tr,px,tr["qty_left"]/tr["qty"],"süre-sonu"); close_trade(tr)
except Exception as e:
    log(f"⚠️ kapatma hatası: {e}")
report=write_report(True)
print("\n"+report,flush=True)
log(f"✅ Bitti. Rapor: {REP_PATH}")
