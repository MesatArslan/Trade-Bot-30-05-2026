#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_feed.py — Anahtarsız, halka açık borsa duyuru akışı (Bybit + Binance, opsiyonel CryptoPanic).

Kullanım (modül):
    from news_feed import fetch_all, recent_for
    items = fetch_all(since_ts_ms)              # [{ts, source, kind, title, symbols, url, ...}]
    news  = recent_for("HYPEUSDT", hours=24)    # cache'ten coin bazlı

Kullanım (CLI):
    python news_feed.py                # son 24 saat, coin bazlı
    python news_feed.py --hours 72     # son 72 saat
    python news_feed.py --days 180 --json out.json   # tarihsel çekim, JSON dump

Kaynaklar:
  - Bybit  : https://api.bybit.com/v5/announcements/index  (tüm tipler tek listede; limit≤100/sayfa)
  - Binance: https://www.binance.com/bapi/composite/v1/public/cms/article/list/query
             catalogId=48 yeni listeler, 161 delist  (User-Agent şart; bazı bölgelerde erişilemeyebilir)
  - CryptoPanic (opsiyonel): CRYPTOPANIC_TOKEN env varsa https://cryptopanic.com/api/v1/posts/
Sadece stdlib (urllib) kullanır; hata toleranslı: kaynak düşerse boş liste döner, _errors alanına yazar.
Cache: news_cache.json (aynı dizinde) — url anahtarlı; sembol evreni (Bybit linear) 1 gün cache'lenir.
"""
import json, os, re, sys, time, datetime as dt, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "news_cache.json")
UA = "Mozilla/5.0 (Macintosh) news_feed/1.0"
TIMEOUT = 20
LAST_ERRORS = []

# ------------------------------------------------------------------ yardımcılar
def _get_json(url, headers=None, retries=2):
    hdr = {"User-Agent": UA, "Accept": "application/json"}
    if headers: hdr.update(headers)
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa
            last = e; time.sleep(0.6)
    LAST_ERRORS.append(f"{url[:80]}: {last}")
    return None


def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": {}, "universe": {"ts": 0, "bases": []}}


def _save_cache(c):
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception as e:
        LAST_ERRORS.append(f"cache write: {e}")


# ------------------------------------------------------------------ sembol evreni
_STOP = set("""USDT USDC USD USDE BTC ETH APR APY VIP AI NFT P2P ETF API TGE USDⓈ UTC AM PM EU US UK MENA
TR CIS LATAM APAC NEW HOT TOP BUY WIN GET ALL FOR AND THE OF TO IN ON AT BY VS OR NO YES OFF ONE TWO
TON KYC OTC FAQ RWA DEX CEX DEFI CEFI GRID DCA BOT BOTS COPY EARN SAVE LOAN LAUNCHPOOL LAUNCHPAD
MT5 MT4 TRADFI FX GOLD OIL WEB3 WEB IOS APP PRO MAX MIN ID FEE FEES XAU XAG PERP PERPS PRE POST
USDT-M COIN-M ISO USDT-MARGINED AMA X Q1 Q2 Q3 Q4 H1 H2 II III IV V VI AUG SEP OCT NOV DEC JAN FEB
MAR MAY JUN JUL UNI-FIED BB TL EUR GBP JPY KRW BRL INR ARS MXN TRY AED NGN VND IDR THB PHP PLN
UNIFIED UTA MM VIPS PNL P&L ATH 1ST 2ND 3RD CNY HKD RUB UAH KZT MYR SGD AUD CAD CHF""".split())

_UNIVERSE_TTL = 86400


def get_universe(force=False):
    """Bybit linear USDT-perp baz coin seti (ör. {'BTC','ETH','1000PEPE',...}). 1 gün cache."""
    c = _load_cache()
    u = c.get("universe") or {"ts": 0, "bases": []}
    if not force and u.get("bases") and time.time() - u.get("ts", 0) < _UNIVERSE_TTL:
        return set(u["bases"])
    bases = set(); cursor = ""
    for _ in range(10):
        url = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        d = _get_json(url)
        if not d or d.get("retCode") != 0: break
        for it in d["result"].get("list", []):
            if it.get("quoteCoin") == "USDT" and it.get("symbol", "").endswith("USDT"):
                bases.add(it["symbol"][:-4])
        cursor = d["result"].get("nextPageCursor") or ""
        if not cursor: break
    if bases:
        c["universe"] = {"ts": time.time(), "bases": sorted(bases)}
        _save_cache(c)
        return bases
    return set(u.get("bases") or [])


def _norm_base(b, universe):
    """Başlıktaki ham baz adı → evrendeki karşılığı (1000PEPE, SHIB1000 gibi çarpanlı isimler dahil)."""
    b = b.upper()
    if b in universe: return b
    for cand in ("1000" + b, "10000" + b, b + "1000", "1000000" + b):
        if cand in universe: return cand
    if b.startswith("1000") and b[4:] in universe: return b[4:]
    return None


def extract_symbols(title, universe=None, strict=False):
    """Başlıktan coin sembollerini çıkar → ['HYPEUSDT', ...].
    Öncelik: XYZUSDT / XYZ/USDT / XYZ-USDT kalıpları > parantez içi (XYZ) > evrende bulunan büyük harfli kelimeler.
    universe None ise sadece açık kalıplar (strict) kullanılır."""
    t = title.replace("USDⓈ", "USDT").replace(" ", " ")
    found = []
    def add(b, explicit=False):
        if universe:
            nb = _norm_base(b, universe)
            if nb is None and explicit and b not in _STOP: nb = b   # XYZUSDT kalıbı: evrende yoksa da (delist edilmiş) al
            if nb and nb not in found: found.append(nb)
        elif b not in _STOP and b not in found:
            found.append(b)
    # 1) açık kalıplar
    for m in re.finditer(r"\b([A-Z0-9]{2,15}?)(?:USDT|/USDT|-USDT|USDT-M|PERP)\b", t):
        b = m.group(1)
        if b and b not in ("USDT", "USD", "USDC", "USDE"): add(b, explicit=True)
    # 2) parantez içi (XYZ) / (XYZ, ABC)
    for m in re.finditer(r"\(([A-Z0-9]{2,10}(?:\s*,\s*[A-Z0-9]{2,10})*)\)", t):
        for b in re.split(r"\s*,\s*", m.group(1)): add(b)
    if strict or not universe: return [f + "USDT" for f in found]
    # 3) evrende bulunan büyük harfli kelimeler (virgül listeleri: "Delist ICX, SCRT, STORJ")
    for m in re.finditer(r"\b([A-Z][A-Z0-9]{1,9})\b", t):
        b = m.group(1)
        if b in _STOP or b.isdigit(): continue
        if b in universe: add(b)
    return [f + "USDT" for f in found]


# ------------------------------------------------------------------ sınıflandırma
_BYBIT_KIND = {"new_crypto": "listing", "delistings": "delist", "latest_activities": "campaign",
               "product_updates": "product", "maintenance_updates": "maintenance", "latest_bybit_news": "news"}


def classify(title, base_kind="other", tags=None):
    """Başlık + kaynak tipinden kind: listing_perp / listing_spot / delist / leverage / campaign / maintenance / product / news / other"""
    tl = title.lower(); tags = [x.lower() for x in (tags or [])]
    is_perp = ("perpetual" in tl or "futures" in tl or "derivatives" in tags or "usdt-m" in tl or "contract" in tl)
    if "delist" in tl or "removal of" in tl or "terminat" in tl or "cease" in tl or "discontinu" in tl: return "delist"
    listing_kw = re.search(r"new listing|will list|new spot|launch(es|ing)?\b.*\b(perpetual|contract|pair|trading)|lists?\b.*\b(perpetual|spot|contract)|now listed|listing of|coming soon|now available on bybit (spot|derivatives)|pre-market", tl)
    campaign_kw = re.search(r"\bearn\b|prize|rewards?|giveaway|airdrop|competition|carnival|share of|bonus|celebrat|fragment|festival|splash|up for grabs|hotspot|trading bots|grab", tl)
    if listing_kw: return "listing_perp" if is_perp else "listing_spot"
    if re.search(r"leverage|risk limit|margin tier|maintenance margin|position limit|max\.? position|funding rate|funding fee|tick size|price limit", tl): return "leverage"
    if campaign_kw or base_kind == "campaign": return "campaign"
    if base_kind == "listing": return "listing_perp" if is_perp else "listing_spot"
    if base_kind in ("maintenance", "product", "news"): return base_kind
    return "other"


# ------------------------------------------------------------------ kaynaklar
def fetch_bybit(since_ts, max_pages=100, universe=None):
    """Bybit duyuruları (tüm tipler). since_ts: ms. Sayfa sıralaması dateTimestamp'e göre; publishTime olay zamanı olarak alınır."""
    out = []; page = 1
    while page <= max_pages:
        d = _get_json(f"https://api.bybit.com/v5/announcements/index?locale=en-US&page={page}&limit=100")
        if not d or d.get("retCode") != 0: break
        lst = d["result"].get("list", [])
        if not lst: break
        oldest = None
        for it in lst:
            pt = it.get("publishTime") or it.get("dateTimestamp")
            dts = it.get("dateTimestamp") or pt
            oldest = min(oldest, dts) if oldest else dts
            if pt < since_ts: continue
            typ = (it.get("type") or {}).get("key") or ""
            base = _BYBIT_KIND.get(typ, "other")
            out.append({"ts": pt, "event_ts": dts, "source": "bybit", "src_type": typ or (it.get("type") or {}).get("title", ""),
                        "kind": classify(it.get("title", ""), base, it.get("tags")), "title": it.get("title", ""),
                        "symbols": extract_symbols(it.get("title", ""), universe), "url": it.get("url", ""),
                        "tags": it.get("tags") or []})
        if oldest and oldest < since_ts - 3 * 86400_000: break   # 3 gün pay (publishTime < dateTimestamp olabilir)
        page += 1
    return out


