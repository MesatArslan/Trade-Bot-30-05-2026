#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMA50 + STOCHRSI SİNYAL BOTU — web panelindeki "EMA50" sekmesinin taktiğini otomatik
tarar ve kurulumu tutan HER coin için işlem sinyali (giriş / stop / TP1 / TP2) verir.

v4 (2026-09-04): Bot artık "kendi giriyor gibi" davranmaz. Sanal bakiye, pozisyon
slotu (MAX_POS), notional, komisyon, kısmi çıkış YOK. Sadece sinyal üretir, kaydeder
ve sonrasında seviyelerin (TP1/TP2/stop) dokunulup dokunulmadığını izler (rapor için).

KURULUM (server.py _emastoch_detail ile birebir):
  EMA:      Length 50, source close (+ EMA9 yumuşatma çizgisi)
  StochRSI: RSI 14, Stoch 14, %K 3, %D 3
  LONG  : fiyat > EMA50  ve  StochRSI %K < 30 + %K/%D boğa kesişimi
  SHORT : fiyat < EMA50  ve  StochRSI %K > 70 + %K/%D ayı kesişimi
  REJİM : ADX(14) ≥ 22
  SEVİYE: stop = STOP_ATR_MULT×ATR | TP1 = +1.5R | TP2 = +2.5R

Çalıştırma:   python emastoch_bot.py <tf> [süre_dk]   (süre 0 veya boş = sınırsız)
  Örn 1dk:    python emastoch_bot.py 1
  Örn 5dk:    python emastoch_bot.py 5 480
