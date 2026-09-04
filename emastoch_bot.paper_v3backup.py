#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMA50 + STOCHRSI PAPER BOT — web panelindeki "EMA50" sekmesinin taktiğini otomatik uygular.

KURULUM (server.py _emastoch_detail ile birebir):
  EMA:      Length 50, source close (+ EMA9 yumuşatma çizgisi)
  StochRSI: RSI 14, Stoch 14, %K 3, %D 3
  LONG  : fiyat > EMA50  ve  StochRSI %K < 30   (yukarı trend + dip / aşırı satım)
  SHORT : fiyat < EMA50  ve  StochRSI %K > 70   (aşağı trend + tepe / aşırı alım)
  REJİM : ADX(14) ≥ 22   → yatay piyasadaki whipsaw sinyallerini eler
  RİSK  : stop = 1.2×ATR | TP1 = +1.5R (%40) | TP2 = +2.5R (%30) | kalan trailing

Bu bot aday üretimini emastoch_board(tf) fonksiyonundan alır (panelle AYNI mantık),
en yüksek skorlu kurulumlara paper pozisyon açar ve canlı Bybit fiyatıyla yönetir.

Çalıştırma:   python emastoch_bot.py <tf> <süre_dk>
  Örn 1dk:    python emastoch_bot.py 1 30
  Örn 5dk:    python emastoch_bot.py 5 30
