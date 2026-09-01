"""
Funding Trader — ANA DOSYA.
Funding kesim saatlerinde bunu çalıştır.

Komutlar:
    ./.venv/bin/python trade.py schedule      # kesim saatleri + ne zaman çalıştırılır
    ./.venv/bin/python trade.py scan          # adayları tara ve listele
    ./.venv/bin/python trade.py plan          # tara + yön + tam işlem planı (kesimden SONRA)
    ./.venv/bin/python trade.py watch SEMBOL  # canlı giriş+çıkış asistanı (tek coin)
"""
import sys
import time
import datetime as dt

import mexc
import scan
import decide
import config as C

TR = dt.timezone(dt.timedelta(hours=3))   # Türkiye saati (UTC+3)


def _now_tr():
    return dt.datetime.now(TR)


def _fmt_ms(ms):
    if not ms:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000, TR).strftime("%H:%M:%S")


def _mins_until(ms):
    if not ms:
        return None
    return (ms / 1000 - time.time()) / 60.0


# ─── schedule ───────────────────────────────────────────────────
def cmd_schedule():
    now = _now_tr()
    print(f"\n  Şu an (TR): {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
    print("  8 saatlik pariteler için kesimler (Türkiye saati):")
    print("      03:00   ·   11:00   ·   19:00")
    print("  (4s ve 1s döngülü pariteler farklı — sistem her coinin")
    print("   kendi nextSettleTime'ını okur.)\n")

    # BTC üzerinden bir sonraki 8s kesimi göster
    det = mexc.funding_detail("BTC_USDT")
    if det and det["nextSettleTime"]:
        m = _mins_until(det["nextSettleTime"])
        print(f"  Sıradaki 8s kesim: {_fmt_ms(det['nextSettleTime'])} TR "
              f"(~{m:.0f} dk sonra)\n")
    print("  NE ZAMAN ÇALIŞTIR:")
    print("   • Kesimden ~30 dk ÖNCE:  python trade.py scan   (adayları gör)")
    print("   • Kesimden hemen SONRA:  python trade.py plan   (yön + plan)")
    print("   • Bir coin seçince:      python trade.py watch <SEMBOL>\n")


# ─── scan ───────────────────────────────────────────────────────
def cmd_scan():
    print(f"\n  [{_now_tr().strftime('%H:%M:%S')} TR] Tarama başlıyor...")
    res = scan.screen(verbose=True)
    scan.print_candidates(res["candidates"])
    if res["watchlist"]:
        print("  İzleme listesi (eşiği geçmedi, en yakınlar):")
        for w in res["watchlist"]:
            print(f"    {w['symbol']:<16}{w['crowd']:<7}z={w['z']:<6}"
                  f"fund={w['funding_norm']*100:+.3f}%  yakınlık={w['proximity']}"
                  f"  ({', '.join(w['fails'])})")
        print()
    return res["candidates"]


# ─── plan ───────────────────────────────────────────────────────
def cmd_plan():
    cands = cmd_scan()
    if not cands:
        return
    print("  Yön kararı (kesimden sonra çalışır) uygulanıyor...\n")
    any_trade = False
    for c in cands[:5]:
        d = decide.decide(c)
        if not d:
            print(f"  · {c['symbol']:<16} → çeyrek tradeable değil "
                  f"(kalabalık fiyatla çelişmiyor). Atla.")
            continue
        any_trade = True
        _print_plan_card(c, d)
    if not any_trade:
        print("\n  Hiçbir aday 'kalabalık yanılıyor' çeyreğinde değil.")
        print("  Bu iyi bir şey — kurulum yoksa işlem yok. Bir sonraki kesimi bekle.\n")


def _print_plan_card(c, d):
    # Provizyonel referans: son 5 dakikalık mumun H/L (canlı için `watch` kullan)
    k = mexc.klines(c["symbol"], minutes=6, interval="Min1")
    if len(k) >= 5:
        ref_high = max(x["h"] for x in k[-5:])
        ref_low = min(x["l"] for x in k[-5:])
    else:
        ref_high = d["last"] * 1.003
        ref_low = d["last"] * 0.997
    p = decide.position_plan(d, ref_high, ref_low)

    print("\n  ┌─────────────────────────────────────────────────────────────")
    print(f"  │  {c['symbol']}   →   {p['side']}   ({d['setup']})")
    print("  ├─────────────────────────────────────────────────────────────")
    print(f"  │  Neden: funding z={c['z']}, 8s getiri={d['ret_8h']*100:+.2f}%, "
          f"1s={d['ret_1h']*100:+.2f}%, ATR={d['atr_pct']*100:.2f}%")
    print(f"  │  Kalabalık: {c['crowd']} sıkışmış  |  basis={c['basis']*100:+.3f}%")
    print("  │")
    print(f"  │  Referans mum (PROVİZYONEL, son 5dk):  H={ref_high:.6g}  L={ref_low:.6g}")
    print(f"  │  Giriş (limit):   {p['entry']:.6g}")
    print(f"  │  Stop:            {p['stop']:.6g}   (mesafe %{p['stop_pct']*100:.2f})")
    print(f"  │  Hedef 1 (1R):    {p['tp1']:.6g}   → yarısını burada al")
    print(f"  │  Hedef 2 (2xATR): {p['tp2']:.6g}   → kalanı, stop'u girişe çek")
    print("  │")
    print(f"  │  Risk: {p['risk_usd']:.2f} USDT  ({C.RISK_PER_TRADE*100:.0f}% x {C.BALANCE_USDT:.0f})")
    print(f"  │  Pozisyon büyüklüğü:  {p['notional_usd']:.2f} USDT  "
          f"({p['qty_coin']:.6g} coin)")
    print(f"  │  Önerilen kaldıraç:   {p['suggested_leverage']:.0f}x  "
          f"(marj ~{p['margin_usd']:.2f} USDT)")
    print("  │")
    print(f"  │  ⏱  Canlı giriş/çıkış için:  python trade.py watch {c['symbol']}")
    print("  └─────────────────────────────────────────────────────────────\n")


# ─── watch (canlı giriş + çıkış asistanı) ──────────────────────
def cmd_watch(symbol):
    symbol = symbol.upper()
    if not symbol.endswith("_USDT"):
        symbol += "_USDT"

    print(f"\n  === CANLI ASİSTAN: {symbol} ===")
    # Önce bu coin gerçekten aday mı / yönü ne — tek coin için değerlendir
    det = mexc.funding_detail(symbol)
    if not det:
        print("  Bu sembol bulunamadı.")
        return
    st = mexc.funding_stats(symbol, pages=C.FUNDING_HIST_PAGES)
    snap = [r for r in mexc.snapshot() if r["symbol"] == symbol]
    if not snap or not st:
        print("  Yeterli veri yok.")
        return
    r = snap[0]
    fnorm = r["fundingRate"] * (8.0 / det["collectCycle"])
    z = (fnorm - st["mean_norm"]) / st["std_norm"]
    crowd = "LONG" if fnorm > 0 else "SHORT"
    cand = {"symbol": symbol, "crowd": crowd, "z": round(z, 2),
            "basis": r["basis"], "funding_norm": fnorm}
    d = decide.decide(cand)
    if not d:
        print(f"  {symbol}: şu an tradeable çeyrekte DEĞİL (kalabalık fiyatla "
              f"çelişmiyor). z={z:.1f}, crowd={crowd}. İşlem açma.")
        return

    print(f"  Yön: {d['side']}  ({d['setup']})   z={z:.1f}  ATR={d['atr_pct']*100:.2f}%")
    print(f"  Bir sonraki kesim: {_fmt_ms(det['nextSettleTime'])} TR\n")

    # ── 1) Referans mum: 5 dakika izle ──
    print("  [1/3] Referans mum oluşturuluyor — 5 dakika izleniyor...")
    print("        (Bunu kesimden hemen sonra başlatmalısın.)")
    t_start = time.time()
    ref_high, ref_low = None, None
    while time.time() - t_start < 5 * 60:
        k = mexc.klines(symbol, minutes=6, interval="Min1")
        if len(k) >= 5:
            ref_high = max(x["h"] for x in k[-5:])
            ref_low = min(x["l"] for x in k[-5:])
            price = k[-1]["c"]
            print(f"        H={ref_high:.6g}  L={ref_low:.6g}  şu an={price:.6g}", end="\r")
        time.sleep(20)
    print()
    if ref_high is None:
        print("  Referans mum oluşturulamadı, iptal.")
        return
    p = decide.position_plan(d, ref_high, ref_low)
    print(f"  Referans: H={ref_high:.6g}  L={ref_low:.6g}")
    print(f"  Tetik: {'H kır' if d['side']=='LONG' else 'L kır'} = "
          f"{p['entry']:.6g}\n")

    # ── 2) Tetik bekle (T+5..T+25) ──
    print("  [2/3] Kırılım tetiği bekleniyor (en fazla 20 dk)...")
    trigger_deadline = time.time() + C.ENTRY_WINDOW_MIN * 60
    entered = False
    while time.time() < trigger_deadline:
        k = mexc.klines(symbol, minutes=3, interval="Min1")
        if not k:
            time.sleep(15); continue
        last_close = k[-1]["c"]
        left = (trigger_deadline - time.time()) / 60
        if d["side"] == "LONG" and last_close > p["entry"]:
            entered = True; break
        if d["side"] == "SHORT" and last_close < p["entry"]:
            entered = True; break
        print(f"        şu an={last_close:.6g}  tetik={p['entry']:.6g}  "
              f"kalan={left:.0f}dk", end="\r")
        time.sleep(20)
    print()
    if not entered:
        print("  ⏹  25 dk içinde tetik gelmedi → OLAY İPTAL. İşlem açma.")
        print("      Sıkışma senaryosu bu sürede başlamadıysa yoktur.\n")
        return

    # ── GİRİŞ SİNYALİ ──
    print("\n  ╔══════════════════════════════════════════════════╗")
    print(f"  ║  GİRİŞ SİNYALİ:  {d['side']}  {symbol}")
    print(f"  ║  Limit giriş:  {p['entry']:.6g}  (geri çekilişte doldur)")
    print(f"  ║  Stop:         {p['stop']:.6g}  (%{p['stop_pct']*100:.2f})")
    print(f"  ║  TP1 (yarı):   {p['tp1']:.6g}")
    print(f"  ║  TP2:          {p['tp2']:.6g}")
    print(f"  ║  Boyut:        {p['notional_usd']:.2f} USDT  @ "
          f"{p['suggested_leverage']:.0f}x")
    print("  ╚══════════════════════════════════════════════════╝\n")
    input("  Pozisyonu açtıysan ENTER'a bas — çıkış izlemesi başlasın... ")

    # ── 3) Çıkış izleme: OI durması + zaman stopu ──
    print("\n  [3/3] Çıkış izleniyor: OI durması + T+90dk zaman stopu")
    oi0 = mexc.current_oi_usd(symbol)
    oi_peak = oi0 or 0
    entry_time = time.time()
    stall_ticks = 0
    while True:
        elapsed = (time.time() - entry_time) / 60
        if elapsed > C.TIME_STOP_MIN:
            print("\n  ⏹  ZAMAN STOPU (T+90dk). KOŞULSUZ KAPAT. Olay bitti.\n")
            return
        oi = mexc.current_oi_usd(symbol)
        price = None
        k = mexc.klines(symbol, minutes=2, interval="Min1")
        if k:
            price = k[-1]["c"]
        if oi is not None:
            if oi > oi_peak:
                oi_peak = oi
            # OI tepeden düşüp sonra düşmeyi bıraktıysa yakıt bitmiş demek
            drop = (oi_peak - oi) / oi_peak if oi_peak else 0
            if drop > 0.02:   # OI tepeden %2 düştü = squeeze işliyor
                stall_ticks += 1
            print(f"        t+{elapsed:>4.0f}dk  fiyat={price if price else '—':<10}  "
                  f"OI=${oi/1e6:.1f}M  tepeden düşüş=%{drop*100:.1f}  ", end="\r")
            if stall_ticks >= 3 and drop > 0.02:
                print("\n  🟡  OI tepeden anlamlı düştü ve stabilize oluyor → "
                      "yakıt azalıyor. KÂR AL / pozisyonu küçült.\n")
                return
        time.sleep(30)


# ─── main ───────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "schedule"
    if cmd == "schedule":
        cmd_schedule()
    elif cmd == "scan":
        cmd_scan()
    elif cmd == "plan":
        cmd_plan()
    elif cmd == "watch":
        if len(args) < 2:
            print("Kullanım: python trade.py watch <SEMBOL>")
            return
        cmd_watch(args[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