Çıktı: konsol + emastoch_bot_<tf>m_report.txt + emastoch_bot_<tf>m_trades.json
"""
import sys, os, time, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import _bybit_get, _BYBIT, emastoch_board

# ───────── PARAMETRELER (CLI) ─────────
TF           = sys.argv[1] if len(sys.argv) > 1 else "1"
DURATION_MIN = int(sys.argv[2]) if len(sys.argv) > 2 else 0   # 0 = sınırsız

# ───────── SİNYAL KRİTERLERİ ─────────
REQUIRE_CROSS = os.environ.get("REQUIRE_CROSS", "1") != "0"   # %K/%D dönüş kesişimi şart
MIN_SCORE    = float(os.environ.get("MIN_SCORE", "3.0"))
STOP_MIN_PCT = 0.15       # stop en az %0.15
STOP_MAX_PCT = 6.0        # stop en fazla %6
SYM_COOLDOWN = int(os.environ.get("SYM_COOLDOWN", str(12*60)))  # aynı coin+yön için tekrar sinyal aralığı (sn)
TP1_R, TP2_R = 1.5, 2.5
# TF'ye göre izleme süresi: seviyeler dokunulmazsa sinyal "zaman" ile kapanır
MAX_HOLD_MIN = {"1": 25, "3": 60, "5": 120, "15": 300}.get(TF, 90)
POLL_SEC     = 15

# ───────── UYARI FİLTRELERİ (v3 otopsi bulguları) ─────────
# v4: bunlar sinyali ENGELLEMEZ, sinyale "uyarı" etiketi olarak eklenir.
# STRICT=1 ile eski davranış (uyarılı sinyal üretilmez).
STRICT        = os.environ.get("STRICT", "0") == "1"
BTC_CONFIRM   = float(os.environ.get("BTC_CONFIRM", "0.3"))   # BTC 1s getirisi ≥ bu % (yönümüzde) ve 4s aynı yön; 0=kapalı
STOP_ATR_MULT = float(os.environ.get("STOP_ATR_MULT", "4.0")) # stop = bu × ATR14 (board 1.2 verir)
DEAD_COIN_PCT = float(os.environ.get("DEAD_COIN_PCT", "5"))   # 24s getirisi ≤ -bu% → ölü coin uyarısı
NEWS_GUARD    = os.environ.get("NEWS_GUARD", "1") != "0"
NEWS_ABS_PCT  = float(os.environ.get("NEWS_ABS_PCT", "15"))
NEWS_VOL_MULT = float(os.environ.get("NEWS_VOL_MULT", "5"))
CRYPTOPANIC_TOKEN = os.environ.get("CRYPTOPANIC_TOKEN", "")

BASE = os.path.dirname(os.path.abspath(__file__))
TAG  = f"{TF}m"
REP_PATH = os.path.join(BASE, f"emastoch_bot_{TAG}_report.txt")
LOG_PATH = os.path.join(BASE, f"emastoch_bot_{TAG}_trades.json")
END_TS   = (time.time() + DURATION_MIN * 60) if DURATION_MIN > 0 else float("inf")

signals=[]; open_sig={}; last_sig={}; _sid=0

def now(): return dt.datetime.now().strftime("%H:%M:%S")
def log(m): print(f"[{now()}][{TAG}] {m}", flush=True)

def tickers():
    """Tüm USDT perp için canlı son fiyat."""
    tk=_bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    out={}
    for t in tk:
        s=t["symbol"]
        if not s.endswith("USDT"): continue
        try: out[s]=float(t["lastPrice"])
        except (KeyError,ValueError,TypeError): continue
    return out

_BTC_CACHE = {"ts": 0, "r1": None, "r4": None}
def btc_momentum():
    """BTC son KAPANMIŞ 5dk mumlara göre 1s ve 4s getirisi (%). 30sn cache."""
    if time.time()-_BTC_CACHE["ts"] < 30: return _BTC_CACHE["r1"], _BTC_CACHE["r4"]
    try:
        from server import _bybit_kl
        k = _bybit_kl("BTCUSDT", "5", 60); cl = [float(x[4]) for x in k][:-1]
        r1 = (cl[-1]/cl[-13]-1)*100; r4 = (cl[-1]/cl[-49]-1)*100
        _BTC_CACHE.update(ts=time.time(), r1=r1, r4=r4)
    except Exception as e:
        log(f"⚠️ BTC momentum alınamadı: {e}")
    return _BTC_CACHE["r1"], _BTC_CACHE["r4"]

def btc_warn(side):
    if not BTC_CONFIRM: return ""
    r1, r4 = btc_momentum()
    if r1 is None: return "btc-veri-yok"
    sgn = 1 if side == "LONG" else -1
    if r1*sgn < BTC_CONFIRM or r4*sgn < 0: return f"BTC teyidi yok (1s {r1:+.2f}% 4s {r4:+.2f}%)"
    return ""

_NEWS_CACHE = {}
def news_warn(sym, pc24):
    """Haber/olay uyarısı: |24s| ≥ NEWS_ABS_PCT, son 1s hacmi anormal, CryptoPanic haberi."""
    if not NEWS_GUARD: return ""
    if abs(pc24)*100 >= NEWS_ABS_PCT: return f"olay: 24s {pc24*100:+.1f}%"
    c = _NEWS_CACHE.get(sym)
    if c and time.time()-c[0] < 300: return c[1]
    why = ""
    try:
        from server import _bybit_kl
        k = _bybit_kl(sym, "5", 300); turn = [float(x[6]) for x in k][:-1]
        if len(turn) >= 300-1:
            v1 = sum(turn[-12:]); v24 = sum(turn[-300:-12])/24
            if v24 > 0 and v1 >= NEWS_VOL_MULT*v24: why = f"olay: hacim {v1/v24:.1f}×"
    except Exception: pass
    if not why and CRYPTOPANIC_TOKEN:
        try:
            import urllib.request
            u = f"https://cryptopanic.com/api/v1/posts/?auth_token={CRYPTOPANIC_TOKEN}&currencies={sym[:-4]}&kind=news&public=true"
            d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "curl/8"}), timeout=8))
            for post in d.get("results", [])[:10]:
                t = dt.datetime.fromisoformat(post["published_at"].replace("Z", "+00:00")).timestamp()
                if time.time()-t < 2*3600: why = f"haber: {post.get('title','')[:50]}"; break
        except Exception: pass
    _NEWS_CACHE[sym] = (time.time(), why)
    return why

def apply_stop_override(c):
    """Board 1.2×ATR stop verir; STOP_ATR_MULT×ATR kullan, TP'leri R'ye göre yeniden hesapla."""
    c = dict(c)
    if STOP_ATR_MULT and abs(STOP_ATR_MULT-1.2) >= 1e-9:
        e = c["entry"]; atr = c["atr_pct"]/100*e; sd = STOP_ATR_MULT*atr
        c["stop"] = round(e-sd if c["side"] == "LONG" else e+sd, 8)
        c["stop_pct"] = round(sd/e*100, 2)
    e = c["entry"]; R = abs(e-c["stop"])
    c["tp1"] = round(e+R*TP1_R if c["side"] == "LONG" else e-R*TP1_R, 8)
    c["tp2"] = round(e+R*TP2_R if c["side"] == "LONG" else e-R*TP2_R, 8)
    return c

def emit_signal(c, warns):
    """Kurulumu tutan aday için işlem sinyali kaydet (giriş/stop/TP)."""
    global _sid
    _sid+=1
    sym=c["symbol"]; side=c["side"]; entry=c["entry"]; stop=c["stop"]
    R=abs(entry-stop)
    sg={"id":_sid,"tf":TF,"symbol":sym,"side":side,"entry":entry,
        "stop":stop,"stop_pct":c["stop_pct"],"tp1":c["tp1"],"tp2":c["tp2"],"R":R,
        "k":c["k"],"d":c["d"],"rsi":c["rsi"],"adx":c["adx"],"cross":c["cross"],
        "dist_ema":c["dist_ema"],"atr_pct":c["atr_pct"],"score":c["score"],
        "grade":c.get("grade"),"funding":c["funding"],"pc24":c.get("pc24"),
        "warn":warns,
        "opened_ts":time.time(),"opened":now(),
        "opened_iso":dt.datetime.now().isoformat(timespec="seconds"),
        "best":entry,"max_r":0.0,"tp1_hit":False,"tp2_hit":False,"stop_hit":False,
        "tp1_t":None,"tp2_t":None,"stop_t":None,
        "closed":False,"result":None,"end_r":None}
    open_sig[(sym,side)]=sg; signals.append(sg); last_sig[(sym,side)]=time.time()
    arrow="🟢 LONG" if side=="LONG" else "🔴 SHORT"
    w=f" ⚠️ {' | '.join(warns)}" if warns else ""
    log(f"{arrow} SİNYAL #{_sid} {sym:13} giriş {entry:<11g} stop {stop:<11g} (%{c['stop_pct']}) "
        f"tp1 {c['tp1']:<11g} tp2 {c['tp2']:<11g} | skor {c['score']} K {c['k']} D {c['d']} "
        f"ADX {c['adx']} {c['cross']} distEMA {c['dist_ema']:+.2f}%{w}")
    write_report(False)

def close_signal(sg, result, px):
    sgn=1 if sg["side"]=="LONG" else -1
    sg["closed"]=True; sg["result"]=result; sg["closed_ts"]=time.time(); sg["closed_at"]=now()
    sg["hold_min"]=round((sg["closed_ts"]-sg["opened_ts"])/60,1)
    sg["end_r"]=round((px-sg["entry"])*sgn/sg["R"],2) if sg["R"] else 0.0
    open_sig.pop((sg["symbol"],sg["side"]),None)
    emo={"tp2":"🎯","stop":"🛑","zaman":"⏰"}.get(result,"•")
    log(f"   {emo} #{sg['id']} {sg['symbol']} sinyal kapandı [{result}] @ {px:g} | "
        f"max +{sg['max_r']}R son {sg['end_r']:+}R | tp1 {'✓' if sg['tp1_hit'] else '✗'} "
        f"tp2 {'✓' if sg['tp2_hit'] else '✗'} ({sg['hold_min']}dk)")

def track(tk):
    """Açık sinyallerin seviyelerine dokunuldu mu? (izleme; pozisyon yönetimi DEĞİL)"""
    for key in list(open_sig.keys()):
        sg=open_sig[key]; px=tk.get(sg["symbol"])
        if px is None: continue
        long=sg["side"]=="LONG"; sgn=1 if long else -1; R=sg["R"]
        sg["best"]=max(sg["best"],px) if long else min(sg["best"],px)
        sg["max_r"]=round((sg["best"]-sg["entry"])*sgn/R,2) if R else 0.0
        hit=lambda lvl: (px<=lvl) if long else (px>=lvl)
        up =lambda lvl: (px>=lvl) if long else (px<=lvl)
        if not sg["tp1_hit"] and up(sg["tp1"]):
            sg["tp1_hit"]=True; sg["tp1_t"]=now(); log(f"   🎯 #{sg['id']} {sg['symbol']} TP1 dokundu @ {px:g}")
        if not sg["tp2_hit"] and up(sg["tp2"]):
            sg["tp2_hit"]=True; sg["tp2_t"]=now(); close_signal(sg,"tp2",px); continue
        if hit(sg["stop"]):
            sg["stop_hit"]=True; sg["stop_t"]=now(); close_signal(sg,"stop",px); continue
        if time.time()-sg["opened_ts"]>MAX_HOLD_MIN*60:
            close_signal(sg,"zaman",px)

def scan():
    """Board'dan (panelle aynı) adayları çek, kurulumu tutan HER coin için sinyal ver."""
    try:
        board=emastoch_board(TF)
    except Exception as e:
        log(f"⚠️ board hatası: {e}"); return
    cands=board.get("candidates",[])
    skipped=[]
    for c in cands:
        s=c["symbol"]; key=(s,c["side"])
        if key in open_sig: continue
        if time.time()-last_sig.get(key,0)<SYM_COOLDOWN: continue
        if c["score"]<MIN_SCORE: continue
        if REQUIRE_CROSS and c["cross"]=="none": continue
        c = apply_stop_override(c)
        if not (STOP_MIN_PCT <= c["stop_pct"] <= STOP_MAX_PCT):
            skipped.append(f"{s}(stop%{c['stop_pct']})"); continue
        warns=[]
        pc24 = float(c.get("pc24") or 0)
        if DEAD_COIN_PCT and pc24*100 <= -DEAD_COIN_PCT: warns.append(f"ölü coin 24s {pc24*100:+.1f}%")
        w=news_warn(s, pc24);  w and warns.append(w)
        w=btc_warn(c["side"]); w and warns.append(w)
        if STRICT and warns:
            skipped.append(f"{s}({'; '.join(warns)})"); continue
        emit_signal(c, warns)
    top=", ".join(f"{c['symbol']}:{c['side'][0]}{c['score']}" for c in cands[:5])
    if top:
        r1, r4 = btc_momentum()
        log(f"👀 board {board.get('count',0)} aday (L{board.get('longs',0)}/S{board.get('shorts',0)}) top: {top} | "
            f"BTC 1s {r1 if r1 is None else round(r1,2)}% 4s {r4 if r4 is None else round(r4,2)}% | açık sinyal {len(open_sig)}")
    if skipped:
        log(f"🚫 elenen: {', '.join(skipped[:8])}{' …' if len(skipped)>8 else ''}")

