"""Instrument key resolution from Upstox BOD JSON rows."""

from __future__ import annotations

from backend.constants import BOOTSTRAP_NIFTY50, TRADING_SYMBOL_ALIASES
from backend.upstox_client import build_nifty50_equity_key_map


def _row(trading_symbol: str, instrument_key: str, segment: str = "NSE_EQ", instrument_type: str = "EQ") -> dict:
    return {
        "segment": segment,
        "instrument_type": instrument_type,
        "trading_symbol": trading_symbol,
        "instrument_key": instrument_key,
    }


def test_maps_nse_eq_only() -> None:
    rows = [
        _row("RELIANCE", "NSE_EQ|INE002A01018"),
        _row("RELIANCE", "NSE_FO|OPT", segment="NSE_FO", instrument_type="OPTSTK"),
    ]
    m = build_nifty50_equity_key_map(rows, ["RELIANCE"])
    assert m["RELIANCE"] == "NSE_EQ|INE002A01018"


def test_trading_symbol_alias() -> None:
    rows = [_row("TMPV", "NSE_EQ|INE155A01022")]
    m = build_nifty50_equity_key_map(rows, ["TATAMOTORS"], TRADING_SYMBOL_ALIASES)
    assert m["TATAMOTORS"] == "NSE_EQ|INE155A01022"


def test_bootstrap_subset_count() -> None:
    rows = [_row(sym, f"NSE_EQ|INE{sym[:6]}") for sym in BOOTSTRAP_NIFTY50 if sym != "TATAMOTORS"]
    rows.append(_row("TMPV", "NSE_EQ|INE155A01022"))
    m = build_nifty50_equity_key_map(rows, BOOTSTRAP_NIFTY50, TRADING_SYMBOL_ALIASES)
    assert len(m) == len(BOOTSTRAP_NIFTY50)
