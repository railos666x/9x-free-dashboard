"""
Frankfurter — ECB forex rates + time series
No API key required. European Central Bank reference rates.
"""

import requests
from datetime import datetime, timedelta
from typing import Optional

BASE_URL = "https://api.frankfurter.app"


def get_latest_rates(base: str = "EUR", symbols: list = None) -> dict:
    """Get latest ECB reference rates.
    base: Base currency (default EUR)
    symbols: List of target currencies (default: major currencies)
    """
    if symbols is None:
        symbols = ["USD", "GBP", "JPY", "CHF", "AUD", "CAD", "CNY", "SEK", "NOK", "PLN"]
    
    params = {"from": base, "to": ",".join(symbols)}
    
    try:
        r = requests.get(f"{BASE_URL}/latest", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "date" in data and "rates" in data:
            return {
                "date": data["date"],
                "base": base,
                "rates": data["rates"],
            }
    except Exception as e:
        print(f"[Frankfurter] latest_rates error: {e}")
    
    return None


def get_time_series(start_date: str, end_date: str = None, 
                    base: str = "EUR", symbols: list = None) -> dict:
    """Get historical exchange rates.
    start_date: YYYY-MM-DD
    end_date: YYYY-MM-DD (default: latest)
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if symbols is None:
        symbols = ["USD", "GBP", "JPY", "CHF"]
    
    params = {
        "from": start_date,
        "to": end_date,
        "base": base,
        "symbols": ",".join(symbols)
    }
    
    try:
        r = requests.get(f"{BASE_URL}/{start_date}..{end_date}", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "rates" in data:
            return {
                "base": base,
                "start_date": start_date,
                "end_date": end_date,
                "rates": data["rates"],
            }
    except Exception as e:
        print(f"[Frankfurter] time_series error: {e}")
    
    return None


def get_eur_usd() -> float:
    """Quick EUR/USD rate."""
    data = get_latest_rates("EUR", ["USD"])
    if data and "rates" in data:
        return data["rates"].get("USD", 0)
    return None


def convert(amount: float, from_currency: str, to_currency: str, date: str = None) -> dict:
    """Convert amount between currencies."""
    if date is None:
        date = "latest"
    
    params = {"amount": amount, "from": from_currency, "to": to_currency}
    
    try:
        r = requests.get(f"{BASE_URL}/{date}", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "date" in data:
            return {
                "date": data["date"],
                "amount": amount,
                "from": from_currency,
                "to": to_currency,
                "result": data.get("result"),
                "rate": data.get("rates", {}).get(to_currency),
            }
    except Exception as e:
        print(f"[Frankfurter] convert error: {e}")
    
    return None


if __name__ == "__main__":
    print("=== Frankfurter ECB Rates Test ===")
    
    print("\nLatest EUR rates:")
    latest = get_latest_rates("EUR")
    if latest:
        print(f"  Date: {latest['date']}")
        for cur, rate in latest["rates"].items():
            print(f"  EUR/{cur}: {rate}")
    
    print("\nEUR/USD quick rate:")
    eur_usd = get_eur_usd()
    if eur_usd:
        print(f"  {eur_usd}")
    
    print("\nConvert 100 EUR to USD:")
    conv = convert(100, "EUR", "USD")
    if conv:
        print(f"  {conv['amount']} {conv['from']} = {conv['result']} {conv['to']}")
        print(f"  Rate: {conv['rate']}")