def fetch_binance(since_ts, max_pages=60, universe=None, catalogs=((48, "listing"), (161, "delist"))):
    """Binance CMS duyuruları: catalogId 48 yeni listeler, 161 delist. Bölgesel engel varsa boş döner (LAST_ERRORS'a yazar)."""
    out = []
    for cat, base in catalogs:
        page = 1
        while page <= max_pages:
            d = _get_json(f"https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo={page}&pageSize=50&catalogId={cat}",
                          headers={"clienttype": "web", "lang": "en"})
            try:
                arts = d["data"]["catalogs"][0]["articles"]
            except Exception:
                break
            if not arts: break
            for a in arts:
                ts = a.get("releaseDate") or 0
                if ts < since_ts: continue
                title = a.get("title", "")
                out.append({"ts": ts, "event_ts": ts, "source": "binance", "src_type": f"catalog{cat}",
                            "kind": classify(title, base), "title": title, "symbols": extract_symbols(title, universe),
                            "url": f"https://www.binance.com/en/support/announcement/{a.get('code','')}", "tags": []})
            if arts[-1].get("releaseDate", 0) < since_ts: break
            page += 1
    return out


def fetch_cryptopanic(since_ts, universe=None, currencies=None):
    """Opsiyonel: CRYPTOPANIC_TOKEN env varsa. Ücretsiz plan: ~son 200 post, 'currencies' filtresi ≤50 sembol."""
    tok = os.environ.get("CRYPTOPANIC_TOKEN")
    if not tok: return []
    out = []; url = f"https://cryptopanic.com/api/v1/posts/?auth_token={tok}&kind=news&public=true"
    if currencies: url += "&currencies=" + ",".join(currencies[:50])
    for _ in range(10):
        d = _get_json(url)
        if not d or "results" not in d: break
        for p in d["results"]:
            try: ts = int(dt.datetime.fromisoformat(p["published_at"].replace("Z", "+00:00")).timestamp() * 1000)
            except Exception: continue
            if ts < since_ts: continue
            syms = [c.get("code", "").upper() for c in (p.get("currencies") or [])]
            syms = [s + "USDT" for s in syms if (not universe or _norm_base(s, universe))]
            out.append({"ts": ts, "event_ts": ts, "source": "cryptopanic", "src_type": p.get("kind", "news"),
                        "kind": "news", "title": p.get("title", ""), "symbols": syms or extract_symbols(p.get("title", ""), universe),
                        "url": p.get("url", ""), "tags": []})
        url = d.get("next")
        if not url or (d["results"] and d["results"][-1] and out and out[-1]["ts"] < since_ts): break
    return out


