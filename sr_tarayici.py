#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Terminalden destek/direnç taraması. Kullanım: python3 sr_tarayici.py [5|15] [--sinyal]"""
import sys
from sr_board import sr_board
tf = next((a for a in sys.argv[1:] if a in ("5", "15")), "5"); only = "--sinyal" in sys.argv
d = sr_board(tf); rows = d["candidates"]
if only: rows = [r for r in rows if r["signal"]]
print(f"{d['count']} coin · sinyal {d['signals']} (L{d['longs']}/S{d['shorts']}) · seviyeye yakın {d['near']} · TF {tf}dk\n")
f = lambda x: f"{x:.6g}"
for r in rows[:40]:
    s = r["symbol"].replace("USDT", ""); sg = r["signal"]
    S = r["supports"][0] if r["supports"] else None; R = r["resistances"][0] if r["resistances"] else None
    line = f"{s:<12} {f(r['price']):>10}  D1 {f(R['p']) if R else '—':>10} ({R['dist_atr'] if R else '-'}ATR/{R['n'] if R else '-'}t)  S1 {f(S['p']) if S else '—':>10} ({S['dist_atr'] if S else '-'}ATR/{S['n'] if S else '-'}t)"
    if sg: line += f"  ★ {sg['type'].upper()} {sg['side']} @{f(sg['level'])} [{sg['src']}, {sg['n']}t] giriş {f(sg['entry'])} stop {f(sg['stop'])} (-{sg['stop_pct']}%) hedef {f(sg['tp'])} (+{sg['tp_pct']}%)"
    elif r["near"]: line += f"  📍 {'destekte' if r['near']['kind']=='S' else 'dirençte'} ({r['near']['dist_atr']} ATR)"
    print(line)
