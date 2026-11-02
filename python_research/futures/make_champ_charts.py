"""Generate a pure-Python PNG chart (using stdlib zlib + struct) of the Schwartz Championship
equity curves + extract the full trades and sampled curves for Canvas.
"""
from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

LAB = Path(__file__).resolve().parent


def write_png(path: Path, width: int, height: int, rgb_buf: bytearray) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb_buf[y * stride:(y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def draw_rect(buf: bytearray, w: int, h: int, x0: int, y0: int, x1: int, y1: int, col: tuple[int, int, int]):
    x0, x1 = max(0, x0), min(w - 1, x1)
    y0, y1 = max(0, y0), min(h - 1, y1)
    r, g, b = col
    for y in range(y0, y1 + 1):
        row = y * w * 3
        for x in range(x0, x1 + 1):
            idx = row + x * 3
            buf[idx] = r
            buf[idx + 1] = g
            buf[idx + 2] = b


def draw_line(buf: bytearray, w: int, h: int, x0: int, y0: int, x1: int, y1: int, col: tuple[int, int, int], thick: int = 2):
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for s in range(steps + 1):
        x = int(round(x0 + (x1 - x0) * s / steps))
        y = int(round(y0 + (y1 - y0) * s / steps))
        draw_rect(buf, w, h, x - thick // 2, y - thick // 2, x + thick // 2, y + thick // 2, col)


def render_equity_png():
    dump = json.loads((LAB / "championship_dump.json").read_text(encoding="utf-8"))
    # Plot 1: Last Quarter (QTR) equity comparison across CORE_4_ALTS (Mode5_5x, MaxPitbull_6x, UltraChamp_7x, TitanChamp_8x) vs ALL_9_PAIRS Mode5_5x
    w, h = 1100, 520
    buf = bytearray([15, 22, 36] * (w * h))  # dark slate background #0f1624

    pad_l, pad_r, pad_t, pad_b = 70, 40, 45, 50
    pw = w - pad_l - pad_r
    ph = h - pad_t - pad_b

    # Grid lines
    for i in range(6):
        gy = pad_t + int(ph * i / 5)
        draw_line(buf, w, h, pad_l, gy, w - pad_r, gy, (34, 45, 69), 1)
    for i in range(7):
        gx = pad_l + int(pw * i / 6)
        draw_line(buf, w, h, gx, pad_t, gx, h - pad_b, (34, 45, 69), 1)

    series_keys = [
        ("ALL_9_PAIRS__Mode5_5x", (148, 163, 184)),       # muted gray-blue: 9 coins (+70.1%)
        ("CORE_4_ALTS__Mode5_5x", (59, 130, 246)),        # blue: 4 clean alts 5x (+105.0%)
        ("CORE_4_ALTS__MaxPitbull_6x", (245, 158, 11)),   # amber: 4 clean alts 6x (+156.1%)
        ("CORE_4_ALTS__UltraChamp_7x", (16, 185, 129)),   # emerald: 4 clean alts 7x (+200.0%)
        ("CORE_4_ALTS__TitanChamp_8x", (236, 72, 153)),   # magenta: 4 clean alts 8x (+249.7%)
    ]

    all_pts = []
    for k, _c in series_keys:
        crv = dump["runs"][k]["periods"]["QTR"]["curve"]
        all_pts.extend(crv)
    t_min = min(p[0] for p in all_pts)
    t_max = max(p[0] for p in all_pts)
    v_min = 1800.0
    v_max = max(p[1] for p in all_pts) * 1.04

    for k, col in series_keys:
        crv = dump["runs"][k]["periods"]["QTR"]["curve"]
        pts = []
        for ts, eq in crv:
            x = pad_l + int((ts - t_min) / max(t_max - t_min, 1) * pw)
            y = pad_t + ph - int((eq - v_min) / max(v_max - v_min, 1.0) * ph)
            pts.append((x, y))
        for i in range(1, len(pts)):
            draw_line(buf, w, h, pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1], col, 3)

    out_qtr = LAB / "schwartz_championship_qtr.png"
    write_png(out_qtr, w, h, buf)

    # Plot 2: Full History (2022..2026) equity curves for CORE_4_ALTS (5x, 6x, 7x)
    buf2 = bytearray([15, 22, 36] * (w * h))
    for i in range(6):
        gy = pad_t + int(ph * i / 5)
        draw_line(buf2, w, h, pad_l, gy, w - pad_r, gy, (34, 45, 69), 1)
    for i in range(7):
        gx = pad_l + int(pw * i / 6)
        draw_line(buf2, w, h, gx, pad_t, gx, h - pad_b, (34, 45, 69), 1)

    full_keys = [
        ("ALL_9_PAIRS__Mode5_5x", (148, 163, 184)),       # $10,990
        ("CORE_4_ALTS__Mode5_5x", (59, 130, 246)),        # $27,657
        ("CORE_4_ALTS__MaxPitbull_6x", (245, 158, 11)),   # $63,240
        ("CORE_4_ALTS__UltraChamp_7x", (16, 185, 129)),   # $101,070
    ]
    all_full = []
    for k, _c in full_keys:
        all_full.extend(dump["runs"][k]["periods"]["FULL"]["curve"])
    tf_min = min(p[0] for p in all_full)
    tf_max = max(p[0] for p in all_full)
    vf_min = 1500.0
    vf_max = max(p[1] for p in all_full) * 1.03

    for k, col in full_keys:
        crv = dump["runs"][k]["periods"]["FULL"]["curve"]
        pts = []
        for ts, eq in crv:
            x = pad_l + int((ts - tf_min) / max(tf_max - tf_min, 1) * pw)
            y = pad_t + ph - int((eq - vf_min) / max(vf_max - vf_min, 1.0) * ph)
            pts.append((x, y))
        for i in range(1, len(pts)):
            draw_line(buf2, w, h, pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1], col, 3)

    out_full = LAB / "schwartz_championship_full.png"
    write_png(out_full, w, h, buf2)
    print("WROTE PNGs:", out_qtr, out_full)

    # Also print QTR trades for CORE_4_ALTS__UltraChamp_7x and ALL_9_PAIRS__Mode5_5x
    qtr_4 = dump["runs"]["CORE_4_ALTS__UltraChamp_7x"]["periods"]["QTR"]["trades"]
    print(f"\n=== ALL {len(qtr_4)} TRADES IN LAST QUARTER (CORE_4_ALTS: ETH, SOL, ADA, SUI — UltraChamp 7x: +200.0%, DD 15.9%) ===")
    for idx, t in enumerate(qtr_4, 1):
        side_s = "LONG" if t["side"] == 1 else "SHORT"
        pyr_s = "+PYR" if t["pyr"] else "    "
        print(
            f"{idx:2d}. {t['time']} -> {t['exit_time']} ({t['hold_h']:2d}h) | {t['sym']:7s} {side_s:5s} {pyr_s} | "
            f"margin=${t['margin']:7.1f} lev={t['eff_lev']:4.2f}x | move={t['raw_net_pct']:+6.2f}% | "
            f"PnL=${t['pnl_usd']:+8.2f} ({t['pnl_pct_eq']:+6.2f}% eq) | Eq=${t['eq']:8.2f} | {t['reason']}"
        )


if __name__ == "__main__":
    render_equity_png()