# ------------------------------------------------------------------ ana API
def fetch_all(since_ts, use_cache=True, sources=("bybit", "binance", "cryptopanic")):
    """since_ts (ms) sonrası tüm duyurular; cache ile birleştirilir; ts'e göre azalan sırada döner."""
    LAST_ERRORS.clear()
    universe = get_universe() or None
    cache = _load_cache() if use_cache else {"items": {}, "universe": {}}
    items = cache.setdefault("items", {})
    fresh = []
    if "bybit" in sources: fresh += fetch_bybit(since_ts, universe=universe)
    if "binance" in sources: fresh += fetch_binance(since_ts, universe=universe)
    if "cryptopanic" in sources: fresh += fetch_cryptopanic(since_ts, universe=universe)
    for it in fresh:
        key = it["url"] or (it["source"] + "|" + it["title"])
        items[key] = it
    if use_cache: _save_cache(cache)
    res = [it for it in items.values() if it["ts"] >= since_ts]
    res.sort(key=lambda x: -x["ts"])
    return res


def recent_for(symbol, hours=24, refresh=False):
    """Belirli sembol için son `hours` saatteki haberler (cache'ten; refresh=True ise önce çeker)."""
    since = int(time.time() * 1000) - hours * 3600_000
    items = fetch_all(since) if refresh else [it for it in _load_cache().get("items", {}).values() if it["ts"] >= since]
    sym = symbol.upper()
    if not sym.endswith("USDT"): sym += "USDT"
    return sorted([it for it in items if sym in it.get("symbols", [])], key=lambda x: -x["ts"])