Paper: $500 sanal, canlı Bybit fiyatları. Çıktı: konsol + rapor.txt + trades.json
"""
import sys, os, time, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import _bybit_get, _BYBIT, emastoch_board

# ───────── PARAMETRELER (CLI) ─────────
TF           = sys.argv[1] if len(sys.argv) > 1 else "1"
DURATION_MIN = int(sys.argv[2]) if len(sys.argv) > 2 else 30

# ───────── AYARLAR ─────────
START_EQUITY = 500.0
RISK_PCT     = 0.01       # işlem başı risk = equity'nin %1 (stop mesafesine göre boyut)
# MAX_POS: aynı anda açılabilecek pozisyon sayısı. Kullanıcı isteği: SINIRSIZ —
# kurulumu tutan (skor≥3 + cross teyidi) HER fırsata gir, 4 ile sınırlama.
# Kalite filtreleri (MIN_SCORE, REQUIRE_CROSS) "geçerli işlem"i tanımlar, kalır.
MAX_POS      = int(os.environ.get("MAX_POS", "3"))  # v3: 3 (otopsi: sınırsız = hesap 2 günde sıfır)
MAKER_FEE    = 0.0002     # limit giriş / tp %0.02
TAKER_FEE    = 0.00055    # market çıkış (stop/trail/zaman) %0.055
NOTIONAL_CAP = 3.0        # notional en fazla equity × 3

# Aday seçimi
# REQUIRE_CROSS: v1 testinde (30dk canlı) cross=none girişler 1m'de -8.87 / 5m'de
# -21.25 net ile TÜM zararın kaynağıydı (düşen bıçağı tutmak). cross var olanlar
# artıdaydı. Bu yüzden v2 varsayılan: SADECE %K/%D dönüş kesişimi olunca gir.
REQUIRE_CROSS = os.environ.get("REQUIRE_CROSS", "1") != "0"
MIN_SCORE    = 3.0        # skor eşiği (board skoru: depth*4 + near*2 + cross + adx_bonus)
# MAX_SCORE: ters-sinyal / saf-kayıp testi için denenen "kazananı ele" filtresi.
# SONUÇ: BAŞARISIZ, varsayılan DEVRE DIŞI (999). 5m 47 işlem analizinde score≥7.0
# elemek 9 kazananı elerken 17 KAYBEDENİ de eledi (-$75.5) ve en büyük kazananı
# (USELESSUSDT +$15.51, skor 6.54) TUTTU. Kazananların girişte imzası yok → skor
# onları izole edemiyor. Elle denemek istersen: MAX_SCORE=7 python emastoch_bot.py
MAX_SCORE    = float(os.environ.get("MAX_SCORE", "999"))  # bu skordan yükseği ELE (999=kapalı)
STOP_MIN_PCT = 0.15       # stop en az %0.15 (çok dar stop = anında vurulur, ele)
STOP_MAX_PCT = 6.0        # stop en fazla %6 (daha genişse aday atla)
SYM_COOLDOWN = 12 * 60    # aynı coine tekrar girmeden önce bekleme

# Çıkış — TP1=+1.5R, TP2=+2.5R (board seviyeleri), kalan trailing
TP1_R, TP1_F = 1.5, 0.40  # +1.5R'de %40 al, stop breakeven
TP2_R, TP2_F = 2.5, 0.30  # +2.5R'de %30 al, kalan trailing
TRAIL_R      = 1.2        # kalan %30: en iyi fiyattan 1.2R iz-süren
# TF'ye göre zaman stop: 1dk → 25dk, 5dk → 120dk, diğer → 40 bar
MAX_HOLD_MIN = {"1": 25, "3": 60, "5": 120, "15": 300}.get(TF, 90)

POLL_SEC     = 15

# ───────── v3 FİLTRELER (2026-09-03 otopsi bulguları) ─────────
# Otopsi (15.596 işlem, 30g): kazanan/kaybedeni ayıran tek güçlü şey BTC'nin işlem
# sırasındaki yönüydü; stop 1.2ATR önceki 2 saatin dalgasının 1/5'iydi (%42 stop
# ilk 15 dk'da); 24s'te %5+ düşmüş coinde her iki yön de en kötüydü.
BTC_CONFIRM   = float(os.environ.get("BTC_CONFIRM", "0.3"))   # BTC 1s getirisi ≥ bu % (yönümüzde) ve 4s aynı yön; 0=kapalı
STOP_ATR_MULT = float(os.environ.get("STOP_ATR_MULT", "4.0")) # stop = bu × ATR14 (board 1.2 verir, bot geçersiz kılar)
DEAD_COIN_PCT = float(os.environ.get("DEAD_COIN_PCT", "5"))   # 24s getirisi ≤ -bu% olan coine girme; 0=kapalı
NEWS_GUARD    = os.environ.get("NEWS_GUARD", "1") != "0"      # haber/olay koruması
NEWS_ABS_PCT  = float(os.environ.get("NEWS_ABS_PCT", "15"))   # |24s getiri| ≥ bu% → olay var, girme
NEWS_VOL_MULT = float(os.environ.get("NEWS_VOL_MULT", "5"))   # son 1s hacmi, önceki 24s saatlik ort. × bu → olay var
CRYPTOPANIC_TOKEN = os.environ.get("CRYPTOPANIC_TOKEN", "")   # varsa gerçek haber akışı (son 2s'te haber = girme)

BASE = os.path.dirname(os.path.abspath(__file__))
TAG  = f"{TF}m"
REP_PATH = os.path.join(BASE, f"emastoch_bot_{TAG}_report.txt")
LOG_PATH = os.path.join(BASE, f"emastoch_bot_{TAG}_trades.json")
END_TS   = time.time() + DURATION_MIN * 60

equity=START_EQUITY; peak_eq=START_EQUITY; trough_eq=START_EQUITY; total_fees=0.0
open_pos={}; trades=[]; last_entry={}; _tid=0

def now(): return dt.datetime.now().strftime("%H:%M:%S")
def log(m): print(f"[{now()}][{TAG}] {m}", flush=True)

def tickers():
    """Tüm USDT perp için canlı son fiyat."""
    tk=_bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    out={}
    for t in tk:
        s=t["symbol"]
        if not s.endswith("USDT"): continue
        try:
            out[s]=float(t["lastPrice"])
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

def btc_ok(side):
    if not BTC_CONFIRM: return True, ""
    r1, r4 = btc_momentum()
    if r1 is None: return False, "btc-veri-yok"
    sgn = 1 if side == "LONG" else -1
    if r1*sgn < BTC_CONFIRM or r4*sgn < 0: return False, f"btc 1s{r1:+.2f}% 4s{r4:+.2f}%"
    return True, f"btc 1s{r1:+.2f}%"

_NEWS_CACHE = {}
def news_guard(sym, pc24):
    """Haber/olay koruması. Dönüş: (engelle?, neden).
    1) |24s getiri| ≥ NEWS_ABS_PCT → coin'de olay var.
    2) Son 1s hacmi, önceki 24s saatlik ortalamanın ≥ NEWS_VOL_MULT katı → anormal akış.
    3) CRYPTOPANIC_TOKEN varsa: son 2 saatte coin hakkında haber → engelle."""
    if not NEWS_GUARD: return False, ""
    if abs(pc24)*100 >= NEWS_ABS_PCT: return True, f"24s {pc24*100:+.1f}%"
    c = _NEWS_CACHE.get(sym)
    if c and time.time()-c[0] < 300: return c[1], c[2]
    block, why = False, ""
    try:
        from server import _bybit_kl
        k = _bybit_kl(sym, "5", 300); turn = [float(x[6]) for x in k][:-1]
        if len(turn) >= 300-1:
            v1 = sum(turn[-12:]); v24 = sum(turn[-300:-12])/24
            if v24 > 0 and v1 >= NEWS_VOL_MULT*v24: block, why = True, f"hacim {v1/v24:.1f}×"
    except Exception: pass
    if not block and CRYPTOPANIC_TOKEN:
        try:
            base = sym[:-4]
            import urllib.request
            u = f"https://cryptopanic.com/api/v1/posts/?auth_token={CRYPTOPANIC_TOKEN}&currencies={base}&kind=news&public=true"
            d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "curl/8"}), timeout=8))
            for post in d.get("results", [])[:10]:
                t = dt.datetime.fromisoformat(post["published_at"].replace("Z", "+00:00")).timestamp()
                if time.time()-t < 2*3600: block, why = True, f"haber: {post.get('title','')[:50]}"; break
        except Exception: pass
    _NEWS_CACHE[sym] = (time.time(), block, why)
    return block, why

def apply_stop_override(c):
    """Board 1.2×ATR stop verir; v3 STOP_ATR_MULT×ATR kullanır (gürültü çözümü). TP'ler R'ye göre yeniden."""
    if not STOP_ATR_MULT or abs(STOP_ATR_MULT-1.2) < 1e-9: return c
    c = dict(c); e = c["entry"]; atr = c["atr_pct"]/100*e; sd = STOP_ATR_MULT*atr
    c["stop"] = e-sd if c["side"] == "LONG" else e+sd
    c["stop_pct"] = round(sd/e*100, 2)
    c["tp1"] = e+sd*1.5 if c["side"] == "LONG" else e-sd*1.5
    c["tp2"] = e+sd*2.5 if c["side"] == "LONG" else e-sd*2.5
    return c

def open_trade(c):
    """Board adayından (c) paper pozisyon aç. c: emastoch_board candidate dict."""
    global equity,total_fees,_tid
    _tid+=1
    sym=c["symbol"]; side=c["side"]; entry=c["entry"]; stop=c["stop"]
    stop_dist=abs(entry-stop)
    risk=equity*RISK_PCT
    qty=risk/stop_dist
    notional=qty*entry
    cap=equity*NOTIONAL_CAP
    if notional>cap: notional=cap; qty=notional/entry
    fee=notional*MAKER_FEE; total_fees+=fee; equity-=fee
    R=stop_dist
    if side=="LONG":
        tp1=entry+TP1_R*R; tp2=entry+TP2_R*R
    else:
        tp1=entry-TP1_R*R; tp2=entry-TP2_R*R
    tr={"id":_tid,"symbol":sym,"side":side,"entry":entry,"qty":qty,
        "notional":round(notional,2),"stop0":stop,"stop":stop,"R":R,
        "best":entry,"stop_pct":c["stop_pct"],"tp1":tp1,"tp2":tp2,
        "k":c["k"],"d":c["d"],"rsi":c["rsi"],"adx":c["adx"],"cross":c["cross"],
        "dist_ema":c["dist_ema"],"atr_pct":c["atr_pct"],"score":c["score"],
        "funding":c["funding"],"opened_ts":time.time(),"opened":now(),
        "fee_open":fee,"tp1_done":False,"tp2_done":False,"closed":False,
        "qty_left":qty,"legs":[],"gross_pnl":0.0,"fee_close":0.0}
    open_pos[sym]=tr; trades.append(tr); last_entry[sym]=time.time()
    arrow="🟢 LONG" if side=="LONG" else "🔴 SHORT"
    log(f"{arrow} GİRİŞ #{_tid} {sym:13} @ {entry:<11g} skor {c['score']} | "
        f"K {c['k']} D {c['d']} ADX {c['adx']} {c['cross']} distEMA {c['dist_ema']:+.2f}% | "
        f"risk ${risk:.2f} stop %{c['stop_pct']} notional ${notional:.0f} | "
        f"tp1 +{TP1_R}R tp2 +{TP2_R}R | kom ${fee:.3f}")

def realize(tr, px, frac, reason, rate=TAKER_FEE):
    global equity,total_fees,peak_eq,trough_eq
    q=tr["qty"]*frac
    sgn=1 if tr["side"]=="LONG" else -1
    pnl=(px-tr["entry"])*q*sgn
    fee=q*px*rate; total_fees+=fee; equity+=pnl-fee
    peak_eq=max(peak_eq,equity); trough_eq=min(trough_eq,equity)
    tr["qty_left"]-=q; tr["gross_pnl"]+=pnl; tr["fee_close"]+=fee
    hold=(time.time()-tr["opened_ts"])/60
    tr["legs"].append({"exit":px,"pct":round(frac*100),"reason":reason,
                       "pnl":round(pnl,3),"fee":round(fee,3),"t":now(),
                       "hold_min":round(hold,1)})
    emo="✅" if pnl>=0 else "❌"
    log(f"   {emo} ÇIKIŞ #{tr['id']} {tr['symbol']:13} @ {px:<11g} %{round(frac*100)} "
        f"[{reason}] PnL ${pnl:+.3f} kom ${fee:.3f} | equity ${equity:.2f}")

def close_trade(tr):
    tr["closed"]=True; tr["closed_ts"]=time.time()
    tr["hold_min"]=round((tr["closed_ts"]-tr["opened_ts"])/60,1)
    tr["net"]=round(tr["gross_pnl"]-tr["fee_open"]-tr["fee_close"],3)
    tr["fee_total"]=round(tr["fee_open"]+tr["fee_close"],3)
    open_pos.pop(tr["symbol"],None)

def manage(tk):
    for sym in list(open_pos.keys()):
        tr=open_pos[sym]; px=tk.get(sym)
        if px is None: continue
        side=tr["side"]; R=tr["R"]; long=side=="LONG"
        tr["best"]=max(tr["best"],px) if long else min(tr["best"],px)
        hit=lambda lvl: (px<=lvl) if long else (px>=lvl)   # stop yönü
        up  =lambda lvl: (px>=lvl) if long else (px<=lvl)  # hedef yönü
        # 1) stop / trailing
        if not tr["tp1_done"]:
            if hit(tr["stop"]):
                realize(tr,tr["stop"],1.0,"stop"); close_trade(tr); continue
        else:
            trail=(tr["best"]-TRAIL_R*R) if long else (tr["best"]+TRAIL_R*R)
            tr["stop"]=max(tr["stop"],trail) if long else min(tr["stop"],trail)
            if hit(tr["stop"]):
                realize(tr,tr["stop"],tr["qty_left"]/tr["qty"],"trail"); close_trade(tr); continue
        # 2) hedefler
        if not tr["tp1_done"] and up(tr["tp1"]):
            realize(tr,tr["tp1"],TP1_F,"tp1",MAKER_FEE); tr["tp1_done"]=True
            tr["stop"]=tr["entry"]   # breakeven
            log(f"   ↳ #{tr['id']} {sym} tp1 → %{int(TP1_F*100)} alındı, stop breakeven")
        if tr["tp1_done"] and not tr["tp2_done"] and up(tr["tp2"]):
            realize(tr,tr["tp2"],TP2_F,"tp2",MAKER_FEE); tr["tp2_done"]=True
            log(f"   ↳ #{tr['id']} {sym} tp2 → %{int(TP2_F*100)} alındı, kalan trailing")
        # 3) zaman stop
        if time.time()-tr["opened_ts"]>MAX_HOLD_MIN*60 and tr["qty_left"]>1e-9:
            realize(tr,px,tr["qty_left"]/tr["qty"],"zaman"); close_trade(tr)

def try_enter():
    """Board'dan (panelle aynı) adayları çek, en iyi skorluları aç."""
    try:
        board=emastoch_board(TF)
    except Exception as e:
        log(f"⚠️ board hatası: {e}"); return
    cands=board.get("candidates",[])
    watch=[]
    for c in cands:
        if len(open_pos)>=MAX_POS: break
        s=c["symbol"]
        if s in open_pos: continue
        if time.time()-last_entry.get(s,0)<SYM_COOLDOWN: continue
        if c["score"]<MIN_SCORE:
            continue
        if c["score"]>=MAX_SCORE:
            watch.append(f"{s}(skor{c['score']}≥{MAX_SCORE}:kazanan-elendi)"); continue
        if REQUIRE_CROSS and c["cross"]=="none":
            continue                        # dönüş teyidi yok → düşen bıçak, atla
        # ── v3 filtreleri ──
        pc24 = float(c.get("pc24") or 0)
        if DEAD_COIN_PCT and pc24*100 <= -DEAD_COIN_PCT:
            watch.append(f"{s}(ölü 24s{pc24*100:+.1f}%)"); continue
        blk, why = news_guard(s, pc24)
        if blk:
            watch.append(f"{s}(olay: {why})"); continue
        ok, why = btc_ok(c["side"])
        if not ok:
            watch.append(f"{s}({c['side'][0]} {why})"); continue
        c = apply_stop_override(c)
        if not (STOP_MIN_PCT <= c["stop_pct"] <= STOP_MAX_PCT):
            watch.append(f"{s}(stop%{c['stop_pct']})"); continue
        open_trade(c)
    top=", ".join(f"{c['symbol']}:{c['side'][0]}{c['score']}" for c in cands[:5])
    if top:
        r1, r4 = btc_momentum()
        log(f"👀 board {board.get('count',0)} aday (L{board.get('longs',0)}/S{board.get('shorts',0)}) top: {top} | BTC 1s {r1 if r1 is None else round(r1,2)}% 4s {r4 if r4 is None else round(r4,2)}%")
    if watch:
        log(f"🚫 elenen: {', '.join(watch[:8])}{' …' if len(watch)>8 else ''}")

