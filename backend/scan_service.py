"""Live scan metrics + compliance (rulebook §10)."""

from __future__ import annotations

import math
from typing import Any

from backend.data_manager import mgr


def _num(v: float | None, ready: bool) -> float | None:
    if not ready or v is None:
        return None
    if isinstance(v, float) and math.isnan(v):  # noqa: PLW0211
        return None
    return float(v)


def compute_scan_rows(symbol_signal: str | None) -> list[dict[str, Any]]:
    """`/api/scan` rows: SIGNAL first, then WATCH (score/rvol), then em-dash; stable sort."""

    ready = mgr.cache_ready_public()
    synd = sorted([x["symbol"] for x in mgr.active_stocks if x.get("active", True)])

    if not ready:
        return [
            {
                "symbol": sym,
                "rvol": None,
                "atr_pct": None,
                "gap_pct": None,
                "momentum_15m": None,
                "compliance_score": None,
                "result": "—",
            }
            for sym in synd
        ]

    atr_by_symbol: dict[str, float] = {}
    rvol_by_symbol: dict[str, float | None] = {}
    mom_by_symbol: dict[str, float | None] = {}

    for sym in synd:
        df = mgr.rolling_cache.get(sym)
        if df is None or df.empty:
            rvol_by_symbol[sym] = None
            mom_by_symbol[sym] = None
            continue

        try:
            hi = float(df["high"].max())
            lo = float(df["low"].min())
            cl = float(df["close"].iloc[-1])
            if cl > 0:
                atr_by_symbol[sym] = float((hi - lo) / cl * 100.0)

            vl = df["volume"]
            if len(df) >= 20:
                m = float(vl.iloc[-20:].mean())
                rvol_by_symbol[sym] = float(vl.iloc[-1]) / m if m else None

            else:
                rvol_by_symbol[sym] = None

            if len(df) >= 3:
                mom_by_symbol[sym] = float(
                    (float(df["close"].iloc[-1]) / float(df["close"].iloc[-3]) - 1.0) * 100.0
                )
            else:
                mom_by_symbol[sym] = None

        except Exception:  # noqa: BLE001
            rvol_by_symbol[sym] = None
            mom_by_symbol[sym] = None

    atr_sorted_symbols = sorted(atr_by_symbol, key=lambda s: atr_by_symbol[s], reverse=True)
    top10 = set(atr_sorted_symbols[:10])

    rows_out: list[dict[str, Any]] = []

    for sym in synd:
        atr = atr_by_symbol.get(sym)
        rvol = rvol_by_symbol.get(sym)
        mom = mom_by_symbol.get(sym)
        gp = float(mgr.gap_cache[sym]["gap_pct"]) if sym in mgr.gap_cache else None

        score = 0

        if rvol is not None and not (isinstance(rvol, float) and math.isnan(rvol)) and rvol > 2.0:
            score += 1
        if sym in top10:
            score += 1

        if mom is not None and not (isinstance(mom, float) and math.isnan(mom)) and mom < 0.3:
            score += 1

        if gp is not None and gp > 0:
            score += 1

        if symbol_signal == sym:
            result = "SIGNAL"
        elif score >= 2:
            result = "WATCH"
        else:
            result = "—"

        rows_out.append(
            {
                "symbol": sym,
                "rvol": _num(rvol, True),
                "atr_pct": _num(atr, True),
                "gap_pct": _num(gp, True),
                "momentum_15m": _num(mom, True),
                "compliance_score": score,
                "result": result,
            },
        )

    def _sort_rank(res: str) -> int:
        if res == "SIGNAL":
            return 0
        if res == "WATCH":
            return 1
        return 2

    def _rvol_tiebreak(rvol_val: float | None) -> float:
        if rvol_val is None:
            return float("inf")
        return -float(rvol_val)

    rows_out.sort(
        key=lambda r: (
            _sort_rank(r["result"]),
            -r["compliance_score"],
            _rvol_tiebreak(r["rvol"]),
        ),
    )

    return rows_out
