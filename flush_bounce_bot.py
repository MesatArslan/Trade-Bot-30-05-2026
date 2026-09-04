#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FLUSH BOUNCE PAPER BOT — 2026-09-03 araştırmasında 180 günde doğrulanan tek kural.
Mekanik: piyasa geneli çöküş (≥13 coin |1s|≥%2 ve BTC 1s ≤ −%0.5) içinde, hacimle (1s turnover ≥ 5× önceki 24s
saatlik ort.) ≥%3 düşen coin = zorunlu satış/likidasyon; çöküş OLGUNLAŞMIŞSA (son 60dk'da ≥4 böyle olay zaten
tetiklenmişse) 15dk yukarı teyitle LONG, stop 3×ATR14(5dk), 4 SAAT sonra sabit çıkış. Trend değil, sıçrama.
Backtest (180g, 116 coin): n=278, hit %71, avgR +0.33, PF 3.2, 6/6 ay artı; plasebo (aynı çöküş, olaysız coin) −0.09.
$500 · %1.5 risk · max 5 eşzamanlı → 180g +$183, maxDD %8. Uyarı: olaylar çöküş günlerinde kümelenir (180 günün
79'unda olay var); Temmuz–Ağustos zayıf (+0.11/+0.05R). Paper: sanal bakiye, canlı Bybit fiyatı. Emir GÖNDERMEZ.
Çalıştırma: ./.venv/bin/python flush_bounce_bot.py [süre_dk]   (varsayılan 1440)
"""
import sys, os, time, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import _bybit_get, _BYBIT, _bybit_kl
from concurrent.futures import ThreadPoolExecutor

DURATION_MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
START_EQ = 500.0; RISK = 0.015; MAX_POS = 5; SLIP = 0.0005
TAKER = 0.00055; MIN_TOV = 5_000_000; UNIV_N = 120
DROP = -0.03; VOLX = 5.0; BREADTH_MIN = 13; BTC_DROP = -0.005; MATURE = 4; STOP_ATR = 3.0; HOLD_MIN = 240
BASE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(BASE, "flush_bounce_report.txt"); LOG = os.path.join(BASE, "flush_bounce_trades.json")
def now(): return dt.datetime.now().strftime("%m-%d %H:%M:%S")
def log(m): print(f"[{now()}] {m}", flush=True)

def universe():
    tk = _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
    liq = [(t["symbol"], float(t["turnover24h"])) for t in tk if t["symbol"].endswith("USDT") and float(t.get("turnover24h") or 0) >= MIN_TOV]
    liq.sort(key=lambda x: -x[1]); syms = [s for s, _ in liq[:UNIV_N]]
    if "BTCUSDT" not in syms: syms.append("BTCUSDT")
    return syms

def klines(sym):
    try:
        k = _bybit_kl(sym, "5", 320)
        return sym, [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6])) for r in k][:-1]  # son mum kapanmamış → at
    except Exception: return sym, None

def scan(syms):
    """Kapanmış son mumda: her coin için 1s getiri, 1s hacim oranı, ATR14; breadth ve BTC."""
    data = {}
    with ThreadPoolExecutor(8) as ex:
        for s, b in ex.map(klines, syms):
            if b and len(b) >= 300: data[s] = b
    feats = {}
    for s, b in data.items():
        c = b[-1][4]; r1 = c/b[-13][4]-1
        v1 = sum(x[6] for x in b[-12:]); v24 = sum(x[6] for x in b[-300:-12])/24
        atr = sum(x[2]-x[3] for x in b[-14:])/14
        feats[s] = dict(ts=b[-1][0], c=c, r1=r1, volx=(v1/v24 if v24 > 0 else 0), atr=atr, c3ago=b[-4][4])
    breadth = sum(1 for f in feats.values() if abs(f["r1"]) >= 0.02)
    btc = feats.get("BTCUSDT", {}).get("r1", 0)
    return feats, breadth, btc

eq = START_EQ; open_pos = {}; trades = []; events = []; pending = {}; tid = 0; last_bar = None
def write():
    done = [t for t in trades if t["closed"]]; w = [t for t in done if t["net"] > 0]
    L = ["═"*70, f"  FLUSH BOUNCE PAPER BOT — {now()}", "═"*70,
         f"  Başlangıç ${START_EQ:.0f} · equity ${eq:.2f} · NET ${eq-START_EQ:+.2f} · kapanan {len(done)} · ✅{len(w)} ❌{len(done)-len(w)} · açık {len(open_pos)} · olay(60dk) {len([e for e in events if time.time()-e[0] < 3600])}"]
    for t in trades: L.append(f"  #{t['id']} {t['sym']} giriş {t['entry']:g} @ {t['opened']} stop {t['stop']:g} ({t['stop_pct']:.2f}%) → {t.get('exit','-')} [{t.get('reason','AÇIK')}] net ${t.get('net',0):+.2f}")
    open(REP, "w").write("\n".join(L)); json.dump({"equity": eq, "trades": trades}, open(LOG, "w"), indent=1, default=str)

log(f"🤖 Flush bounce bot: $500 paper, risk %{RISK*100:g}, max {MAX_POS}, {DURATION_MIN} dk. Şart: breadth≥{BREADTH_MIN} & BTC1s≤{BTC_DROP*100:g}% & coin 1s≤{DROP*100:g}% & hacim≥{VOLX}× & olgun≥{MATURE}")
END = time.time()+DURATION_MIN*60; syms = universe(); last_univ = time.time()
while time.time() < END:
    try:
        if time.time()-last_univ > 3600: syms = universe(); last_univ = time.time()
        feats, breadth, btc = scan(syms)
        bar = max(f["ts"] for f in feats.values()) if feats else None
        # 1) açık pozisyonları yönet (kapanmış mum ile: stop veya 4s)
        tk = {t["symbol"]: float(t["lastPrice"]) for t in _bybit_get(f"{_BYBIT}/v5/market/tickers?category=linear")["result"]["list"] if t["symbol"] in open_pos}
        for s in list(open_pos):
            t = open_pos[s]; px = tk.get(s)
            if px is None: continue
            reason = None
            if px <= t["stop"]: reason, xpx = "stop", t["stop"]
            elif time.time()-t["opened_ts"] >= HOLD_MIN*60: reason, xpx = "4s", px
            if reason:
                pnl = (xpx-t["entry"])*t["qty"]; fee = t["qty"]*xpx*TAKER; eq += pnl-fee
                t.update(closed=True, exit=xpx, reason=reason, net=round(pnl-fee-t["fee_open"], 3), closed_ts=time.time())
                open_pos.pop(s); log(f"   {'✅' if t['net']>0 else '❌'} ÇIKIŞ #{t['id']} {s} @ {xpx:g} [{reason}] net ${t['net']:+.2f} · equity ${eq:.2f}")
        if bar and bar != last_bar:
            last_bar = bar; flush = breadth >= BREADTH_MIN and btc <= BTC_DROP
            # 2) yeni olaylar
            for s, f in feats.items():
                if s == "BTCUSDT" or f["r1"] > DROP or f["volx"] < VOLX: continue
                if any(e[1] == s and time.time()-e[0] < 4*3600 for e in events): continue
                events.append((time.time(), s, f["c"], flush, bar))
                if flush: pending[s] = (bar, f["c"]); log(f"⚡ olay {s} 1s {f['r1']*100:+.1f}% hacim {f['volx']:.1f}× | breadth {breadth} BTC {btc*100:+.2f}% | flush={'EVET' if flush else 'hayır'} olgun={len([e for e in events if time.time()-e[0]<3600 and e[3]])}")
            # 3) teyit + giriş (olaydan 3 mum sonra, kapanış olay kapanışının üstünde, olgunluk)
            mature = len([e for e in events if time.time()-e[0] < 3600 and e[3]])
            for s, (ebar, ec) in list(pending.items()):
                if bar-ebar < 3*300000: continue
                pending.pop(s); f = feats.get(s)
                if not f or f["c"] <= ec: log(f"   ✗ {s} teyit yok"); continue
                if mature < MATURE: log(f"   ✗ {s} olgun değil ({mature}<{MATURE})"); continue
                if s in open_pos or len(open_pos) >= MAX_POS: continue
                entry = f["c"]*(1+SLIP); stop = entry-STOP_ATR*f["atr"]; spct = (entry-stop)/entry*100
                if spct > 15 or spct < 0.3: continue
                qty = eq*RISK/(entry-stop); fee = qty*entry*TAKER; eq -= fee; tid += 1
                open_pos[s] = dict(id=tid, sym=s, entry=entry, stop=stop, stop_pct=spct, qty=qty, fee_open=fee, opened_ts=time.time(), opened=now(), closed=False)
                trades.append(open_pos[s]); log(f"🟢 LONG #{tid} {s} @ {entry:g} stop {stop:g} (%{spct:.2f}) risk ${eq*RISK:.2f} | olgun {mature} breadth {breadth}")
            log(f"… bar {dt.datetime.fromtimestamp(bar/1000):%H:%M} · breadth {breadth} · BTC1s {btc*100:+.2f}% · flush={'EVET' if flush else 'hayır'} · olay60dk {mature} · açık {len(open_pos)} · equity ${eq:.2f}")
            write()
    except Exception as e:
        log(f"⚠️ {e}")
    time.sleep(30)
for s in list(open_pos):
    t = open_pos.pop(s); px = t["entry"]; t.update(closed=True, exit=px, reason="süre-sonu", net=round(-t["fee_open"], 3))
write(); log(f"⏹ bitti · equity ${eq:.2f} · rapor {REP}")
