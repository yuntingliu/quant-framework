"""Dated human-readable identities, distinct from the vendor's current master name."""

ETF_LABELS = {
    "510050.XSHG": ("上证50ETF", "SSE 50 ETF"),
    "510300.XSHG": ("沪深300ETF·沪市", "CSI 300 ETF · Shanghai"),
    "159919.XSHE": ("沪深300ETF·深市", "CSI 300 ETF · Shenzhen"),
    "510500.XSHG": ("中证500ETF·沪市", "CSI 500 ETF · Shanghai"),
    "159922.XSHE": ("中证500ETF·深市", "CSI 500 ETF · Shenzhen"),
    "159915.XSHE": ("创业板ETF", "ChiNext ETF"),
    "159901.XSHE": ("深证100ETF", "SZSE 100 ETF"),
    "588000.XSHG": ("科创50ETF·华夏", "STAR 50 ETF · ChinaAMC"),
    "588080.XSHG": ("科创50ETF·易方达", "STAR 50 ETF · E Fund"),
}


def contract_names(underlying, expiry, option_type, strike, multiplier):
    """Use dated strike/multiplier so a later adjustment cannot rename history."""
    zh, en = ETF_LABELS.get(underlying, (underlying, underlying))
    kind_zh, kind_en = ("认购", "Call") if option_type == "C" else ("认沽", "Put")
    adjusted = float(multiplier) != 10000
    return {
        "contract_name_zh": f"{zh} {expiry} {kind_zh} {strike:.4f}"
        + (f"（调整合约 ×{multiplier:g}）" if adjusted else ""),
        "contract_name_en": f"{en} {expiry} {kind_en} {strike:.4f}"
        + (f" (adjusted ×{multiplier:g})" if adjusted else ""),
        "name_source": "dated_contract_terms",
    }