def write_report(final=False):
    done=[t for t in trades if t["closed"]]; wins=[t for t in done if t["net"]>0]
    net=equity-START_EQUITY
    L=[]; P=L.append
    P("═"*74)
    P(f"  EMA50+StochRSI PAPER BOT [{TAG}] — {'FİNAL' if final else 'ARA'} RAPOR ({now()})")
    P("═"*74)
    P(f"  TF {TF}dk | Başlangıç ${START_EQUITY:.2f} | Bitiş equity ${equity:.2f}")
    P(f"  ► NET ${net:+.2f} ({net/START_EQUITY*100:+.2f}%) | komisyon ${total_fees:.3f}")
    P(f"  Risk/işlem %{RISK_PCT*100:.1f} | pik ${peak_eq:.2f} dip ${trough_eq:.2f}")
    if done:
        gp=sum(t["net"] for t in wins); gl=sum(t["net"] for t in done if t["net"]<=0)
        pf=(gp/abs(gl)) if gl else float('inf')
        P(f"  Kapanan {len(done)} | ✅ {len(wins)} ❌ {len(done)-len(wins)} | "
          f"isabet %{100*len(wins)/len(done):.0f} | PF {pf:.2f} | açık {len(open_pos)}")
    else:
        P(f"  Kapanan 0 | açık {len(open_pos)}")
    P("═"*74)
    for t in sorted(trades,key=lambda x:x["id"]):
        st="KAPALI" if t["closed"] else "AÇIK"
        net_t=t.get("net",round(t["gross_pnl"]-t["fee_open"]-t["fee_close"],3))
        emo="✅" if net_t>0 else ("❌" if net_t<0 else "➖")
        a="LONG" if t["side"]=="LONG" else "SHORT"
        P(f"\n  #{t['id']} {emo} {a} {t['symbol']} [{st}]")
        P(f"     Giriş {t['entry']:g} @ {t['opened']} | K {t['k']} D {t['d']} RSI {t['rsi']} "
          f"ADX {t['adx']} {t['cross']} distEMA {t['dist_ema']:+.2f}% | skor {t['score']}")
        P(f"     Notional ${t['notional']:.0f} qty {t['qty']:.4g} | stop %{t['stop_pct']} atr %{t['atr_pct']}")
        for lg in t["legs"]:
            P(f"       → %{lg['pct']:>3} @ {lg['exit']:g} [{lg['reason']:8}] {lg['t']} "
              f"({lg['hold_min']}dk) PnL ${lg['pnl']:+.3f} kom ${lg['fee']:.3f}")
        if not t["legs"]: P("       → (henüz çıkış yok)")
        P(f"     ► NET ${net_t:+.3f}")
    P("\n"+"═"*74)
    txt="\n".join(L)
    with open(REP_PATH,"w") as f: f.write(txt)
    with open(LOG_PATH,"w") as f:
        json.dump({"tf":TF,"start_equity":START_EQUITY,"equity":equity,
                   "net":equity-START_EQUITY,"total_fees":total_fees,"trades":trades},
                  f, indent=2, default=str)
    return txt

