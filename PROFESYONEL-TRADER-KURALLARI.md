# 📊 Profesyonel Trader İşlem Kuralları & Analiz Çerçevesi

> Bu dosya, bir coin'de işlem açmadan önce baştan sona uygulanan profesyonel karar sürecidir.
> Amaç: **duyguyla değil, kurallarla** işlem açmak. Her işlem bu checklist'ten geçmeden AÇILMAZ.
> Odak: Funding-rate / squeeze mantığı + klasik teknik analiz (evren: Bybit + Coinglass).

---

## 0. TEMEL FELSEFE — Trader Zihniyeti

| İlke | Açıklama |
|------|----------|
| **Sermaye korunması #1** | Para kazanmadan önce para kaybetmemeyi öğren. Nakit de bir pozisyondur. |
| **Olasılık oyunu** | Hiçbir işlem %100 değildir. Biz "kesin" değil, "getiri/risk'i lehimize" işlem ararız. |
| **Kurala uy, tahmine değil** | Piyasa haklı, sen değil. Plan bozulunca ego değil stop konuşur. |
| **İşlem YOKSA da kazanç** | Setup yoksa beklemek en kârlı harekettir. Zorlama işlem = zarar. |
| **Ardışık zarar limiti** | Üst üste 2-3 stop yediysen o gün DURDUR. Revenge trade yasak. |
| **Asimetri ara** | 1 risk edip 2-3+ kazanılacak yerler. R:R < 1.5 ise geç. |

---

## 1. İŞLEM ÖNCESİ MAKRO / BAĞLAM KONTROLÜ

İşleme girmeden önce "genel hava" nasıl?

- [ ] **BTC yönü** — BTC düşerken altcoin long açmak akıntıya karşı yüzmektir. Önce lider coini oku.
- [ ] **BTC dominance (BTC.D)** — Yükseliyorsa para BTC'ye kaçıyor, altcoinler zayıflar. Düşüyorsa altseason lehte.
- [ ] **Total / Total2 (piyasa hacmi)** — Genel piyasa risk-on mu risk-off mu?
- [ ] **Haber / event riski** — FOMC, CPI, büyük unlock, listeleme, halving vb. yaklaşıyor mu? Belirsizlik öncesi pozisyon küçültülür.
- [ ] **Seans / saat** — Likidite düşük saatlerde (ör. hafta sonu, Asya ölü saatleri) fitil riski yüksek.

> **Kural:** Bağlam aleyhineyse setup mükemmel bile olsa pozisyon boyutunu yarıya indir veya geç.

---

## 2. TREND & YAPI ANALİZİ (Multi-Timeframe)

Her zaman **büyükten küçüğe** bak. Üst zaman dilimi yönü belirler, alt zaman dilimi giriş verir.

| Zaman Dilimi | Görevi |
|--------------|--------|
| **1D / 4H** | Ana trend + majör destek/direnç. "Nerede oynuyoruz?" |
| **1H / 15M** | Yapı, momentum, tetik bölgesi. "Ne zaman?" |
| **5M / 1M** | Sadece hassas giriş & stop yerleşimi. |

**Trend okuma:**
- **Yükseliş yapısı** = Higher High (HH) + Higher Low (HL). Long önceliği.
- **Düşüş yapısı** = Lower High (LH) + Lower Low (LL). Short önceliği.
- **Yatay (range)** = Alt banttan al, üst bantta sat. Kırılım bekle.

> **Kural:** Trende karşı işlem, ancak net bir yapı kırılımı (BOS / CHoCH) + teyit varsa açılır. Aksi halde trend yönünde işlem yap.

---

## 3. DESTEK / DİRENÇ & LİKİDİTE HARİTASI

İşlemin iskeleti burasıdır. Fiyat "boşlukta" değil, seviyelerde döner.

- [ ] **Majör yatay S/R** — Geçmişte defalarca tepki verilen fiyatlar (1D/4H).
- [ ] **Önceki HH/LL, günlük/haftalık açılış, önemli fitil dipleri.**
- [ ] **Likidite havuzları** — Eşit dipler/tepeler (equal highs/lows), yuvarlak sayılar. Buralarda stop avı olur.
- [ ] **VWAP / hacim profili (POC, VAH, VAL)** — Kurumsal ortalama maliyet bölgeleri.
- [ ] **Fibonacci** — Düzeltmede 0.5–0.618–0.66 "golden pocket" tepki bölgesi.
- [ ] **Order block / FVG (imbalance)** — Fiyatın hızlı geçtiği ve doldurmaya döneceği boşluklar.

