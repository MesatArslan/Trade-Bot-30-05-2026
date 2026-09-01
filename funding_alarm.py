#!/usr/bin/env python3
"""FUNDING ALARM — Bybit funding rate izleyici.
   |funding| >= %0.4 olan ve YUKSELEN coinleri yakalar, masaustu bildirimi atar.
   Pozitif -> longlar kalabalik (SHORT adayi). Negatif -> shortlar kalabalik (LONG/squeeze adayi).
   launchd/cron ile her ~5 dk calistirilir; durumu .funding_alarm_state.json'da tutar."""
import urllib.request, json, time, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_F = os.path.join(HERE, ".funding_alarm_state.json")
LOG_F   = os.path.join(HERE, "funding_alarm.log")

THRESHOLD   = 0.004   # %0.4  (|funding| bu esigi gececek)
RISE_DELTA  = 0.001   # tekrar haber vermek icin en az %0.1 daha artmali
COOLDOWN    = 30*60   # ayni coin icin 30 dk icinde tekrar bildirim yok (buyuk artis haric)
MIN_TURN    = 2_000_000  # $2M+ 24s hacim (olu coinleri ele)
MAX_ALERTS  = 6       # tek seferde en fazla kac coin bildirilsin

BASE = "https://api.bybit.com"
def get(path, params, retries=4):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    for i in range(retries):
        try:
            req = urllib.request.Request(f"{BASE}{path}?{q}", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == retries-1: raise
            time.sleep(1.5*(i+1))

def notify(title, msg):
    # ensure_ascii=False: emoji/UTF-8 harfleri AppleScript'te literal kalsin, tirnak/backslash kacisin
    t = json.dumps(title, ensure_ascii=False)
    m = json.dumps(msg, ensure_ascii=False)
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification {m} with title {t} sound name "Ping"'],
                       check=False)
    except Exception:
        pass

def log(line):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_F, "a") as f:
        f.write(f"{ts}  {line}\n")

def load_state():
    try:
        return json.load(open(STATE_F))
    except Exception:
        return {}

def main():
    state = load_state()
    now = time.time()
    try:
        d = get("/v5/market/tickers", {"category": "linear"})
    except Exception as e:
        log(f"API hata: {e}"); return

    cands = []  # (rate, sym, ch24, turn, nextFund)
    for t in d["result"]["list"]:
        s = t["symbol"]
        if not s.endswith("USDT"): continue
        try:
            fr = float(t.get("fundingRate") or 0)
            ch = float(t.get("price24hPcnt") or 0)*100
            turn = float(t.get("turnover24h") or 0)
            nft = int(t.get("nextFundingTime") or 0)
        except Exception:
            continue
        if turn < MIN_TURN: continue
        if abs(fr) >= THRESHOLD:
            cands.append((fr, s, ch, turn, nft))

    alerts = []
    new_state = {}
    for fr, s, ch, turn, nft in cands:
        prev = state.get(s, {})
        prev_rate = prev.get("rate")
        prev_alert_rate = prev.get("alert_rate")
        prev_alert_ts = prev.get("alert_ts", 0)

        newly_crossed = (prev_rate is None) or (abs(prev_rate) < THRESHOLD)
        rising = (prev_rate is None) or (abs(fr) > abs(prev_rate))  # bir onceki taramaya gore artiyor mu
        cooled = (now - prev_alert_ts) >= COOLDOWN
        big_further = (prev_alert_rate is not None) and (abs(fr) - abs(prev_alert_rate) >= RISE_DELTA)

        do_alert = False
        if newly_crossed and rising:
            do_alert = True                       # esigi yeni gecti ve yukseliyor
        elif rising and (cooled or big_further):
            do_alert = True                       # zaten esik ustunde ama daha da yukseldi

        ns = {"rate": fr}
        if do_alert:
            ns["alert_rate"] = fr
            ns["alert_ts"] = now
            alerts.append((fr, s, ch, nft))
        else:
            # bildirim yoksa onceki alert bilgisini koru
            if prev_alert_rate is not None: ns["alert_rate"] = prev_alert_rate
            ns["alert_ts"] = prev_alert_ts
        new_state[s] = ns

    json.dump(new_state, open(STATE_F, "w"))

    if not alerts:
        log(f"tarama ok — {len(cands)} coin esikte, yeni bildirim yok")
        return

    # En yuksek |oran| basta, cap uygula
    alerts.sort(key=lambda x: -abs(x[0]))
    shown = alerts[:MAX_ALERTS]
    lines = []
    for fr, s, ch, nft in shown:
        yon = "🟢POZITIF→SHORT adayi" if fr > 0 else "🔴NEGATIF→LONG/squeeze"
        sec = (nft/1000 - now)
        kalan = f"{int(sec//3600)}s{int(sec%3600//60)}dk" if sec > 0 else "-"
        lines.append(f"{s} {fr*100:+.3f}% ({yon}) 24s%{ch:+.0f} fund:{kalan}")

    title = f"⚡ Funding Alarm — {len(alerts)} coin"
    msg = " | ".join(f"{s} {fr*100:+.2f}%" for fr, s, ch, nft in shown)
    notify(title, msg)
    for ln in lines:
        log("ALARM  " + ln)
    if len(alerts) > MAX_ALERTS:
        log(f"(+{len(alerts)-MAX_ALERTS} coin daha esikte)")

if __name__ == "__main__":
    main()
