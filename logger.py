"""
Anlık görüntü logger'ı — dakikada bir tüm pariteleri CSV'ye yazar.
AMAÇ: MEXC açık pozisyon (holdVol) geçmişini public API'den geriye dönük
alamıyorsun. Backtest ve OI-tabanlı çıkış için bu veriyi BUGÜN biriktirmeye
başlaman şart. Bunu bir kez başlat, arka planda çalışsın.

Kullanım:
    ./.venv/bin/python logger.py
Durdurmak için Ctrl+C.
"""
import os
import csv
import time
import datetime as dt
import mexc

OUT_DIR = os.path.join(os.path.dirname(__file__), "data", "snapshots")
os.makedirs(OUT_DIR, exist_ok=True)

COLS = ["ts_ms", "symbol", "lastPrice", "fairPrice", "indexPrice",
        "fundingRate", "holdVol", "amount24", "bid1", "ask1"]


def _day_file():
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    return os.path.join(OUT_DIR, f"{day}.csv")


def log_once():
    snap = mexc.snapshot()
    if not snap:
        return 0
    path = _day_file()
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(COLS)
        for r in snap:
            w.writerow([r.get(c, "") for c in COLS])
    return len(snap)


def loop(interval=60):
    print(f"Logger başladı. Çıktı: {OUT_DIR}")
    print(f"Her {interval} sn'de bir tüm pariteler yazılıyor. Durdurmak: Ctrl+C")
    n = 0
    while True:
        t0 = time.time()
        try:
            wrote = log_once()
            n += 1
            stamp = dt.datetime.now().strftime("%H:%M:%S")
            print(f"  [{stamp}] #{n:>5}  {wrote} satır yazıldı  -> {os.path.basename(_day_file())}")
        except KeyboardInterrupt:
            print("\nLogger durduruldu.")
            return
        except Exception as e:
            print(f"  hata: {e}")
        # interval'a göre uyu (işlem süresini düş)
        sleep = max(1.0, interval - (time.time() - t0))
        try:
            time.sleep(sleep)
        except KeyboardInterrupt:
            print("\nLogger durduruldu.")
            return


if __name__ == "__main__":
    loop(60)
