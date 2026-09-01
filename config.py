"""
Funding Trader — ayarlar.
Buradaki değerleri kendine göre düzenle. Kod dosyalarına dokunmana gerek yok.
"""

# ─── HESAP / RİSK ───────────────────────────────────────────────
BALANCE_USDT       = 100.0    # Toplam işlem bakiyen (USDT)
RISK_PER_TRADE     = 0.01     # İşlem başına riske ettiğin oran (0.01 = %1)
MAX_LEVERAGE       = 20       # Asla bunun üstüne çıkma (sistem 8x-20x ister)
MAX_OPEN_POSITIONS = 2        # Aynı anda en fazla kaç pozisyon

# ─── EVREN FİLTRESİ (Aşama 0 — işlem edilebilirlik) ─────────────
MIN_VOL24_USDT   = 15_000_000   # 24s hacim alt sınırı
MIN_OI_USDT      =  3_000_000   # Açık pozisyon (holdVol*fiyat) alt sınırı
MAX_SPREAD       = 0.0008       # (ask1-bid1)/bid1 üst sınırı  (%0.08)
MIN_PRICE        = 0.0005       # Tick problemi olan mikro coinleri ele
MIN_FUNDING_HIST = 30           # Bu kadar funding kaydı yoksa z-skor güvenilmez

# ─── FUNDING AŞIRILIĞI (Aşama 1) ────────────────────────────────
Z_MIN            = 2.0          # |funding_z| bu eşiği geçmeli
FUNDING_ABS_MIN  = 0.0015       # 8s'e normalize |funding| >= %0.15

# ─── BASIS TEYİDİ (Aşama 2) ─────────────────────────────────────
BASIS_ABS_MIN    = 0.0008       # |basis| >= %0.08 ve funding ile aynı işaret

# ─── YÖN / SIKIŞMA TEYİDİ (Aşama 3, decide.py) ──────────────────
SQUEEZE_RET8H_MULT  = 0.5       # LONG: ret_8h > 0.5 * atr_pct
FLUSH_RET8H_MULT    = 3.0       # SHORT: ret_8h > 3 * atr_pct  (parabolik)

# ─── ÇIKIŞ ──────────────────────────────────────────────────────
TP_ATR_MULT      = 2.0          # Kâr hedefi = giriş ATR'sinin katı
STOP_ATR_MULT    = 0.3          # Stop = referans mum ucunun 0.3 ATR ötesi
TIME_STOP_MIN    = 90           # T+90dk koşulsuz kapat
ENTRY_WINDOW_MIN = 25           # T+25dk'da tetik gelmediyse olayı iptal et

# ─── ÜCRETLER (bilgi amaçlı, planlamada kullanılır) ─────────────
FEE_MAKER        = 0.0001       # %0.01
FEE_TAKER        = 0.0005       # %0.05

# ─── TARAYICI DERİNLİĞİ / HIZ ───────────────────────────────────
PRESCREEN_TOP    = 45           # Ham funding'e göre kaç coini detaylı incele
FUNDING_HIST_PAGES = 3          # funding geçmişi kaç sayfa (100/sayfa) — 300 kayıt z-skor için yeterli
SCAN_WORKERS     = 10           # funding geçmişini kaç coin paralel çekilsin (hız)
