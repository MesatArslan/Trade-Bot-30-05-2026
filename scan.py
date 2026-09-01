"""
Tarama hunisi: 1000+ pariteyi 4 aşamadan geçirip 1-3 aday bırakır.
Aşama 0 (evren) ve 1-2 (funding aşırılığı + basis) burada.
Aşama 3 (yön) decide.py'de.
"""
import math
from concurrent.futures import ThreadPoolExecutor
import mexc
import binance
import config as C


def _sign(x):
    return (x > 0) - (x < 0)


def screen(verbose=True):
    """
    Aday listesi döner (skora göre sıralı). Her aday bir dict.
    crowd = 'LONG'  -> longlar kalabalık (funding pozitif) -> muhtemel SHORT kurulumu
    crowd = 'SHORT' -> shortlar kalabalık (funding negatif) -> muhtemel LONG kurulumu
    """
    snap = mexc.snapshot()
    binance.funding_map()   # Binance funding cache'ini ısıt (çapraz teyit)
    if verbose:
        print(f"  · {len(snap)} USDT perp tarandı")

    # ── Aşama 0: evren filtresi ──
    universe = [
        r for r in snap
        if r["amount24"] > C.MIN_VOL24_USDT
        and r["oi_usd"] > C.MIN_OI_USDT
        and r["spread"] < C.MAX_SPREAD
        and r["lastPrice"] > C.MIN_PRICE
    ]
    if verbose:
        print(f"  · {len(universe)} tanesi evren filtresini geçti "
              f"(hacim/OI/spread/fiyat)")

    # Ham funding'e göre ön eleme (funding geçmişi çağrısını azaltmak için)
    universe.sort(key=lambda r: abs(r["fundingRate"]), reverse=True)
    universe = universe[:C.PRESCREEN_TOP]

    # Funding geçmişini PARALEL çek (en yavaş adım buydu) — cycle de buradan gelir
    def _fetch(r):
        return r, mexc.funding_stats(r["symbol"], pages=C.FUNDING_HIST_PAGES)
    with ThreadPoolExecutor(max_workers=C.SCAN_WORKERS) as ex:
        results = list(ex.map(_fetch, universe))
    if verbose:
        print(f"  · {len(results)} coinin funding geçmişi çekildi")

    # bir sonraki kesim (tek çağrı, tüm coinler için ortak gösterim yeterli)
    _det = mexc.funding_detail("BTC_USDT")
    next_settle = _det["nextSettleTime"] if _det else 0

    candidates = []
    watchlist = []
    for r, st in results:
        if not st or st["n"] < C.MIN_FUNDING_HIST:
            continue
        cycle = st.get("cycle", 8)

        fnorm = r["fundingRate"] * (8.0 / cycle)          # 8s'e normalize
        z = (fnorm - st["mean_norm"]) / st["std_norm"]

        # Her değerlendirilen coini izleme listesine ekle (eşiği geçmese de)
        fails = []
        if abs(z) < C.Z_MIN:
            fails.append(f"z {abs(z):.1f}/{C.Z_MIN:.0f}")
        if abs(fnorm) < C.FUNDING_ABS_MIN:
            fails.append(f"funding {abs(fnorm)*100:.2f}/{C.FUNDING_ABS_MIN*100:.2f}%")
        if abs(r["basis"]) < C.BASIS_ABS_MIN:
            fails.append("basis zayıf")
        elif _sign(r["basis"]) != _sign(fnorm):
            fails.append("basis ters")
        # Çapraz borsa teyidi (Binance)
        cross = binance.cross_check(r["symbol"], fnorm)
        # yakınlık: eşiklere ne kadar yakın (1.0 = tam geçiyor)
        prox = min(abs(z) / C.Z_MIN, abs(fnorm) / C.FUNDING_ABS_MIN)
        watchlist.append({
            "symbol": r["symbol"],
            "crowd": "LONG" if fnorm > 0 else "SHORT",
            "funding_norm": round(fnorm, 6),
            "z": round(z, 2),
            "basis": round(r["basis"], 5),
            "cycle_h": cycle,
            "oi_usd": int(r["oi_usd"]),
            "proximity": round(prox, 2),
            "fails": fails,
            "cross": cross["status"],
            "binance_funding": cross["binance_funding"],
        })

        # ── Aşama 1: funding aşırılığı (göreli + mutlak) ──
        if abs(z) < C.Z_MIN or abs(fnorm) < C.FUNDING_ABS_MIN:
            continue

        # ── Aşama 2: basis teyidi ──
        if _sign(r["basis"]) != _sign(fnorm) or abs(r["basis"]) < C.BASIS_ABS_MIN:
            continue

        # ── Skorlama ──
        score = (
            1.0 * abs(z)
            + 0.8 * abs(r["basis"]) / 0.001
            + 0.5 * math.log10(max(r["amount24"] / C.MIN_VOL24_USDT, 1.0))
            - 1.2 * (r["spread"] / 0.0008)
        )
        # Çapraz borsa teyidi skoru etkiler
        if cross["status"] == "confirmed":
            score += 1.0        # Binance de aynı yönde uç -> güçlü
        elif cross["status"] == "diverge":
            score -= 1.5        # Binance ters -> şüpheli, cezalandır

        candidates.append({
            "symbol": r["symbol"],
            "crowd": "LONG" if fnorm > 0 else "SHORT",
            "funding_norm": round(fnorm, 6),
            "z": round(z, 2),
            "basis": round(r["basis"], 5),
            "cycle_h": cycle,
            "next_settle_ms": next_settle,
            "oi_usd": int(r["oi_usd"]),
            "vol24_usd": int(r["amount24"]),
            "spread": round(r["spread"], 5),
            "last": r["lastPrice"],
            "score": round(score, 2),
            "hist_n": st["n"],
            "cross": cross["status"],
            "binance_funding": cross["binance_funding"],
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    # izleme listesi: eşiği geçmeyenler dahil, yakınlığa göre sıralı ilk 8
    passing = {c["symbol"] for c in candidates}
    watchlist = [w for w in watchlist if w["symbol"] not in passing]
    watchlist.sort(key=lambda w: w["proximity"], reverse=True)
    watchlist = watchlist[:8]
    if verbose:
        print(f"  · {len(candidates)} aday, {len(watchlist)} izleme")
    return {"candidates": candidates, "watchlist": watchlist}


def print_candidates(cands):
    if not cands:
        print("\n  Aday yok. Şu an sıkışmış bir kalabalık görünmüyor — bu normal.")
        print("  Her funding olayında kurulum çıkmaz; zorlama.\n")
        return
    print(f"\n  {len(cands)} aday bulundu (skora göre sıralı):\n")
    hdr = (f"  {'#':>2} {'SEMBOL':<16}{'KALABALIK':<10}{'fund_8h':>9}"
           f"{'z':>7}{'basis%':>8}{'döngü':>7}{'OI($M)':>9}{'skor':>7}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for i, c in enumerate(cands, 1):
        print(f"  {i:>2} {c['symbol']:<16}{c['crowd']:<10}"
              f"{c['funding_norm']*100:>8.3f}%{c['z']:>7.1f}"
              f"{c['basis']*100:>7.3f}%{c['cycle_h']:>6}s"
              f"{c['oi_usd']/1e6:>8.1f}{c['score']:>7.2f}")
    print()


if __name__ == "__main__":
    print("Tarama başlıyor...")
    res = screen()
    print_candidates(res["candidates"])
    if res["watchlist"]:
        print("  İzleme listesi (eşiği geçmedi, en yakınlar):")
        for w in res["watchlist"]:
            print(f"    {w['symbol']:<16}{w['crowd']:<7}z={w['z']:<6}"
                  f"fund={w['funding_norm']*100:+.3f}%  yakınlık={w['proximity']}"
                  f"  ({', '.join(w['fails'])})")
        print()
