// ==UserScript==
// @name         Funding Trader — MEXC Otomatik Doldur (5 USDT + TP1 %80)
// @namespace    funding-trader.local
// @version      1.1
// @description  Trend Scalp kartindan acilan MEXC futures sayfasinda: grafigi 1m yapar, miktari 5 USDT yapar, TP fiyatini Hedef 1'e kurar (pozisyonun %80'ini kapatacak sekilde), Long/Short'a basmayi sana birakir.
// @match        https://futures.mexc.com/exchange/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  // ─────────────────────────────────────────────────────────────
  // AYARLAR — MEXC arayuzunu degistirirse bu secicileri guncelle.
  // Panelde "bulunamadi" yazan alani, sayfada sag tik > Inceleme ile
  // bakip buraya CSS secicisini yaz. Bos birakirsan sezgisel arama yapar.
  // ─────────────────────────────────────────────────────────────
  const CONFIG = {
    // Miktar (Cost/USDT) giris kutusu. Ornek: 'input[name="inputVolume"]'
    amountSelector: "",
    // TP tetik fiyati giris kutusu (TP/SL panelinde). Ornek: '.tp-price input'
    tpPriceSelector: "",
    // TP/SL'i acan kutucuk/anahtar. Ornek: 'input[name="tpSlSwitch"]'
    tpSlToggleSelector: "",
    // Grafik acilinca hangi zaman dilimine gecsin ("1m" scalp icin). Bos = dokunma.
    chartInterval: "1m",
    // 1m butonunun secicisi (bulamazsa metne gore arar). Ornek: '.chart-interval-1m'
    intervalSelector: "",
    fillDelayMs: 900,     // sayfa yuklenince ilk denemeye kadar bekleme
    retryMs: 700,         // alan gelene kadar tekrar deneme araligi
    maxTries: 20,         // en fazla kac kez dene
  };

  // ─── hash parametrelerini oku (#ft_amt=5&ft_tp=..&ft_cls=80&ft_side=long)
  function readParams() {
    const h = (location.hash || "").replace(/^#/, "");
    if (!h.includes("ft_amt")) return null;
    const p = {};
    h.split("&").forEach((kv) => {
      const [k, v] = kv.split("=");
      if (k) p[decodeURIComponent(k)] = decodeURIComponent(v || "");
    });
    return {
      amt: p.ft_amt || "5",
      tp: p.ft_tp || "",
      cls: p.ft_cls || "80",
      side: (p.ft_side || "long").toLowerCase(),
    };
  }

  const P = readParams();
  if (!P) return; // normal MEXC gezintisinde hic calisma

  // ─── React uyumlu deger yazma (native setter + input/change eventi) ───
  function setReactInput(el, value) {
    if (!el) return false;
    const proto = el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, String(value));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
    return true;
  }

  // ─── gorunur mu ───
  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  // ─── sezgisel: miktar kutusu ───
  function findAmountInput() {
    if (CONFIG.amountSelector) return document.querySelector(CONFIG.amountSelector);
    // gorunur text/number inputlar arasindan, yakininda USDT/Miktar/Cost geceni sec
    const inputs = [...document.querySelectorAll('input[type="text"],input[type="number"],input:not([type])')]
      .filter(visible);
    const scored = inputs.map((el) => {
      const ctx = (el.closest("div,section,form")?.textContent || "").toLowerCase();
      const ph = (el.getAttribute("placeholder") || "").toLowerCase();
      let s = 0;
      if (/usdt|cost|amount|miktar|tutar|hacim|volume|adet|quantity/.test(ph + " " + ctx)) s += 2;
      if (!/tp|sl|stop|take|kar-al|zarar|price|fiyat|trigger|tetik/.test(ph + " " + ctx)) s += 1;
      return { el, s };
    });
    scored.sort((a, b) => b.s - a.s);
    return scored.length ? scored[0].el : null;
  }

  // ─── sezgisel: TP fiyat kutusu ───
  function findTpInput() {
    if (CONFIG.tpPriceSelector) return document.querySelector(CONFIG.tpPriceSelector);
    const inputs = [...document.querySelectorAll('input[type="text"],input[type="number"],input:not([type])')]
      .filter(visible);
    for (const el of inputs) {
      const ctx = (el.closest("div,section,form")?.textContent || "").toLowerCase();
      const ph = (el.getAttribute("placeholder") || "").toLowerCase();
      if (/take.?profit|tp\b|kar.?al|kâr.?al|hedef/.test(ph + " " + ctx)) return el;
    }
    return null;
  }

  // ─── grafik zaman dilimini 1m yap ───
  let intervalDone = false;
  function setChartInterval() {
    if (intervalDone || !CONFIG.chartInterval) return true;
    let btn = CONFIG.intervalSelector ? document.querySelector(CONFIG.intervalSelector) : null;
    if (!btn) {
      // grafik uzerindeki hizli sekmeler: "1m" / "1min" / "1dk" (sadece bu metni iceren yaprak)
      const leaves = [...document.querySelectorAll("div,span,button,li,a")]
        .filter((el) => visible(el) && el.children.length === 0);
      btn = leaves.find((el) => /^\s*1\s*(m|min|dk|dakika)\s*$/i.test(el.textContent || ""));
    }
    if (btn) { try { btn.click(); intervalDone = true; return true; } catch (e) {} }
    return false;
  }

  function findTpSlToggle() {
    if (CONFIG.tpSlToggleSelector) return document.querySelector(CONFIG.tpSlToggleSelector);
    // "TP/SL" yazisini iceren tiklanabilir kutucuk/etiket
    const nodes = [...document.querySelectorAll("label,span,div,button")].filter(visible);
    return nodes.find((n) => /tp\s*\/\s*sl|take.?profit.*stop.?loss/i.test(n.textContent || "")) || null;
  }

  // ─── panel ───
  const panel = document.createElement("div");
  panel.style.cssText = [
    "position:fixed", "top:12px", "right:12px", "z-index:2147483647",
    "width:250px", "background:#12141c", "color:#e8eaf0",
    "border:1px solid #2b2f3d", "border-radius:12px", "padding:13px 14px",
    "font:12.5px/1.5 -apple-system,Segoe UI,Roboto,sans-serif",
    "box-shadow:0 10px 30px rgba(0,0,0,.5)",
  ].join(";");
  const sideColor = P.side === "long" ? "#2fe08a" : "#ff5470";
  const sideLabel = P.side === "long" ? "🟢 LONG" : "🔴 SHORT";
  panel.innerHTML =
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:9px">' +
      '<b style="font-size:13px">⚡ Otomatik Kurulum</b>' +
      '<span id="ftClose" style="cursor:pointer;color:#8a90a2;font-size:15px">×</span></div>' +
    '<div style="color:' + sideColor + ';font-weight:700;margin-bottom:8px">' + sideLabel + "</div>" +
    '<div style="display:grid;grid-template-columns:auto 1fr;gap:5px 10px">' +
      '<span style="color:#8a90a2">Miktar</span><span style="text-align:right;font-weight:700">' + P.amt + " USDT</span>" +
      '<span style="color:#8a90a2">TP (Hedef 1)</span><span style="text-align:right;font-weight:700">' + (P.tp || "—") + "</span>" +
      '<span style="color:#8a90a2">TP\'de kapat</span><span style="text-align:right;font-weight:700">%' + P.cls + "</span>" +
    "</div>" +
    '<div id="ftStatus" style="margin-top:10px;padding-top:9px;border-top:1px solid #2b2f3d;color:#8a90a2;font-size:11.5px"></div>' +
    '<button id="ftRetry" style="margin-top:9px;width:100%;padding:6px;background:#232838;color:#e8eaf0;border:1px solid #2b2f3d;border-radius:8px;cursor:pointer;font-weight:600">↻ Tekrar doldur</button>' +
    '<div style="margin-top:8px;font-size:10.5px;color:#6b7080">Long/Short butonuna basmak sana kaldi. TP\'yi ' +
      '%' + P.cls + ' kapatacak sekilde ayarla (pozisyon acildiktan sonra pozisyon satirindan TP miktarini %' + P.cls + ' yap).</div>';
  document.body.appendChild(panel);
  const statusEl = panel.querySelector("#ftStatus");
  panel.querySelector("#ftClose").onclick = () => panel.remove();

  function status(lines) {
    statusEl.innerHTML = lines.map((l) => (l.ok ? "✅ " : l.warn ? "⚠️ " : "◻️ ") + l.t).join("<br>");
  }

  // ─── doldurma denemesi ───
  function attempt() {
    const st = [];

    // 0) grafik zaman dilimi → 1m
    if (CONFIG.chartInterval) {
      const okI = setChartInterval();
      st.push(okI
        ? { ok: true, t: "Grafik " + CONFIG.chartInterval + " yapildi" }
        : { warn: true, t: "Grafik " + CONFIG.chartInterval + " butonu bulunamadi — elle sec" });
    }

    // 1) miktar
    const amtEl = findAmountInput();
    if (amtEl) { setReactInput(amtEl, P.amt); st.push({ ok: true, t: "Miktar " + P.amt + " USDT girildi" }); }
    else st.push({ warn: true, t: "Miktar kutusu bulunamadi — elle " + P.amt + " gir" });

    // 2) TP/SL ac
    if (P.tp) {
      const tog = findTpSlToggle();
      if (tog) { try { tog.click(); } catch (e) {} }
      // toggle sonrasi TP kutusu gelmis olabilir → kisa gecikme ile ara
      setTimeout(() => {
        const tpEl = findTpInput();
        if (tpEl) { setReactInput(tpEl, P.tp); status(readStatus(true)); }
        else status(readStatus(false));
      }, 350);
      st.push({ ok: !!tog, warn: !tog, t: tog ? "TP/SL acildi, fiyat kuruluyor…" : "TP/SL anahtari bulunamadi — elle ac" });
    }

    status(st);

    function readStatus(tpOk) {
      const base = st.slice();
      base.push(tpOk
        ? { ok: true, t: "TP fiyati " + P.tp + " girildi" }
        : { warn: true, t: "TP kutusu bulunamadi — elle " + P.tp + " gir" });
      return base;
    }
  }

  // ─── alan gelene kadar tekrar dene ───
  let tries = 0;
  function loop() {
    tries++;
    setChartInterval(); // grafik formdan bagimsiz yuklenebilir → erken dene
    if (findAmountInput()) { attempt(); return; }
    if (tries < CONFIG.maxTries) setTimeout(loop, CONFIG.retryMs);
    else status([{ warn: true, t: "Form yuklenemedi — panel degerlerini elle gir" }]);
  }
  setTimeout(loop, CONFIG.fillDelayMs);
  panel.querySelector("#ftRetry").onclick = () => { tries = 0; loop(); };
})();
