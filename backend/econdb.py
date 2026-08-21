"""
Econdb — global macro data
No key required for basic access. Covers multiple countries.
"""

import requests
from datetime import datetime
from typing import Optional

BASE_URL = "https://www.econdb.com/api"


def get_indicators(dataset: str = "main", country: str = "US") -> list:
    """Get economic indicators for a country.
    dataset: 'main', 'inflation', 'employment', 'gdp', etc.
    country: 2-letter country code (US, EA, GB, JP, etc.)
    """
    params = {
        "dataset": dataset,
        "country": country,
        "format": "json",
    }
    
    try:
        r = requests.get(f"{BASE_URL}/indicators/", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "results" in data:
            return [
                {
                    "ticker": item.get("ticker"),
                    "date": item.get("date"),
                    "value": item.get("value"),
                    "unit": item.get("unit"),
                    "country": item.get("country"),
                }
                for item in data["results"]
            ]
    except Exception as e:
        print(f"[Econdb] indicators error: {e}")
    return []


def get_us_gdp() -> dict:
    """Get US GDP data."""
    indicators = get_indicators("gdp", "US")
    for ind in indicators:
        if ind.get("ticker") == "GDP":
            return {
                "date": ind.get("date"),
                "value": ind.get("value"),
                "unit": ind.get("unit", "Billion USD"),
            }
    return None


def get_us_inflation() -> dict:
    """Get US CPI/inflation data."""
    indicators = get_indicators("inflation", "US")
    for ind in indicators:
        if ind.get("ticker") == "CPI":
            return {
                "date": ind.get("date"),
                "value": ind.get("value"),
                "unit": "Percent",
            }
    return None


def get_us_unemployment() -> dict:
    """Get US unemployment rate."""
    indicators = get_indicators("employment", "US")
    for ind in indicators:
        if ind.get("ticker") == "UNRATE":
            return {
                "date": ind.get("date"),
                "value": ind.get("value"),
                "unit": "Percent",
            }
    return None


def get_eurozone_gdp() -> dict:
    """Get Eurozone GDP data."""
    indicators = get_indicators("gdp", "EA")
    for ind in indicators:
        if ind.get("ticker") == "GDP":
            return {
                "date": ind.get("date"),
                "value": ind.get("value"),
                "unit": "Billion EUR",
            }
    return None


def get_global_macro_snapshot() -> dict:
    """Get snapshot of major economies."""
    return {
        "source": "econdb.com",
        "us_gdp": get_us_gdp(),
        "us_inflation": get_us_inflation(),
        "us_unemployment": get_us_unemployment(),
        "eurozone_gdp": get_eurozone_gdp(),
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    print("=== Econdb Test ===")
    
    print("\nGlobal Macro Snapshot:")
    snapshot = get_global_macro_snapshot()
    if snapshot:
        if snapshot.get("us_gdp"):
            gdp = snapshot["us_gdp"]
            print(f"  US GDP: {gdp['value']} {gdp['unit']} ({gdp['date']})")
        if snapshot.get("us_inflation"):
            cpi = snapshot["us_inflation"]
            print(f"  US Inflation: {cpi['value']}% ({cpi['date']})")
        if snapshot.get("us_unemployment"):
            unemp = snapshot["us_unemployment"]
            print(f"  US Unemployment: {unemp['value']}% ({unemp['date']})")
        if snapshot.get("eurozone_gdp"):
            eugdp = snapshot["eurozone_gdp"]
            print(f"  Eurozone GDP: {eugdp['value']} {eugdp['unit']} ({eugdp['date']})")