> **Kural:** Long, desteğe yakın açılır; direncin dibinden long açılmaz. Short, dirence yakın açılır; desteğin üstünden short açılmaz. "Ortada" işlem = kötü R:R.

---

## 4. MOMENTUM & GÖSTERGE TEYİDİ

Göstergeler karar VERMEZ, kararı TEYİT eder. Tek başına asla.

- [ ] **RSI** — Aşırı alım (>70) / aşırı satım (<30). Daha önemlisi: **uyumsuzluk (divergence)** fiyat yeni dip/tepe yaparken RSI yapmıyorsa dönüş sinyali.
- [ ] **EMA'lar (20/50/200)** — Fiyat EMA'ların üstünde mi altında mı? Diziliş trendi doğruluyor mu? EMA'lar dinamik S/R.
- [ ] **Hacim** — Kırılıma hacim eşlik ediyor mu? Hacimsiz kırılım = tuzak (fakeout).
- [ ] **MACD** — Momentum yönü ve kesişimler.

> **Kural:** En az 2 bağımsız teyit (ör. seviye + divergence + hacim) olmadan tetik çekilmez.

---

## 5. 🔥 FUNDING RATE & TÜREV VERİSİ (Ana Uzmanlık Alanı)

Bu senin asıl kenarın. Squeeze mantığı burada kurulur.

- [ ] **Funding rate yönü & büyüklüğü**
  - **Aşırı pozitif funding** → Longlar aşırı kalabalık, longlar shortlara ödüyor → **long squeeze / düzeltme riski** (short fırsatı olabilir).
  - **Aşırı negatif funding** → Shortlar aşırı kalabalık, shortlar longlara ödüyor → **short squeeze / yukarı sıkışma riski** (long fırsatı olabilir).
- [ ] **Open Interest (OI)**
  - Fiyat ↑ + OI ↑ = yeni para, sağlıklı trend.
  - Fiyat ↑ + OI ↓ = short kapanışı (squeeze), yakıt bitebilir.
  - Fiyat ↓ + OI ↑ = yeni shortlar geliyor, agresif düşüş.
  - OI zirvede + funding ekstrem = **temizlik (liquidation) yakın**.
- [ ] **Long/Short oranı** — Kalabalık hangi tarafta? Kalabalığın tersi genelde kazanır.
- [ ] **Likidasyon haritası (Coinglass)** — Üstte ve altta likidite kümeleri nerede? Fiyat mıknatıs gibi oralara çekilir.
- [ ] **Funding zamanlaması** — Funding ödeme saatine yakın ekstrem oranlarda ani ters hareket olabilir.

> **Squeeze Kuralı:** Ekstrem funding + ters yönde likidasyon kümesi + seviye teyidi = yüksek olasılıklı squeeze işlemi. Kalabalık aşırı tek taraflıysa, o tarafın "yakıtı" biter.

---

## 6. RİSK YÖNETİMİ (İşlemin Kalbi — Pazarlıksız)

Burası atlanırsa gerisi anlamsız. En iyi trader bile bunsuz batar.

| Kural | Değer / Mantık |
|-------|----------------|
| **İşlem başına risk** | Sermayenin **%1–2'si** MAX. Asla "tüm parayla" değil. |
| **Stop-Loss ZORUNLU** | Her işlemde, girişten ÖNCE belirlenir. Stopsuz işlem = kumar. |
| **Stop yeri** | Teknik yapının geçersiz olduğu yer (destek altı / direnç üstü), rakam güzel diye değil. |
| **Risk:Reward (R:R)** | Minimum **1:1.5**, tercihen **1:2–1:3+**. Düşükse işlem YOK. |
| **Pozisyon boyutu** | Boyut = (Sermaye × %risk) ÷ (giriş–stop mesafesi). Stop uzaksa boyut küçülür. |
| **Kaldıraç** | Kaldıraç risk BÜYÜTMEZ, likidasyonu yakınlaştırır. Riski stop belirler, kaldıraç değil. Düşük tut. |
| **Likidasyon mesafesi** | Stop'un çok çok üstünde olmalı. Stop'a değmeden likit olmak = sistem hatası. |

> **Altın Kural:** Kaybedeceğin miktarı işleme GİRMEDEN bileceksin. Bilmiyorsan girme.

---

## 7. GİRİŞ TETİĞİ (Entry Trigger)

Seviyeye "gelmesi" yetmez, "tepki/teyit" gerekir. Bıçağı düşerken tutma.

