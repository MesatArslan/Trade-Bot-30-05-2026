#!/opt/homebrew/bin/python3
# -*- coding: utf-8 -*-
"""
Funding Köşe — macOS menü çubuğu (sağ üst köşe) squeeze tarayıcı.

SKR profiline uyan coinleri sürekli tarar ve köşede şık gösterir:
  derin & giderek negatifleşen funding + küçük OI + taze/yeni + yeterli hacim.

Menü çubuğunda:  🔥3  (bulunan aday sayısı)
Tıkla → liste:   SKR   -2.50%  OI $8M  24h +119%  ↓kötüleşiyor  (TAZE)
Coine tıkla → Bybit grafiğini açar.

Çalıştır:  /opt/homebrew/bin/python3 "funding_kose.py"
"""

import urllib.request, json, time, threading, webbrowser
import rumps

BASE = "https://api.bybit.com"

# ---- Tarama eşikleri (SKR profili) ----
FUNDING_MAX   = -0.003    # anlık funding <= -0.3% (derin negatif)
MIN_TURNOVER  = 3e6       # 24h hacim >= $3M (işlem edilebilir)
MAX_OI        = 60e6      # OI <= $60M (küçük-orta cap; büyükleri ele)
FRESH_DAYS    = 120       # <120 gün = TAZE
REFRESH_SEC   = 300       # 5 dakikada bir tara
TOP_DETAIL    = 30        # funding geçmişi çekilecek aday sayısı


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': 'curl/8'})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def scan():
    """SKR profiline uyan adayları döndür (skora göre sıralı liste)."""
    tk = _get(f"{BASE}/v5/market/tickers?category=linear")['result']['list']
    info = _get(f"{BASE}/v5/market/instruments-info?category=linear&limit=1000")['result']['list']
    launch = {i['symbol']: int(i.get('launchTime', '0') or 0) for i in info}

    now = time.time()
    cands = []
    for t in tk:
        s = t['symbol']
        if not s.endswith('USDT'):
            continue
        try:
            fr = float(t['fundingRate'])
            oi = float(t['openInterestValue'])
            tov = float(t['turnover24h'])
            pc = float(t['price24hPcnt'])
        except (KeyError, ValueError, TypeError):
            continue
        if fr >= FUNDING_MAX:      continue
        if tov < MIN_TURNOVER:     continue
        if oi > MAX_OI:            continue
        cands.append([s, fr, oi, tov, pc])

    cands.sort(key=lambda x: x[1])           # en negatif önce
    rows = []
    for s, fr, oi, tov, pc in cands[:TOP_DETAIL]:
        try:
            fh = _get(f"{BASE}/v5/market/funding/history?category=linear&symbol={s}&limit=4")['result']['list']
            vals = [float(f['fundingRate']) for f in fh]   # [0]=en yeni
        except Exception:
            vals = [fr]
        worsening = len(vals) >= 3 and vals[0] < vals[1] < vals[2]
        allneg = all(v < 0 for v in vals[:3])
        if worsening:   trend = "↓kötüleşiyor"
        elif allneg:    trend = "↓negatif"
        else:           trend = "karışık"
        age = (now - launch.get(s, 0) / 1000) / 86400 if launch.get(s, 0) else 9999
        fresh = "TAZE" if age < FRESH_DAYS else ("orta" if age < 365 else "eski")

        # skor
        sc = min(abs(fr) / 0.01, 3) * 3
        if worsening: sc += 3
        elif allneg:  sc += 1
        sc += max(0, (MAX_OI - oi) / MAX_OI) * 2
        if fresh == "TAZE": sc += 2
        elif fresh == "orta": sc += 1

        rows.append(dict(sym=s, fr=fr, oi=oi, tov=tov, pc=pc,
                         trend=trend, age=age, fresh=fresh, vals=vals, score=sc))
    rows.sort(key=lambda r: r['score'], reverse=True)
    return rows


class FundingKose(rumps.App):
    def __init__(self):
        super().__init__("Funding", title="⏳ Funding", quit_button=None)
        self.rows = []
        self.last = "—"
        self.menu = ["(taranıyor…)"]
        # arka planda ilk tarama + zamanlayıcı
        rumps.Timer(self._tick, REFRESH_SEC).start()
        threading.Thread(target=self._scan_bg, daemon=True).start()

    # zamanlayıcı her REFRESH_SEC'te tetikler
    def _tick(self, _):
        threading.Thread(target=self._scan_bg, daemon=True).start()

    def _scan_bg(self):
        try:
            rows = scan()
            self.rows = rows
            self.last = time.strftime("%H:%M")
            self._render()
        except Exception as e:
            self.title = "⚠️ Funding"
            self.menu.clear()
            self.menu.update([
                rumps.MenuItem(f"Hata: {e}"),
                None,
                rumps.MenuItem("🔄 Şimdi tara", callback=self._manual),
                rumps.MenuItem("Çıkış", callback=rumps.quit_application),
            ])

    def _render(self):
        n = len(self.rows)
        self.title = (f"🔥 {n}" if n else "· 0")
        items = []
        if not self.rows:
            items.append(rumps.MenuItem("Şu an SKR profili aday yok"))
        else:
            for r in self.rows:
                short = r['sym'].replace("USDT", "")
                label = (f"{short:<7} {r['fr']*100:+.2f}%  "
                         f"OI ${r['oi']/1e6:.1f}M  24h {r['pc']*100:+.0f}%  "
                         f"{r['trend']} ({r['fresh']})")
                it = rumps.MenuItem(label, callback=self._open)
                it._sym = r['sym']
                items.append(it)
        items += [
            None,
            rumps.MenuItem(f"Son tarama: {self.last}"),
            rumps.MenuItem("🔄 Şimdi tara", callback=self._manual),
            rumps.MenuItem("Çıkış", callback=rumps.quit_application),
        ]
        self.menu.clear()
        self.menu.update(items)

    def _open(self, sender):
        sym = getattr(sender, "_sym", None)
        if sym:
            webbrowser.open(f"https://www.bybit.com/trade/usdt/{sym}")

    def _manual(self, _):
        self.title = "⏳ Funding"
        threading.Thread(target=self._scan_bg, daemon=True).start()


if __name__ == "__main__":
    FundingKose().run()