def write_report(final=False):
    done=[s for s in signals if s["closed"]]
    tp1=[s for s in signals if s["tp1_hit"]]; tp2=[s for s in signals if s["tp2_hit"]]
    stp=[s for s in done if s["result"]=="stop"]
    L=[]; P=L.append
    P("═"*74)
    P(f"  EMA50+StochRSI SİNYAL BOTU [{TAG}] — {'FİNAL' if final else 'ARA'} RAPOR ({now()})")
    P("═"*74)
    P(f"  TF {TF}dk | sinyal {len(signals)} | açık {len(open_sig)} | kapanan {len(done)}")
    if signals:
        P(f"  TP1 dokunan {len(tp1)} (%{100*len(tp1)/len(signals):.0f}) | TP2 dokunan {len(tp2)} "
          f"(%{100*len(tp2)/len(signals):.0f}) | stop {len(stp)}"
          + (f" (%{100*len(stp)/len(done):.0f} kapananın)" if done else ""))
        if done:
            avg=sum(s["end_r"] for s in done)/len(done)
            P(f"  Kapananlarda ortalama son R {avg:+.2f} | ortalama max R +{sum(s['max_r'] for s in done)/len(done):.2f}")
    P("═"*74)
    for s in sorted(signals,key=lambda x:x["id"]):
        st=f"KAPALI/{s['result']}" if s["closed"] else "İZLENİYOR"
        a="LONG" if s["side"]=="LONG" else "SHORT"
        P(f"\n  #{s['id']} {a} {s['symbol']} [{st}] {s['opened']}")
        P(f"     Giriş {s['entry']:g} | Stop {s['stop']:g} (%{s['stop_pct']}) | TP1 {s['tp1']:g} | TP2 {s['tp2']:g}")
        P(f"     K {s['k']} D {s['d']} RSI {s['rsi']} ADX {s['adx']} {s['cross']} distEMA {s['dist_ema']:+.2f}% | skor {s['score']}")
        if s["warn"]: P(f"     ⚠️ {' | '.join(s['warn'])}")
        P(f"     max +{s['max_r']}R | tp1 {'✓ '+str(s['tp1_t']) if s['tp1_hit'] else '✗'} | tp2 {'✓ '+str(s['tp2_t']) if s['tp2_hit'] else '✗'}"
          + (f" | son {s['end_r']:+}R ({s['hold_min']}dk)" if s["closed"] else ""))
    P("\n"+"═"*74)
    txt="\n".join(L)
    with open(REP_PATH,"w") as f: f.write(txt)
    tmp=LOG_PATH+".tmp"
    with open(tmp,"w") as f:
        json.dump({"mode":"signal","tf":TF,"count":len(signals),"open":len(open_sig),
                   "updated":int(time.time()),"trades":signals}, f, indent=2, default=str)
    os.replace(tmp,LOG_PATH)
    return txt