**Giriş tipleri:**
1. **Kırılım (Breakout)** — Seviye kırılır + hacim gelir + retest tutar. En sağlamı retest'li giriş.
2. **Sıçrama/Tepki (Bounce)** — Destekte alıcı mumu (pin bar, engulfing, doji tepkisi) sonrası.
3. **Retest / geri çekilme** — Kırılan direncin destek olarak test edilmesi.

- [ ] Mum kapanışı teyidi (fitile değil, kapanışa güven).
- [ ] Alt zaman diliminde momentum lehe dönüyor mu?

> **Kural:** "Belki döner" ile girilmez. "Döndüğünü gösteriyor" ile girilir. Teyit yoksa kaçan işlem, kaybettiren işlemden iyidir.

---

## 8. İŞLEM YÖNETİMİ (Girdikten Sonra)

- [ ] **Kısmi kâr al (TP1/TP2/TP3)** — İlk hedefte pozisyonun bir kısmını kapat, riski azalt.
- [ ] **Stop'u girişe çek (break-even)** — TP1 sonrası stop giriş seviyesine → risksiz işlem.
- [ ] **Trailing stop** — Trend güçlüyse kârı takip ettir, erken kapatma.
- [ ] **Plan değiştirme** — Sadece yapı değişirse. Sıkıldığın/korktuğun için DEĞİL.
- [ ] **Ekleme (add) sadece kâr varken** — Zarara ekleme (averaging down) yasak (plansızsa).

---

## 9. İŞLEM SONRASI — Kayıt & Ders

- [ ] Her işlemi **journal'a** yaz: giriş, stop, hedef, sebep, sonuç, duygu.
- [ ] "Kural mı hatalıydı, uygulama mı?" ayır. Kurala uyup kaybetmek OK; kuralı çiğnemek DEĞİL.
- [ ] Haftalık gözden geçir: en çok para hangi setup'tan geldi? Ona odaklan.

---

## ✅ İŞLEM ÖNCESİ HIZLI CHECKLIST (Tetik Öncesi Son Bakış)

```
[ ] 1. Makro/BTC bağlamı lehte mi? (BTC yönü, dominance)
[ ] 2. Ana trend yönü ne? İşlem trend yönünde mi?
[ ] 3. Sağlam bir seviyede miyim (destek/direnç/likidite)?
[ ] 4. En az 2 teyit var mı (seviye + momentum/divergence + hacim)?
[ ] 5. Funding/OI ne söylüyor? Kalabalık nerede, squeeze var mı?
[ ] 6. Stop yerim net mi? (yapının bozulduğu yer)
[ ] 7. R:R ≥ 1.5 mi?
[ ] 8. Pozisyon boyutu risk %1-2'yi aşmıyor mu?
[ ] 9. Giriş tetiğim geldi mi (kapanış teyidi)?
[ ] 10. Duygusal mıyım / revenge trade mi? → Öyleyse GİRME.
```

> **10 madde de EVET değilse → işlem YOK.** Bir tanesi bile "hayır"sa beklersin.

---

## 📋 COİN ANALİZ RAPORU FORMATI (Bana coin verdiğinde bu şablonu doldururum)

```
COİN: XXX/USDT
TARİH/SAAT:

1) BAĞLAM: BTC yönü, dominance, genel hava
2) TREND (1D/4H/1H): Yapı, HH/HL veya LH/LL
3) SEVİYELER:
   - Majör Dirençler: R1, R2, R3
   - Majör Destekler: S1, S2, S3
   - Likidite/POC/Fib bölgeleri
4) MOMENTUM: RSI, EMA dizilişi, hacim, divergence
5) FUNDING & TÜREV: funding rate, OI, long/short oranı, likidasyon kümeleri
6) SENARYOLAR:
   - Long senaryosu: tetik, giriş, stop, hedefler, R:R
   - Short senaryosu: tetik, giriş, stop, hedefler, R:R
7) SONUÇ / KARAR:
   - İşlem mantıklı mı? (EVET / HAYIR / BEKLE)
   - Yön, güven skoru (1-10)
   - Neden?
8) RİSK NOTU: pozisyon boyutu, dikkat edilecek event/seviye
```

---

### ⚠️ Yasal Not
Bu bir eğitim/analiz çerçevesidir, **yatırım tavsiyesi değildir**. Piyasa risklidir; her kararın sorumluluğu sana aittir. Kaybetmeyi göze alamayacağın parayla işlem yapma.
