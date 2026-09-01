#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pro Trend CANLI bot — MEXC'te otomatik AÇ/KAPAT.

Sinyal + fiyat: mexc_trend.board()  (MEXC-native, Bybit değil)
Emir:           mexc_trade  (imzalı özel API; VARSAYILAN DRY-RUN → emir gitmez)
Çıkış yönetimi: TP1'de %60 kapat + stop'u breakeven'a al → kalan %40 TP2'de,
                aksi halde stop / zaman-stop. (protrend_bot.py ile aynı mantık)

⚠️  GERÇEK PARA: .env içinde MEXC_DRY_RUN=0 yapılırsa canlı emir gönderir.
    Önce DRY-RUN'da izle, planları doğrula, sonra canlıya geç.

Çıktı: konsol + protrend_live.log (dosyaya) + protrend_live_trades.json
"""
import sys, os, time, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mexc
import mexc_trade as mt
from mexc_trend import board

# ───────── AYARLAR ─────────
MARGIN_USDT  = 5.0       # işlem başına GERÇEK teminat (USDT) — küçük başla
LEVERAGE     = 10        # kaldıraç (izole)
MAX_POS      = 3         # aynı anda en fazla pozisyon
MIN_CONF     = 62        # bu güvenin altındaki adayı alma
TAKER_FEE    = 0.0002    # MEXC contract taker %0.02 / bacak
TP1_CLOSE    = 0.60      # TP1'de kapatılan oran
DURATION_MIN = 60        # botun toplam çalışma süresi (dk)
POLL_SEC     = 20        # döngü periyodu
MAX_HOLD_MIN = 15        # pozisyon zaman-stopu (dk)
SYM_COOLDOWN = 8 * 60    # aynı sembole tekrar girişte bekleme (sn)
START_EQUITY = 500.0     # rapor istatistiği için sanal başlangıç (gerçek risk = MARGIN×MAX_POS)

BASE = os.path.dirname(os.path.abspath(__file__))
REP_PATH = os.path.join(BASE, "protrend_live_report.txt")
LOG_PATH = os.path.join(BASE, "protrend_live_trades.json")
END_TS = time.time() + DURATION_MIN * 60

equity     = START_EQUITY
peak_eq    = START_EQUITY
trough_eq  = START_EQUITY
total_fees = 0.0
open_pos   = {}      # symbol -> trade dict
trades     = []
last_entry = {}
_tid = 0

def now(): return dt.datetime.now().strftime("%H:%M:%S")
def log(m): print(f"[{now()}] {m}", flush=True)

def price_map():
    """symbol -> son fiyat (MEXC snapshot, tek çağrı)."""
    return {r["symbol"]: r["lastPrice"] for r in mexc.snapshot()}

def _explain(res):
    """mexc_trade sonucunu tek satırda özetle (dry plan ya da canlı sonuç)."""
    if res.get("dry_run"):
        p = res["plan"]["params"]
        extra = ""
        if "stopLossPrice" in p: extra += f" SL {p['stopLossPrice']:g}"
        if "takeProfitPrice" in p: extra += f" TP {p['takeProfitPrice']:g}"
        return f"[DRY] {res['plan']['label']}  side={p['side']} type={p['type']}{extra}"
    if res.get("success"):
        return f"[CANLI ✓] emir id {res.get('data')}"
    return f"[CANLI ✗] hata: {res.get('error')} {res.get('message')}"

def open_trade(sig):
    global equity, total_fees, _tid
    sym, side, entry = sig["symbol"], sig["side"], sig["price"]

    # 1) kaldıraç ayarla (izole)
    lr = mt.set_leverage(sym, LEVERAGE, mt.OPEN_ISOLATED)
    # 2) piyasa emriyle aç + koruyucu SL/TP (borsa tarafı emniyet ağı)
    res = mt.open_market(sym, side, MARGIN_USDT, LEVERAGE, entry,
                         stop_price=sig["stop"], tp_price=sig["tp2"],
                         open_type=mt.OPEN_ISOLATED)
    if not res.get("success"):
        log(f"⛔ AÇ başarısız {sym}: {_explain(res)} | sizing {res.get('sizing')}")
        return
    vol = res.get("vol", 0)
    szinfo = res.get("sizing", {})
    real_margin = szinfo.get("real_margin", MARGIN_USDT)
    notional = szinfo.get("real_notional", MARGIN_USDT * LEVERAGE)
    qty = szinfo.get("coin", notional / entry if entry else 0)
    fee_open = notional * TAKER_FEE
    total_fees += fee_open
    equity   -= fee_open

    _tid += 1
    tr = {
        "id": _tid, "symbol": sym, "side": side, "entry": entry,
        "vol": vol, "vol_left": vol, "qty": qty,
        "margin": real_margin, "notional": notional, "leverage": LEVERAGE,
        "stop0": sig["stop"], "stop": sig["stop"], "tp1": sig["tp1"], "tp2": sig["tp2"],
        "stop_pct": sig["stop_pct"], "tp1_pct": sig["tp1_pct"], "tp2_pct": sig["tp2_pct"],
        "conf": sig["conf"], "rr": sig["rr"], "adx1": sig.get("adx1"), "adx5": sig.get("adx5"),
        "atr_pct": sig.get("atr_pct"), "funding": sig.get("funding"), "pc24": sig.get("pc24"),
        "opened_ts": time.time(), "opened": now(),
        "fee_open": fee_open, "tp1_done": False, "closed": False,
        "qty_left": qty, "legs": [], "gross_pnl": 0.0, "fee_close": 0.0,
    }
    open_pos[sym] = tr
    trades.append(tr)
    last_entry[sym] = time.time()
    bumped = " ⚠️minVol'e yükseltildi" if szinfo.get("bumped_to_min") else ""
    log(f"🟢 GİRİŞ #{_tid} {side:5} {sym:14} @ {entry:<11g} {vol} kontrat "
        f"(~${notional:.0f} notional, teminat ${real_margin:.2f}){bumped}")
    log(f"      {_explain(res)}")

def realize(tr, exit_price, frac, reason, final_leg=False):
    """tr'nin başlangıç vol'ünün frac kadarını kapat (kontrat bazlı)."""
    global equity, total_fees, peak_eq, trough_eq
    if final_leg:
        vol_part = tr["vol_left"]
    else:
        vol_part = int(round(tr["vol"] * frac))
        vol_part = min(vol_part, tr["vol_left"])
    if vol_part <= 0:
        return
    # gerçek emir (dry-run'da loglanır)
    res = mt.close_market(tr["symbol"], tr["side"], vol_part, exit_price)

    cs = mt._meta(tr["symbol"])["cs"] if mt._meta(tr["symbol"]) else 0
    coin_part = vol_part * cs
    if tr["side"] == "LONG":
        pnl = (exit_price - tr["entry"]) * coin_part
    else:
        pnl = (tr["entry"] - exit_price) * coin_part
    fee_close = coin_part * exit_price * TAKER_FEE
    total_fees += fee_close
    equity += pnl - fee_close
    peak_eq = max(peak_eq, equity); trough_eq = min(trough_eq, equity)
    tr["vol_left"] -= vol_part
    tr["qty_left"] -= coin_part
    tr["gross_pnl"] += pnl
    tr["fee_close"] += fee_close
    hold = (time.time() - tr["opened_ts"]) / 60
    tr["legs"].append({
        "exit": exit_price, "pct": round(frac * 100), "vol": vol_part, "reason": reason,
        "pnl": round(pnl, 3), "fee": round(fee_close, 3), "t": now(),
        "hold_min": round(hold, 1),
    })
    emo = "✅" if pnl >= 0 else "❌"
    log(f"   {emo} ÇIKIŞ #{tr['id']} {tr['symbol']:14} @ {exit_price:<11g} {vol_part} kontrat [{reason}] "
        f"PnL ${pnl:+.3f} kom ${fee_close:.3f} | equity ${equity:.2f}")
    log(f"      {_explain(res)}")

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
        if (px <= tr["stop"]) if long else (px >= tr["stop"]):
            realize(tr, tr["stop"], (1 - TP1_CLOSE) if tr["tp1_done"] else 1.0,
                    "BE-stop" if tr["tp1_done"] else "stop", final_leg=True)
            close_trade(tr); continue
        if not tr["tp1_done"] and ((px >= tr["tp1"]) if long else (px <= tr["tp1"])):
            realize(tr, tr["tp1"], TP1_CLOSE, "tp1")
            tr["tp1_done"] = True; tr["stop"] = tr["entry"]
            log(f"   ↳ #{tr['id']} {sym} stop→breakeven, kalan %{int((1-TP1_CLOSE)*100)} tp2 bekliyor")
        if tr["tp1_done"] and ((px >= tr["tp2"]) if long else (px <= tr["tp2"])):
            realize(tr, tr["tp2"], 1 - TP1_CLOSE, "tp2", final_leg=True)
            close_trade(tr); continue
        if time.time() - tr["opened_ts"] > MAX_HOLD_MIN * 60:
            realize(tr, px, (1 - TP1_CLOSE) if tr["tp1_done"] else 1.0, "zaman", final_leg=True)
            close_trade(tr)

def try_enter(bd, prices):
    for sig in bd.get("candidates", []):
        if len(open_pos) >= MAX_POS: break
        sym = sig["symbol"]
        if sig["conf"] < MIN_CONF or sym in open_pos: continue
        if time.time() - last_entry.get(sym, 0) < SYM_COOLDOWN: continue
        px = prices.get(sym, sig["price"])
        if abs(px / sig["price"] - 1) > 0.004: continue   # fiyat kaydıysa atla
        open_trade(sig)

def write_report(final=False):
    done = [t for t in trades if t["closed"]]
    wins = [t for t in done if t["net"] > 0]
    loss = [t for t in done if t["net"] <= 0]
    gross = sum(t["gross_pnl"] for t in done) + sum(t["gross_pnl"] for t in open_pos.values())
    net = equity - START_EQUITY
    L = []; P = L.append
    mode = "CANLI" if not mt.DRY_RUN else "DRY-RUN"
    P("═" * 72)
    P(f"  PRO TREND CANLI BOT ({mode}) — {'FİNAL' if final else 'ARA'} RAPOR   ({now()})")
    P("═" * 72)
    P(f"  {mt.status_line()}")
    P(f"  Teminat/işlem: ${MARGIN_USDT:.2f} × {LEVERAGE}x  |  max {MAX_POS} poz  "
      f"→ azami gerçek risk ~${MARGIN_USDT*MAX_POS:.0f}")
    P(f"  Sanal equity (istatistik): ${equity:.2f}   ► net ${net:+.2f} ({net/START_EQUITY*100:+.2f}%)")
    P(f"  Brüt PnL: ${gross:+.2f}   |   Toplam komisyon: ${total_fees:.3f} (%{TAKER_FEE*100:.3f}/bacak)")
    P("-" * 72)
    if done:
        wr = 100*len(wins)/len(done)
        avg_hold = sum(t["hold_min"] for t in done)/len(done)
        P(f"  Kapanan: {len(done)}  ✅{len(wins)} ❌{len(loss)}  isabet %{wr:.0f}  "
          f"ort. tutuş {avg_hold:.1f}dk  |  açık {len(open_pos)}")
    else:
        P(f"  Kapanan işlem: 0   |   Açık: {len(open_pos)}")
    P("═" * 72)
    for t in sorted(trades, key=lambda x: x["id"]):
        st = "KAPALI" if t["closed"] else "AÇIK"
        net_t = t.get("net", round(t["gross_pnl"] - t["fee_open"] - t["fee_close"], 3))
        emo = "✅" if net_t > 0 else ("❌" if net_t < 0 else "➖")
        P(f"\n  #{t['id']} {emo} {t['side']} {t['symbol']} [{st}]  {t['vol']} kontrat")
        P(f"     Giriş {t['entry']:g} @ {t['opened']} (conf {t['conf']}, rr {t['rr']}, "
          f"adx1 {t['adx1']}/adx5 {t['adx5']}, atr {t['atr_pct']}%)")
        P(f"     Plan  stop {t['stop0']:g} (%{t['stop_pct']})  tp1 {t['tp1']:g} (%{t['tp1_pct']})  "
          f"tp2 {t['tp2']:g} (%{t['tp2_pct']})")
        for lg in t["legs"]:
            P(f"       → %{lg['pct']:>3} {lg['vol']}kont @ {lg['exit']:g} [{lg['reason']:8}] "
              f"{lg['t']} ({lg['hold_min']}dk) PnL ${lg['pnl']:+.3f} kom ${lg['fee']:.3f}")
        if not t["legs"]:
            P(f"       → (henüz çıkış yok)")
        P(f"     ► NET ${net_t:+.3f}  (komisyon ${t['fee_open']+t['fee_close']:.3f})")
    P("\n" + "═" * 72)
    txt = "\n".join(L)
    with open(REP_PATH, "w") as f: f.write(txt)
    with open(LOG_PATH, "w") as f:
        json.dump({"mode": mode, "start_equity": START_EQUITY, "equity": equity,
                   "net": net, "total_fees": total_fees, "trades": trades},
                  f, indent=2, default=str)
    return txt

# ───────── ANA DÖNGÜ ─────────
log(f"🤖 CANLI bot başladı — {mt.status_line()}")
log(f"   Teminat ${MARGIN_USDT}×{LEVERAGE}x, max {MAX_POS} poz, conf≥{MIN_CONF}, {DURATION_MIN}dk")
if mt.DRY_RUN:
    log("   🧪 DRY-RUN: hiçbir gerçek emir gönderilmez, sadece planlar loglanır.")
else:
    log("   🟢 CANLI MOD: GERÇEK emirler gönderilecek!")

loop = 0
while time.time() < END_TS:
    loop += 1
    try:
        prices = price_map()
        manage(prices)
        try_enter(board(), prices)
        if loop % 6 == 0:
            log(f"… döngü {loop} | equity ${equity:.2f} | açık {len(open_pos)} | "
                f"kapanan {len([t for t in trades if t['closed']])} | kom ${total_fees:.3f} | "
                f"kalan {int((END_TS-time.time())/60)}dk")
            write_report(False)
    except Exception as e:
        log(f"⚠️ hata: {e}")
    time.sleep(POLL_SEC)

log("⏹️ Süre doldu — açık pozisyonlar kapatılıyor…")
try:
    prices = price_map()
    for sym in list(open_pos.keys()):
        tr = open_pos[sym]; px = prices.get(sym, tr["entry"])
        realize(tr, px, (1 - TP1_CLOSE) if tr["tp1_done"] else 1.0, "süre-sonu", final_leg=True)
        close_trade(tr)
except Exception as e:
    log(f"⚠️ kapatma hatası: {e}")

report = write_report(True)
print("\n" + report, flush=True)
log(f"✅ Bitti. Rapor: {REP_PATH}")