# ───────── ANA DÖNGÜ ─────────
log(f"📡 EMA50+StochRSI SİNYAL botu başladı — TF {TF}dk, "
    f"{'sınırsız' if DURATION_MIN<=0 else str(DURATION_MIN)+'dk'}, skor ≥{MIN_SCORE}, "
    f"cross-teyit={'AÇIK' if REQUIRE_CROSS else 'KAPALI'}, stop {STOP_ATR_MULT}×ATR, "
    f"izleme {MAX_HOLD_MIN}dk, coin cooldown {SYM_COOLDOWN//60}dk | "
    f"uyarılar (BTC {BTC_CONFIRM}%, ölü coin ≤-{DEAD_COIN_PCT}%, haber) {'ENGELLER' if STRICT else 'sadece etiketler'}")
write_report(False)
loop=0
while time.time()<END_TS:
    loop+=1
    try:
        tk=tickers()
        track(tk)
        scan()
        if loop%4==0:
            log(f"… döngü {loop} | sinyal {len(signals)} | açık {len(open_sig)} | "
                f"kapanan {len([s for s in signals if s['closed']])}"
                + (f" | kalan {int((END_TS-time.time())/60)}dk" if DURATION_MIN>0 else ""))
            write_report(False)
    except Exception as e:
        log(f"⚠️ hata: {e}")
    time.sleep(POLL_SEC)

log("⏹️ Süre doldu.")
report=write_report(True)
print("\n"+report,flush=True)
log(f"✅ Bitti. Rapor: {REP_PATH}")
