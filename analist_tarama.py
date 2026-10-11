#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analist_tarama.py — 🔭 Toplu Coin Analizi (server.py içinden kullanılır).

1) ÖN ELEME (LLM yok, ucuz): MEXC USDT perp evreni → 24s hacim ≥ min_vol → her coin için coin_analiz 1h/4h/1d
   (EMA dizilimi, ADX, Supertrend, swing) + funding + duyuru → "ilginçlik skoru". Amaç: ajanı 200 coine değil,
   trend/haber açısından mantıklı görünen ilk N coine koşturmak.
2) AJAN TURU: skor sırasına göre ilk N coin için server.analist_sor (claude -p --agent coin-analist) çağrılır,
   sonuçlar toplanır; plan.yon LONG/SHORT ise macOS bildirimi + "islemler" listesine eklenir.

Durum data/coin_analist/tarama_son.json'a yazılır (sunucu yeniden başlasa da son tarama görünür).
"""
import os, json, time, threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DURUM_DOSYA = os.path.join(HERE, "data", "coin_analist", "tarama_son.json")

_sor = _job = _notify = None     # server.py init() ile verir
_LOCK = threading.Lock()
_STOP = threading.Event()
D = {"durum": "bos", "eleme_yapilan": 0, "eleme_toplam": 0, "baslangic": None, "bitis": None, "ayar": {}, "evren_n": 0, "eleme_n": 0,
     "eleme": [], "ajan_sira": [], "sonuclar": [], "islemler": [], "ilerleme": "", "hata": None}


def init(sor_fn, job_fn, notify_fn):
    global _sor, _job, _notify
    _sor, _job, _notify = sor_fn, job_fn, notify_fn
    try:
        with open(DURUM_DOSYA, encoding="utf-8") as f:
            eski = json.load(f)
        if eski.get("durum") in ("bitti", "durduruldu"):
            D.update(eski)
        elif eski.get("durum"):
            eski["durum"] = "durduruldu"; eski["ilerleme"] = "sunucu yeniden başladı, tarama yarım kaldı"; D.update(eski)
    except Exception:
        pass


def _kaydet():
    try:
        os.makedirs(os.path.dirname(DURUM_DOSYA), exist_ok=True)
        tmp = DURUM_DOSYA + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(D, f, ensure_ascii=False)
        os.replace(tmp, DURUM_DOSYA)
    except Exception:
        pass


# ───────────────────────── ön eleme ─────────────────────────
def _skorla(sym, t24):
    """coin_analiz ile 1h/4h/1d; ilginçlik skoru döner."""
    import coin_analiz as CA, mexc
    try:
        dil = {}
        for et, iv, mins, bars in [("1h", "Min60", 60, 300), ("4h", "Hour4", 240, 300), ("1d", "Day1", 1440, 200)]:
            k = mexc.klines(sym, minutes=mins * bars, interval=iv)
            if not k or len(k) < 60:
                return None
            dil[et] = CA.analiz_tf(k, et)
        h4, d1, h1 = dil["4h"], dil["1d"], dil["1h"]
        skor = 0.0; neden = []
        # 1) 4h+1d uyumlu trend
        if h4["trend"] == d1["trend"] and h4["trend"] in ("YUKARI", "AŞAĞI"):
            skor += 3; neden.append(f"4h+1d {h4['trend']}")
        elif h4["trend"] in ("YUKARI", "AŞAĞI"):
            skor += 1.5; neden.append(f"4h {h4['trend']}")
        # 2) Supertrend yeni döndü (4h ≤6 bar veya 1d ≤2 bar)
        if h4["supertrend"]["kac_bar_once_dondu"] <= 6:
            skor += 2; neden.append(f"4h ST {h4['supertrend']['yon']} {h4['supertrend']['kac_bar_once_dondu']}b önce döndü")
        if d1["supertrend"]["kac_bar_once_dondu"] <= 2:
            skor += 2; neden.append(f"1d ST {d1['supertrend']['yon']} yeni döndü")
        # 3) güçlü ADX
        if (h4.get("adx14") or 0) >= 30: skor += 1; neden.append(f"4h ADX {h4['adx14']}")
        # 4) hareket + hacim
        c24 = abs(t24.get("chg", 0) * 100)
        if c24 >= 5: skor += min(3, c24 / 5); neden.append(f"24s {t24.get('chg', 0)*100:+.1f}%")
        if (h4.get("rvol_son_kapanan_bar") or 0) >= 2: skor += 1.5; neden.append(f"4h RVOL {h4['rvol_son_kapanan_bar']}")
        # 5) yapı (HH+HL / LH+LL)
        if h4["swing"]["yapi"] in ("HH+HL", "LH+LL"): skor += 1; neden.append(f"4h {h4['swing']['yapi']}")
        # 6) funding ekstrem
        fr = t24.get("fr", 0) * 100
        if abs(fr) >= 0.05: skor += 1.5; neden.append(f"funding {fr:+.3f}%")
        # 7) duyuru (cache)
        duy = 0
        try:
            import news_feed
            duy = len(news_feed.recent_for(sym.replace("_", ""), hours=72))
            if duy: skor += 2; neden.append(f"{duy} duyuru")
        except Exception:
            pass
        # yön adayı
        oy = [x["trend"] for x in (h4, d1, h1)]
        yon = "LONG" if oy.count("YUKARI") >= 2 else ("SHORT" if oy.count("AŞAĞI") >= 2 else "?")
        return {"sym": sym, "coin": sym.replace("_USDT", ""), "skor": round(skor, 1), "yon_adayi": yon, "neden": neden,
                "fiyat": h1["fiyat"], "chg24": round(t24.get("chg", 0) * 100, 2), "vol24": t24.get("vol", 0),
                "t1h": h1["trend"], "t4h": h4["trend"], "t1d": d1["trend"], "adx4h": h4.get("adx14"),
                "rsi4h": h4.get("rsi14"), "duyuru": duy}
    except Exception as e:
        return {"sym": sym, "coin": sym.replace("_USDT", ""), "skor": -1, "hata": str(e)[:120]}


def _evren(min_vol):
    import mexc
    data = mexc.get("/api/v1/contract/ticker") or []
    out = []
    for r in data:
        s = r.get("symbol", "")
        if not s.endswith("_USDT"):
            continue
        try:
            vol = float(r.get("amount24", 0))
            if vol < min_vol:
                continue
            out.append({"s": s, "vol": vol, "chg": float(r.get("riseFallRate", 0)), "fr": float(r.get("fundingRate", 0))})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: -x["vol"])
    return out


# ───────────────────────── tur ─────────────────────────
def _calistir(n, min_vol, max_evren):
    try:
        D["ilerleme"] = "evren çekiliyor"; _kaydet()
        ev = _evren(min_vol)[:max_evren]
        D["evren_n"] = len(ev)
        D["durum"] = "eleme"; D["ilerleme"] = f"0/{len(ev)} coin ön elemede"; _kaydet()
        try:
            import news_feed
            news_feed.fetch_all(int(time.time() * 1000) - 72 * 3600_000)   # duyuru cache'ini bir kez tazele
        except Exception:
            pass
        sonuc = []; done = 0
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(_skorla, t["s"], t): t for t in ev}
            for f in futs:
                if _STOP.is_set():
                    break
                r = f.result()
                done += 1
                if r: sonuc.append(r)
                D["eleme_yapilan"] = done; D["eleme_toplam"] = len(ev)
                D["ilerleme"] = f"{done}/{len(ev)} coin ön elemede"
                if done % 10 == 0: _kaydet()
        sonuc = [x for x in sonuc if x.get("skor", -1) >= 0]
        sonuc.sort(key=lambda x: -x["skor"])
        D["eleme"] = sonuc[:60]; D["eleme_n"] = len(sonuc)
        if _STOP.is_set():
            D["durum"] = "durduruldu"; D["ilerleme"] = "ön elemede durduruldu"; D["bitis"] = time.time(); _kaydet(); return
        secilen = [x for x in sonuc if x["skor"] >= 3]
        if n > 0:
            secilen = secilen[:n]
        D["ajan_sira"] = [x["coin"] for x in secilen]
        D["sonuclar"] = [{"coin": x["coin"], "skor": x["skor"], "yon_adayi": x["yon_adayi"], "durum": "bekliyor", "id": None, "plan": None} for x in secilen]
        D["durum"] = "ajan"; D["ilerleme"] = f"ajan turu 0/{len(secilen)}"; _kaydet()
        # ajan: 2'şer 2'şer (server semaforu zaten 2)
        bekleyen = list(D["sonuclar"])
        aktif = []
        while (bekleyen or aktif) and not _STOP.is_set():
            while bekleyen and len(aktif) < 2:
                s = bekleyen.pop(0)
                r = _sor(s["coin"], "Toplu tarama. Sadece gerçekten net kurulum varsa LONG/SHORT ver; şüphede BEKLE.")
                if r.get("ok"):
                    s["id"] = r["id"]; s["durum"] = "calisiyor"; aktif.append(s)
                else:
                    s["durum"] = "hata"; s["hata"] = r.get("msg")
            _kaydet()
            time.sleep(4)
            for s in list(aktif):
                j = _job(s["id"]) or {}
                if j.get("durum") in ("bitti", "hata"):
                    aktif.remove(s)
                    s["durum"] = j["durum"]; s["plan"] = j.get("plan"); s["hata"] = j.get("hata")
                    s["ozet"] = (j.get("cevap") or "")[:200]; s["maliyet"] = j.get("maliyet_usd")
                    y = (s["plan"] or {}).get("yon")
                    if y in ("LONG", "SHORT"):
                        D["islemler"].append({"coin": s["coin"], "yon": y, "plan": s["plan"], "id": s["id"], "ts": time.time()})
                        p = s["plan"]
                        _notify(f"🤖 {s['coin']} {y}", f"giriş {p.get('giris')} · stop {p.get('stop')} · TP1 {p.get('tp1')} · güven {p.get('guven')}")
                    biten = sum(1 for x in D["sonuclar"] if x["durum"] in ("bitti", "hata"))
                    D["ilerleme"] = f"ajan turu {biten}/{len(D['sonuclar'])}"; _kaydet()
        D["durum"] = "durduruldu" if _STOP.is_set() else "bitti"
        D["bitis"] = time.time()
        if not _STOP.is_set():
            isl = D["islemler"]
            _notify("🔭 Toplu analiz bitti", f"{len(D['sonuclar'])} coin incelendi, {len(isl)} işlem: " + ", ".join(f"{x['coin']} {x['yon']}" for x in isl) if isl else f"{len(D['sonuclar'])} coin incelendi, işlem yok")
        D["ilerleme"] = "bitti"; _kaydet()
    except Exception as e:
        D["durum"] = "hata"; D["hata"] = str(e)[:300]; D["bitis"] = time.time(); _kaydet()


def baslat(n=10, min_vol=5_000_000, max_evren=200):
    with _LOCK:
        if D["durum"] in ("eleme", "ajan"):
            return {"ok": False, "msg": "tarama zaten çalışıyor"}
        _STOP.clear()
        D.update({"durum": "basliyor", "baslangic": time.time(), "bitis": None, "ayar": {"n": n, "min_vol": min_vol, "max_evren": max_evren},
                  "evren_n": 0, "eleme_n": 0, "eleme": [], "ajan_sira": [], "sonuclar": [], "islemler": [], "ilerleme": "başlıyor", "hata": None})
        _kaydet()
    threading.Thread(target=_calistir, args=(n, min_vol, max_evren), daemon=True).start()
    return {"ok": True}


def durdur():
    _STOP.set()
    return {"ok": True}


def durum():
    return D
