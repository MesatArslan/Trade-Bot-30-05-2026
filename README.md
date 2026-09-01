# Funding Trader

MEXC Futures'ta **funding kaynaklı sıkışma (short squeeze / long flush)** kurulumlarını
tarayan, yön kararı veren ve giriş/çıkışta sana canlı rehberlik eden sistem.

> Sadece **okuma** yapar. Hiçbir emir göndermez, API anahtarı istemez.
> Kararı ve emri sen verirsin. Bu bir sinyal aracıdır, otomatik bot değil.

---

## En kolay kullanım — web arayüzü

1. Finder'da klasörü aç, **`Başlat.command`** dosyasına **çift tıkla.**
   (İlk açılışta "geliştirici doğrulanamadı" derse: sağ tık → Aç → Aç.)
2. Tarayıcı kendiliğinden **http://localhost:8787** adresini açar.
3. Her şeyi butonlarla yönetirsin — terminal komutu yok.

Web arayüzünde:
- Üstte **TR saati** ve bir sonraki kesime **geri sayım.**
- **"Tara ve Planla"** butonu → sıkışmış coinleri yön + planla listeler.
- İşlenebilir bir coinde **"Canlı Takip Et"** → 5 dk referans → **GİRİŞ SİNYALİ**
  → sen MEXC'te açıp **"Pozisyonu Açtım"a** basarsın → **ÇIKIŞ uyarısı.**

Kapatmak için: `Başlat.command`'ın açtığı terminal penceresinde Ctrl+C.

> Not: Site **senin bilgisayarında** çalışır (localhost). İnternette
> yayınlanmaz, kimse erişemez. MEXC verisini buradan çeker.

---

## Kurulum (bir kez, terminalden — istersen)

```bash
cd "/Users/mea/Desktop/Funding trader"
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Ayarların hepsi [config.py](config.py) içinde. En azından şunları kendine göre düzenle:
- `BALANCE_USDT` — bakiyen
- `RISK_PER_TRADE` — işlem başına risk (0.01 = %1)
- `MAX_LEVERAGE` — üst sınır (sistem 8–20x ister; 100x bu sistemle uyumsuz)

---

## Günlük akış — funding saatlerinde ne yapacaksın

8 saatlik pariteler için kesimler (**Türkiye saati**): **03:00 · 11:00 · 19:00**

### 1) Kesimden ~30 dk ÖNCE — adayları gör
```bash
./.venv/bin/python trade.py scan
```
1000+ pariteyi huniden geçirir, sıkışmış kalabalığı olan 1–3 coini listeler.
Liste boşsa **işlem yok** — bu normaldir, zorlama.

### 2) Kesimden HEMEN SONRA — yön + plan
```bash
./.venv/bin/python trade.py plan
```
Her aday için yön (LONG/SHORT), giriş/stop/hedef ve pozisyon boyutunu verir.
"Tradeable çeyrekte değil" diyorsa o coini atla.

### 3) Bir coin seçtin — canlı asistan
```bash
./.venv/bin/python trade.py watch SUI_USDT
```
Sırasıyla: 5 dk referans mum oluşturur → kırılım tetiğini bekler →
**GİRİŞ SİNYALİ** verir → sen pozisyonu açıp ENTER'a basarsın →
OI durması + T+90dk zaman stopuyla **çıkış** uyarısı verir.

25 dk içinde tetik gelmezse olayı otomatik iptal eder.

> İstediğin an `./.venv/bin/python trade.py schedule` ile bir sonraki kesime
> kaç dk kaldığını görebilirsin.

---

## ÖNEMLİ: Logger'ı bugün başlat

MEXC açık pozisyon (OI) geçmişini geriye dönük veremiyor — sadece anlık.
Backtest ve OI-tabanlı çıkış için bu veriyi **bugünden** biriktirmen şart.

Ayrı bir terminal aç ve şunu çalıştır, **açık bıraksın**:
```bash
cd "/Users/mea/Desktop/Funding trader"
./.venv/bin/python logger.py
```
Dakikada bir tüm pariteleri `data/snapshots/YYYYMMDD.csv`'ye yazar (~günde 130 MB).
3–4 hafta sonra dört-çeyrek matrisini gerçek veriyle test edebilirsin.

> Bilgisayar kapanınca durur. Kalıcı çalıştırmak istersen macOS `launchd`
> veya `caffeinate ./.venv/bin/python logger.py` kullan.

---

## Çapraz borsa teyidi (Binance) — rozetler

MEXC funding'i tek başına yanıltıcı olabilir. Araç her coini **Binance** ile
karşılaştırır (Coinglass mantığı, ücretsiz):

| Rozet | Anlam |
|---|---|
| ✓ **Binance onaylı** | İki borsa da aynı yönde uç → **gerçek**, piyasa geneli kalabalık. En güvenilir. |
| ⚠ **Binance ters** | Binance funding ters yönde → şüpheli, skoru düşürülür. Dikkat. |
| ⚠ **sadece MEXC** | Coin Binance'te yok (genelde tokenize hisse / MEXC'e özel). Sinyal MEXC'e özgü, ihtiyatlı ol. |
| Binance zayıf | İki borsada da funding küçük → sinyal yok. |

**Pratik kural:** "✓ Binance onaylı" olmayan bir sinyale ihtiyatlı yaklaş.

---

## Dosyalar
| Dosya | İş |
|---|---|
| [config.py](config.py) | Tüm eşikler ve hesap ayarları |
| [mexc.py](mexc.py) | MEXC public API istemcisi (okuma) |
| [binance.py](binance.py) | Binance funding — çapraz borsa teyidi |
| [scan.py](scan.py) | Tarama hunisi (Aşama 0–2) |
| [decide.py](decide.py) | Yön kararı + pozisyon planı (Aşama 3) |
| [trade.py](trade.py) | **Ana dosya** — schedule / scan / plan / watch |
| [logger.py](logger.py) | Anlık görüntü logger'ı (OI geçmişi) |

---

## Kurulumların mantığı (hatırlatma)
| Funding | Fiyat | Kalabalık | İşlem |
|---|---|---|---|
| Aşırı **negatif** | **Yükseliyor** | Shortlar sıkışmış | **LONG** (short squeeze) |
| Aşırı **pozitif** | Parabolik + momentum kırık | Longlar sıkışmış | **SHORT** (long flush) |

Kalabalığın konumu fiyatla **çelişiyorsa** gir; çelişmiyorsa geç.
Hedef işlem başına **%1–2**. Stop %1.5–3. Bu yüzden kaldıraç 8–20x, asla 100x değil.
