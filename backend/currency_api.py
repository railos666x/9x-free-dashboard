"""
Currency API — fawazahmed0/currency-api (GitHub-hosted)
150+ currencies, no key, no documented rate limit.
"""

import requests
from datetime import datetime, timedelta
from typing import Optional

BASE_URL = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1"


def get_latest_rates(base: str = "usd", currencies: list = None) -> dict:
    """Get latest exchange rates.
    base: Base currency code (lowercase, e.g., 'usd', 'eur')
    currencies: List of target currencies (lowercase)
    """
    if currencies is None:
        currencies = ["eur", "gbp", "jpy", "chf", "aud", "cad", "cny", "inr", "brl", "mxn"]
    
    try:
        r = requests.get(f"{BASE_URL}/currencies/{base}.json", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "date" in data and base in data:
            rates = data[base]
            filtered = {k: v for k, v in rates.items() if k in currencies or not currencies}
            return {
                "date": data["date"],
                "base": base.upper(),
                "rates": filtered,
            }
    except Exception as e:
        print(f"[CurrencyAPI] latest_rates error: {e}")
    
    return None


def get_historical_rates(date: str, base: str = "usd") -> dict:
    """Get historical rates for a specific date.
    date: YYYY-MM-DD format
    """
    # Convert to API format (YYYY-M-D without leading zeros)
    date_parts = date.split("-")
    api_date = f"{date_parts[0]}-{int(date_parts[1])}-{int(date_parts[2])}"
    
    try:
        r = requests.get(f"{BASE_URL}/currencies/{base}/{api_date}.json", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "date" in data and base in data:
            return {
                "date": data["date"],
                "base": base.upper(),
                "rates": data[base],
            }
    except Exception as e:
        print(f"[CurrencyAPI] historical_rates error: {e}")
    
    return None


def convert(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert amount between currencies."""
    from_c = from_currency.lower()
    to_c = to_currency.lower()
    
    try:
        r = requests.get(f"{BASE_URL}/currencies/{from_c}.json", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if from_c in data:
            rate = data[from_c].get(to_c, 0)
            result = amount * rate
            return {
                "date": data.get("date"),
                "amount": amount,
                "from": from_currency.upper(),
                "to": to_currency.upper(),
                "result": round(result, 4),
                "rate": rate,
            }
    except Exception as e:
        print(f"[CurrencyAPI] convert error: {e}")
    
    return None


def get_all_currencies() -> list:
    """Get list of all available currencies."""
    try:
        r = requests.get(f"{BASE_URL}/currencies.json", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "currencies" in data:
            return data["currencies"]
    except Exception as e:
        print(f"[CurrencyAPI] all_currencies error: {e}")
    
    return []


if __name__ == "__main__":
    print("=== Currency API Test ===")
    
    print("\nLatest USD rates:")
    latest = get_latest_rates("usd")
    if latest:
        print(f"  Date: {latest['date']}")
        for cur, rate in list(latest["rates"].items())[:10]:
            print(f"  USD/{cur.upper()}: {rate}")
    
    print("\nConvert 100 EUR to USD:")
    conv = convert(100, "eur", "usd")
    if conv:
        print(f"  {conv['amount']} {conv['from']} = {conv['result']} {conv['to']}")
        print(f"  Rate: {conv['rate']}")
    
    print("\nAvailable currencies (first 20):")
    currencies = get_all_currencies()
    print(f"  Total: {len(currencies)}")
    print(f"  {currencies[:20]}")
