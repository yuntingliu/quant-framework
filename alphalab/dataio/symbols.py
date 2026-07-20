"""Symbol normalization helpers for RQ and framework boundaries."""
from __future__ import annotations

from collections.abc import Iterable

_EXCHANGE_SUFFIXES = (".XSHG", ".XSHE", ".XBEI", ".SH", ".SZ", ".BJ")


def normalize_symbol(symbol: object, *, assume_a_share: bool = False) -> str:
    value = str(symbol).strip().upper()
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]
    for suffix in _EXCHANGE_SUFFIXES:
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            assume_a_share = True
            break
    if assume_a_share and value.isdigit() and len(value) <= 6:
        return value.zfill(6)
    return value


def normalize_symbols(symbols: Iterable[object], *, assume_a_share: bool = False) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        value = normalize_symbol(symbol, assume_a_share=assume_a_share)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def to_framework_symbol(symbol: object) -> str:
    value = str(symbol).strip().upper()
    return (
        value.replace(".XSHG", ".SH")
        .replace(".XSHE", ".SZ")
        .replace(".XBEI", ".BJ")
    )


def to_rq_symbol(symbol: object) -> str:
    value = str(symbol).strip().upper()
    if value.endswith((".XSHG", ".XSHE", ".XBEI")):
        return value
    if value.endswith(".SH"):
        return value[:-3] + ".XSHG"
    if value.endswith(".SZ"):
        return value[:-3] + ".XSHE"
    if value.endswith(".BJ"):
        return value[:-3] + ".XBEI"
    bare = normalize_symbol(value, assume_a_share=True)
    if bare.isdigit() and len(bare) == 6:
        if bare.startswith(("4", "8", "92")):
            return bare + ".XBEI"
        if bare.startswith(("5", "6", "9")):
            return bare + ".XSHG"
        return bare + ".XSHE"
    return value


def canonical_a_share_symbol(symbol: object) -> str:
    return to_framework_symbol(to_rq_symbol(symbol))


__all__ = [
    "normalize_symbol",
    "normalize_symbols",
    "canonical_a_share_symbol",
    "to_framework_symbol",
    "to_rq_symbol",
]