# ───────── ANA DÖNGÜ ─────────
log(f"🤖 EMA50+StochRSI bot başladı — TF {TF}dk, ${START_EQUITY}, risk %{RISK_PCT*100:.1f}/işlem, "
    f"{DURATION_MIN}dk, max {MAX_POS} poz, skor {MIN_SCORE}–{MAX_SCORE} (yüksek skor=kazanan elenir), "
    f"cross-teyit={'AÇIK' if REQUIRE_CROSS else 'KAPALI'}, "
    f"zaman-stop {MAX_HOLD_MIN}dk | v3: BTC teyidi {BTC_CONFIRM}%, stop {STOP_ATR_MULT}×ATR, "
    f"ölü coin ≤-{DEAD_COIN_PCT}%, haber koruması {'AÇIK' if NEWS_GUARD else 'KAPALI'}"
    f"{' +CryptoPanic' if CRYPTOPANIC_TOKEN else ' (vekil: 24s%/hacim)'}")
loop=0
while time.time()<END_TS:
    loop+=1
    try:
        tk=tickers()
        manage(tk)
        try_enter()
        if loop%4==0:
            log(f"… döngü {loop} | equity ${equity:.2f} | açık {len(open_pos)} | "
                f"kapanan {len([t for t in trades if t['closed']])} | kom ${total_fees:.3f} | "
                f"kalan {int((END_TS-time.time())/60)}dk")
            write_report(False)
    except Exception as e:
        log(f"⚠️ hata: {e}")
    time.sleep(POLL_SEC)

log("⏹️ Süre doldu — açık pozisyonlar kapatılıyor…")
try:
    tk=tickers()
    for sym in list(open_pos.keys()):
        tr=open_pos[sym]; px=tk.get(sym,tr["entry"])
        realize(tr,px,tr["qty_left"]/tr["qty"],"süre-sonu"); close_trade(tr)
except Exception as e:
    log(f"⚠️ kapatma hatası: {e}")
report=write_report(True)
print("\n"+report,flush=True)
log(f"✅ Bitti. Rapor: {REP_PATH}")