def _fmt_ts(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%m-%d %H:%M")


def main(argv):
    hours = 24; days = None; jout = None
    a = argv[1:]
    while a:
        k = a.pop(0)
        if k == "--hours": hours = float(a.pop(0))
        elif k == "--days": days = float(a.pop(0))
        elif k == "--json": jout = a.pop(0)
    since = int(time.time() * 1000) - int((days * 24 if days else hours) * 3600_000)
    items = fetch_all(since)
    if jout:
        with open(jout, "w", encoding="utf-8") as f: json.dump(items, f, ensure_ascii=False)
        print(f"{len(items)} duyuru → {jout}")
    if LAST_ERRORS: print("Uyarı (erişim):", *LAST_ERRORS, sep="\n  ")
    by = {}
    for it in items:
        for s in it["symbols"] or ["(genel)"]: by.setdefault(s, []).append(it)
    print(f"Son {days*24 if days else hours:.0f} saat: {len(items)} duyuru, {len([k for k in by if k!='(genel)'])} coin  (UTC)")
    for s in sorted(by, key=lambda s: (s == "(genel)", s)):
        if jout and s == "(genel)": continue
        print(f"\n[{s}]")
        for it in by[s][:12]:
            print(f"  {_fmt_ts(it['ts'])}  {it['source']:<8} {it['kind']:<13} {it['title'][:95]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
