"""
Fed Treasury data — fiscaldata.treasury.gov
No API key required. Provides Treasury yields, debt, and fiscal data.
"""

import requests
from datetime import datetime, timedelta
from typing import Optional

BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/data"

# Common series
SERIES = {
    "YIELD_CURVE": "daily_treasury_yield_curve",
    "RATES": "daily_treasury_rates",
    "DEBT": "debt_to_penny",
}


def get_yield_curve(record_date: str = None) -> dict:
    """Get Treasury yield curve for a specific date or latest.
    Returns dict with 1mo, 2mo, 3mo, 4mo, 6mo, 1yr, 2yr, 3yr, 5yr, 7yr, 10yr, 20yr, 30yr.
    """
    if record_date is None:
        record_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    url = f"{BASE_URL}/{SERIES['YIELD_CURVE']}"
    params = {
        "filter": f"record_date:equals:{record_date}",
        "format": "json"
    }
    
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if not data.get("data"):
            # Try previous day
            prev = (datetime.strptime(record_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            params["filter"] = f"record_date:equals:{prev}"
            r = requests.get(url, params=params, timeout=15)
            data = r.json()
        
        if data.get("data"):
            row = data["data"][0]
            return {
                "date": row.get("record_date"),
                "1mo": float(row.get("bc_1month", 0) or 0),
                "2mo": float(row.get("bc_2month", 0) or 0),
                "3mo": float(row.get("bc_3month", 0) or 0),
                "4mo": float(row.get("bc_4month", 0) or 0),
                "6mo": float(row.get("bc_6month", 0) or 0),
                "1yr": float(row.get("bc_1year", 0) or 0),
                "2yr": float(row.get("bc_2year", 0) or 0),
                "3yr": float(row.get("bc_3year", 0) or 0),
                "5yr": float(row.get("bc_5year", 0) or 0),
                "7yr": float(row.get("bc_7year", 0) or 0),
                "10yr": float(row.get("bc_10year", 0) or 0),
                "20yr": float(row.get("bc_20year", 0) or 0),
                "30yr": float(row.get("bc_30year", 0) or 0),
            }
    except Exception as e:
        print(f"[FedTreasury] yield_curve error: {e}")
    
    return None


def get_latest_rates() -> dict:
    """Get latest Treasury rates (daily)."""
    url = f"{BASE_URL}/{SERIES['RATES']}"
    params = {"format": "json", "page[size]": 1, "sort": "-record_date"}
    
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if data.get("data"):
            row = data["data"][0]
            return {
                "date": row.get("record_date"),
                "fed_funds": float(row.get("rrp4", 0) or 0),  # Fed Funds Rate
                "treasury_3mo": float(row.get("tcm3", 0) or 0),
                "treasury_1yr": float(row.get("tcm1", 0) or 0),
                "treasury_5yr": float(row.get("tcm5", 0) or 0),
                "treasury_10yr": float(row.get("tcm10", 0) or 0),
                "treasury_30yr": float(row.get("tcm30", 0) or 0),
            }
    except Exception as e:
        print(f"[FedTreasury] rates error: {e}")
    
    return None


def get_debt() -> dict:
    """Get latest US debt figures."""
    url = f"{BASE_URL}/{SERIES['DEBT']}"
    params = {"format": "json", "page[size]": 1, "sort": "-record_date"}
    
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if data.get("data"):
            row = data["data"][0]
            return {
                "date": row.get("record_date"),
                "debt_held_public": float(row.get("debt_held_public_amt", 0) or 0),
                "intragovt_holdings": float(row.get("intragovt_holdings_amt", 0) or 0),
                "total_debt": float(row.get("tot_debt_out_amt", 0) or 0),
            }
    except Exception as e:
        print(f"[FedTreasury] debt error: {e}")
    
    return None


def get_macro_snapshot() -> dict:
    """Combined macro snapshot from Treasury data."""
    yields = get_yield_curve()
    rates = get_latest_rates()
    
    return {
        "source": "fiscaldata.treasury.gov",
        "yields": yields,
        "rates": rates,
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    print("=== Fed Treasury Data Test ===")
    print("\nYield Curve:")
    yc = get_yield_curve()
    if yc:
        for k, v in yc.items():
            print(f"  {k}: {v}%")
    
    print("\nLatest Rates:")
    rates = get_latest_rates()
    if rates:
        for k, v in rates.items():
            print(f"  {k}: {v}%")
    
    print("\nDebt:")
    debt = get_debt()
    if debt:
        for k, v in debt.items():
            print(f"  {k}: {v}")
