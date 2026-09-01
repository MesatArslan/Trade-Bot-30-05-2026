#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pro Trend PAPER-TRADING botu — 1 saatlik kagit ustunde al-sat (DETAYLI kayit).
- Sinyal: server.py -> pro_trend_board()  (Bybit MTF: 5dk trend + 1dk giris)
- Sanal bakiye 500 USDT, kaldirac 10x, canli Bybit fiyatlari
- Cikis: %60 tp1'de + kalan %40 tp2'de; tp1 sonrasi stop=girise (breakeven)
- Her pozisyon TEK trade olarak izlenir; kismi cikislar altinda toplanir
- Komisyon: her bacak taker; acilis + her kapanis bacagi ayri ayri kaydedilir
Cikti: konsol + protrend_bot_report.txt (detayli) + protrend_bot_trades.json
"""
import sys, os, time, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import pro_trend_board, _bybit_get, _BYBIT

# ───────── AYARLAR ─────────
START_EQUITY = 500.0
MARGIN_PCT   = 0.20      # islem basi teminat = equity'nin %20'si (kaldirac coin bazli dinamik)
MAX_POS      = 3
MIN_CONF     = 62
TAKER_FEE    = 0.00055   # Bybit taker %0.055 / bacak (market cikis: stop/trail/zaman)
MAKER_FEE    = 0.0002    # limit(maker) %0.02 / bacak (giris + tp1)
TP1_CLOSE    = 0.40      # tp1'de kalanin %40'i alinir, %60 trailing'e birakilir
DURATION_MIN = 60
POLL_SEC     = 20
MAX_HOLD_MIN = 40        # trend olgunlassin diye sabir (eski 15dk cok erken kesiyordu)
SYM_COOLDOWN = 8 * 60

# ── Cikis/kaldirac profili: "Maks. kar" (gecmis fiyat yollari simulasyonu +$45 / %82 win) ──
STOP_ATR_MULT = 2.5     # stop = 2.5 x 1dk ATR -> gurultunun disi (eski ~1.3x cok dardi)
STOP_MIN_PCT  = 0.0035  # stop en az %0.35
STOP_MAX_PCT  = 0.03    # stop en fazla %3.0
TP1_R_MULT    = 2.0     # tp1 = 2.0R -> burada %40 kilitle, stop breakeven
TRAIL_R_MULT  = 1.5     # kalan %60: en iyi noktadan 1.5R geri iz-suren stop (kosuyu yakala)
LEV_MIN       = 4       # dinamik kaldirac tabani
LEV_MAX       = 15      # dinamik kaldirac tavani
LEV_RISK_ROOM = 0.10    # stop = likidasyon mesafesinin (~1/lev) %10'u -> lev=0.10/stop_pct
                        #   sonuc: oynak coin(genis stop) dusuk kaldirac, sakin coin yuksek;
                        #   islem basi $ risk ~sabit (~%1-2 equity), likidasyon stoptan cok uzak

BASE = os.path.dirname(os.path.abspath(__file__))
REP_PATH = os.path.join(BASE, "protrend_bot_report.txt")
LOG_PATH = os.path.join(BASE, "protrend_bot_trades.json")
END_TS = time.time() + DURATION_MIN * 60

equity     = START_EQUITY
peak_eq    = START_EQUITY
trough_eq  = START_EQUITY
total_fees = 0.0
open_pos   = {}      # symbol -> trade dict (acik)
trades     = []      # tum trade'ler (acik + kapali), her biri legs[] icerir
last_entry = {}
_tid = 0

def now(): return dt.datetime.now().strftime("%H:%M:%S")
def log(m): print(f"[{now()}] {m}", flush=True)

def price_map():
    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    return {t["symbol"]: float(t["lastPrice"]) for t in tk if t.get("lastPrice")}

def dyn_leverage(stop_pct):
    """Coin volatilitesine gore kaldirac: stop genis(oynak coin) -> dusuk kaldirac,
    stop dar(sakin coin) -> yuksek kaldirac. Likidasyon her zaman stopun guvenli otesinde."""
    lev = LEV_RISK_ROOM / stop_pct if stop_pct > 0 else LEV_MAX
    return max(LEV_MIN, min(LEV_MAX, int(lev)))

def open_trade(sig):
    global equity, total_fees, _tid
    _tid += 1
    sym, side, entry = sig["symbol"], sig["side"], sig["price"]
    # ── Genis stop: 2.5 x 1dk ATR, gurultunun disinda (min %0.35, max %3.0) ──
    atr_abs   = (sig.get("atr_pct") or 0.3) / 100.0 * entry
    stop_dist = min(max(STOP_ATR_MULT * atr_abs, STOP_MIN_PCT * entry), STOP_MAX_PCT * entry)
    stop_pct  = stop_dist / entry
    lev       = dyn_leverage(stop_pct)                   # ← coin bazli dinamik kaldirac
    margin    = round(equity * MARGIN_PCT, 2)
    notional  = round(margin * lev, 2)
    qty       = notional / entry
    fee_open  = notional * MAKER_FEE                      # limit(maker) giris
    total_fees += fee_open
    equity   -= fee_open
    if side == "LONG":
        stop = entry - stop_dist
        tp1  = entry + TP1_R_MULT * stop_dist
        tp2  = entry + 4.0 * stop_dist                   # yalnizca referans gosterim (cikis trailing)
    else:
        stop = entry + stop_dist
        tp1  = entry - TP1_R_MULT * stop_dist
        tp2  = entry - 4.0 * stop_dist
    tr = {
        "id": _tid, "symbol": sym, "side": side, "entry": entry, "qty": qty,
        "margin": margin, "notional": notional, "leverage": lev,
        "stop0": stop, "stop": stop, "tp1": tp1, "tp2": tp2,
        "R": stop_dist, "peak": entry, "trail_mult": TRAIL_R_MULT,
        "stop_pct": round(stop_pct * 100, 2),
        "tp1_pct": round(TP1_R_MULT * stop_pct * 100, 2),
        "tp2_pct": round(4.0 * stop_pct * 100, 2),
        "conf": sig["conf"], "rr": 4.0, "adx1": sig.get("adx1"), "adx5": sig.get("adx5"),
        "atr_pct": sig.get("atr_pct"), "funding": sig.get("funding"), "pc24": sig.get("pc24"),
        "opened_ts": time.time(), "opened": now(),
        "fee_open": fee_open, "tp1_done": False, "closed": False,
        "qty_left": qty, "legs": [], "gross_pnl": 0.0, "fee_close": 0.0,
    }
    open_pos[sym] = tr
    trades.append(tr)
    last_entry[sym] = time.time()
    log(f"🟢 GİRİŞ #{_tid} {side:5} {sym:13} @ {entry:<11g} teminat ${margin:.0f}×{lev}x=${notional:.0f} "
        f"qty {qty:.4g} | stop {tr['stop_pct']}% tp1 {tr['tp1_pct']}%→%{int(TP1_CLOSE*100)} trail {TRAIL_R_MULT}R conf {sig['conf']} "
        f"adx1 {sig.get('adx1')} adx5 {sig.get('adx5')} atr {sig.get('atr_pct')}% | açılış(maker) kom ${fee_open:.3f}")

def realize(tr, exit_price, frac, reason, fee_rate=TAKER_FEE):
    """tr trade'inin (baslangic qty'sinin) frac kadarini kapatir. fee_rate: maker/taker."""
    global equity, total_fees, peak_eq, trough_eq
    qty_part = tr["qty"] * frac
    if tr["side"] == "LONG":
        pnl = (exit_price - tr["entry"]) * qty_part
    else:
        pnl = (tr["entry"] - exit_price) * qty_part
    fee_close = qty_part * exit_price * fee_rate
    total_fees += fee_close
    equity += pnl - fee_close
    peak_eq = max(peak_eq, equity); trough_eq = min(trough_eq, equity)
    tr["qty_left"] -= qty_part
    tr["gross_pnl"] += pnl
    tr["fee_close"] += fee_close
    hold = (time.time() - tr["opened_ts"]) / 60
    tr["legs"].append({
        "exit": exit_price, "pct": round(frac * 100), "reason": reason,
        "pnl": round(pnl, 3), "fee": round(fee_close, 3), "t": now(),
        "hold_min": round(hold, 1),
    })
    emo = "✅" if pnl >= 0 else "❌"
    log(f"   {emo} ÇIKIŞ #{tr['id']} {tr['symbol']:13} @ {exit_price:<11g} %{int(frac*100)} [{reason}] "
        f"PnL ${pnl:+.3f} kom ${fee_close:.3f} | equity ${equity:.2f}")

def close_trade(tr):
    tr["closed"] = True
    tr["closed_ts"] = time.time()
    tr["hold_min"] = round((tr["closed_ts"] - tr["opened_ts"]) / 60, 1)
    tr["net"] = round(tr["gross_pnl"] - tr["fee_open"] - tr["fee_close"], 3)
    tr["fee_total"] = round(tr["fee_open"] + tr["fee_close"], 3)
    del open_pos[tr["symbol"]]

def manage(prices):
    for sym in list(open_pos.keys()):
        tr = open_pos[sym]; px = prices.get(sym)
        if px is None: continue
        long = tr["side"] == "LONG"
        # lehte uc (peak) guncelle — trailing icin
        tr["peak"] = max(tr["peak"], px) if long else min(tr["peak"], px)

        if not tr["tp1_done"]:
            # 1) sabit ilk stop (2.5R genis)
            if (px <= tr["stop"]) if long else (px >= tr["stop"]):
                realize(tr, tr["stop"], 1.0, "stop"); close_trade(tr); continue
            # 2) tp1 (2R) -> %40 kilitle (maker), stop breakeven, kalan %60 trailing'e gecer
            if (px >= tr["tp1"]) if long else (px <= tr["tp1"]):
                realize(tr, tr["tp1"], TP1_CLOSE, "tp1", MAKER_FEE)
                tr["tp1_done"] = True; tr["stop"] = tr["entry"]
                log(f"   ↳ #{tr['id']} {sym} tp1→%{int(TP1_CLOSE*100)} alındı, stop breakeven, "
                    f"kalan %{int((1-TP1_CLOSE)*100)} {tr['trail_mult']}R iz-süren stop")
        else:
            # 3) iz-suren stop: en iyi noktadan trail_mult*R geri, asla geri gitmez (ratchet)
            trail = (tr["peak"] - tr["trail_mult"] * tr["R"]) if long else (tr["peak"] + tr["trail_mult"] * tr["R"])
            tr["stop"] = max(tr["stop"], trail) if long else min(tr["stop"], trail)
            if (px <= tr["stop"]) if long else (px >= tr["stop"]):
                realize(tr, tr["stop"], 1 - TP1_CLOSE, "trail"); close_trade(tr); continue
        # 4) zaman stopu (market/taker)
        if time.time() - tr["opened_ts"] > MAX_HOLD_MIN * 60:
            realize(tr, px, (1 - TP1_CLOSE) if tr["tp1_done"] else 1.0, "zaman")
            close_trade(tr)

def try_enter(board, prices):
    for sig in board.get("candidates", []):
        if len(open_pos) >= MAX_POS: break
        sym = sig["symbol"]
        if sig["conf"] < MIN_CONF or sym in open_pos: continue
        if time.time() - last_entry.get(sym, 0) < SYM_COOLDOWN: continue
        px = prices.get(sym, sig["price"])
        if abs(px / sig["price"] - 1) > 0.004: continue
        open_trade(sig)

def write_report(final=False):
    done = [t for t in trades if t["closed"]]
    wins = [t for t in done if t["net"] > 0]
    loss = [t for t in done if t["net"] <= 0]
    gross = sum(t["gross_pnl"] for t in done) + sum(t["gross_pnl"] for t in open_pos.values())
    net = equity - START_EQUITY
    L = []
    P = L.append
    P("═" * 70)
    P(f"  PRO TREND PAPER BOT — {'FİNAL' if final else 'ARA'} RAPOR   ({now()})")
    P("═" * 70)
    P(f"  Başlangıç bakiye : ${START_EQUITY:.2f}      Kaldıraç: dinamik {LEV_MIN}-{LEV_MAX}x (coin volatilitesine göre)")
    P(f"  Bitiş equity     : ${equity:.2f}")
    P(f"  ► NET SONUÇ      : ${net:+.2f}   ({net/START_EQUITY*100:+.2f}%)")
    P(f"  Brüt PnL (kom. öncesi): ${gross:+.2f}")
    P(f"  ► TOPLAM KOMİSYON: ${total_fees:.3f}   (giriş+tp1 maker %{MAKER_FEE*100:.3f} / stop+trail taker %{TAKER_FEE*100:.3f})")
    P(f"  Teminat/işlem: equity'nin %{int(MARGIN_PCT*100)}'i (~${START_EQUITY*MARGIN_PCT:.0f}) → notional = teminat × dinamik kaldıraç")
    P(f"  Çıkış: stop {STOP_ATR_MULT}×ATR | tp1 {TP1_R_MULT}R'de %{int(TP1_CLOSE*100)} | kalan {TRAIL_R_MULT}R iz-süren")
    P(f"  Pik equity ${peak_eq:.2f} | dip equity ${trough_eq:.2f} | max düşüş ${trough_eq-START_EQUITY:+.2f}")
    P("-" * 70)
    if done:
        wr = 100*len(wins)/len(done)
        avg_hold = sum(t["hold_min"] for t in done)/len(done)
        P(f"  Kapanan işlem: {len(done)}  |  ✅ kazanan {len(wins)}   ❌ kaybeden {len(loss)}   isabet %{wr:.0f}")
        P(f"  Ort. tutuş süresi: {avg_hold:.1f} dk   |   Açık kalan: {len(open_pos)}")
    else:
        P(f"  Kapanan işlem: 0   |   Açık: {len(open_pos)}")
    P("═" * 70)
    P("  İŞLEM DÖKÜMÜ (her pozisyon tek trade, kısmi çıkışlar altında)")
    P("═" * 70)
    for t in sorted(trades, key=lambda x: x["id"]):
        st = "KAPALI" if t["closed"] else "AÇIK"
        net_t = t.get("net", round(t["gross_pnl"] - t["fee_open"] - t["fee_close"], 3))
        pct_t = net_t / t["margin"] * 100  # teminata gore getiri
        emo = "✅" if net_t > 0 else ("❌" if net_t < 0 else "➖")
        P(f"\n  #{t['id']} {emo} {t['side']} {t['symbol']}  [{st}]")
        P(f"     Giriş  : {t['entry']:g}  @ {t['opened']}   (conf {t['conf']}, rr {t['rr']}, adx1 {t['adx1']}/adx5 {t['adx5']}, atr {t['atr_pct']}%)")
        P(f"     Boyut  : teminat ${t['margin']:.2f} × {t['leverage']}x = notional ${t['notional']:.2f}   (qty {t['qty']:.4g})")
        P(f"     Plan   : stop {t['stop0']:g} (%{t['stop_pct']}) {t['leverage']}x  tp1 {t['tp1']:g} (%{t['tp1_pct']})→%{int(TP1_CLOSE*100)}  kalan {t.get('trail_mult',TRAIL_R_MULT)}R iz-süren")
        for lg in t["legs"]:
            P(f"       → çıkış %{lg['pct']:>3} @ {lg['exit']:g}  [{lg['reason']:8}] {lg['t']} ({lg['hold_min']}dk)  PnL ${lg['pnl']:+.3f}  kom ${lg['fee']:.3f}")
        if not t["legs"]:
            P(f"       → (henüz çıkış yok)")
        P(f"     Komisyon: açılış ${t['fee_open']:.3f} + kapanış ${t['fee_close']:.3f} = ${t['fee_open']+t['fee_close']:.3f}")
        P(f"     ► Bu coin NET: ${net_t:+.3f}   (teminata göre %{pct_t:+.1f})")
    P("\n" + "═" * 70)
    txt = "\n".join(L)
    with open(REP_PATH, "w") as f: f.write(txt)
    with open(LOG_PATH, "w") as f:
        json.dump({"start_equity": START_EQUITY, "equity": equity, "net": net,
                   "total_fees": total_fees, "trades": trades}, f, indent=2, default=str)
    return txt

# ───────── ANA DÖNGÜ ─────────
log(f"🤖 Bot başladı — ${START_EQUITY}, dinamik {LEV_MIN}-{LEV_MAX}x, {DURATION_MIN}dk, Pro Trend conf≥{MIN_CONF}, max {MAX_POS} poz | stop {STOP_ATR_MULT}×ATR, tp1 {TP1_R_MULT}R %{int(TP1_CLOSE*100)}, trail {TRAIL_R_MULT}R")
loop = 0
while time.time() < END_TS:
    loop += 1
    try:
        prices = price_map()
        manage(prices)
        try_enter(pro_trend_board(), prices)
        if loop % 6 == 0:
            log(f"… döngü {loop} | equity ${equity:.2f} | açık {len(open_pos)} | kapanan {len([t for t in trades if t['closed']])} | kom ${total_fees:.3f} | kalan {int((END_TS-time.time())/60)}dk")
            write_report(False)
    except Exception as e:
        log(f"⚠️ hata: {e}")
    time.sleep(POLL_SEC)

log("⏹️ Süre doldu — açık pozisyonlar canlı fiyattan kapatılıyor…")
try:
    prices = price_map()
    for sym in list(open_pos.keys()):
        tr = open_pos[sym]; px = prices.get(sym, tr["entry"])
        realize(tr, px, (1 - TP1_CLOSE) if tr["tp1_done"] else 1.0, "süre-sonu")
        close_trade(tr)
except Exception as e:
    log(f"⚠️ kapatma hatası: {e}")

report = write_report(True)
print("\n" + report, flush=True)
log(f"✅ Bitti. Detaylı rapor: {REP_PATH}")
