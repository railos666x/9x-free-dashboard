"""
FRED (Federal Reserve Economic Data) API integration.

Provides access to 816K+ US economic time series:
- GDP, CPI, unemployment, interest rates
- Treasury yields, inflation expectations
- Housing starts, retail sales
- And much more

Free, no API key required (120 req/min).
Optional key for unlimited requests.
"""
import requests
import time
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import config


# Rate limiting
_last_call = 0.0
MIN_INTERVAL = 0.5  # 120 req/min = 0.5s between calls


def _rate_limit():
    """Ensure we don't exceed rate limits."""
    global _last_call
    now = time.time()
    elapsed = now - _last_call
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_call = time.time()


# Common FRED series IDs
SERIES = {
    # Interest rates
    "FED_FUNDS": "FEDFUNDS",      # Federal funds effective rate
    "TREASURY_10Y": "DGS10",      # 10-year Treasury constant maturity
    "TREASURY_2Y": "DGS2",        # 2-year Treasury
    "TREASURY_3M": "DGS3MO",      # 3-month Treasury
    
    # Inflation
    "CPI_ALL": "CPIAUCSL",        # Consumer Price Index (all items)
    "CPI_CORE": "CPILFESL",       # Core CPI (less food and energy)
    "PCE_ALL": "PCEPI",           # Personal consumption expenditures
    "PCE_CORE": "PCEPILFE",       # Core PCE (Fed's preferred measure)
    "INFLATION_EXPECT": "T5YIE",  # 5-year breakeven inflation rate
    
    # Employment
    "UNEMPLOYMENT": "UNRATE",     # Unemployment rate
    "NONFARM_PAYROLLS": "PAYEMS", # Total nonfarm payroll employment
    "INITIAL_CLAIMS": "ICSA",     # Initial jobless claims
    "CONTINUED_CLAIMS": "CCSA",   # Continued claims
    
    # GDP & Growth
    "GDP_NOMINAL": "GDP",         # Nominal GDP
    "GDP_REAL": "GDPC1",          # Real GDP
    "GDP_GROWTH": "A191RL1Q225SBEA",  # Real GDP growth rate
    
    # Housing
    "HOUSING_STARTS": "HOUST",    # Housing starts
    "BUILDING_PERMITS": "PERMIT", # Building permits
    "NEW_HOME_SALES": "CSUSHPISA", # S&P/Case-Shiller Home Price Index
    "MORTGAGE_30Y": "MORTGAGE30US", # 30-year fixed mortgage rate
    
    # Consumer
    "RETAIL_SALES": "RSAFS",      # Retail sales
    "CONSUMER_CONF": "UMCSENT",   # University of Michigan Consumer Sentiment
    "PERSONAL_INCOME": "PI",      # Personal income
    "PERSONAL_SPENDING": "PCE",   # Personal consumption expenditures
    
    # Manufacturing
    "ISM_MANUFACTURING": "MANEMP", # ISM Manufacturing Employment
    "INDUSTRIAL_PROD": "INDPRO",  # Industrial production index
    "DURABLE_GOODS": "DGORDER",   # Durable goods orders
    
    # Trade
    "TRADE_BALANCE": "BOPGSTB",   # Trade balance
    "EXPORTS": "BOPGSEX",         # Exports of goods and services
    "IMPORTS": "BOPGSIM",         # Imports of goods and services
    
    # Markets
    "SP500": "SP500",             # S&P 500
    "DXY": "DTWEXBGS",            # Trade-weighted dollar index (broad)
    "VIX": "VIXCLS",              # VIX close
    
    # Credit
    "CORPORATE_SPREAD": "BAA10Y", # BAA corporate bond spread
    "MORTGAGE_DELINQ": "MDRSFRACBS", # Mortgage delinquency rate
}


def _get_series(series_id: str, start_date: str = None, end_date: str = None) -> Optional[List[Dict]]:
    """Fetch a FRED series.
    
    Args:
        series_id: FRED series ID (e.g., 'GDP', 'UNRATE')
        start_date: Start date (YYYY-MM-DD), default 1 year ago
        end_date: End date (YYYY-MM-DD), default today
    
    Returns:
        List of {date, value} dicts or None
    
    Note:
        FRED requires a free API key. Get one at:
        https://fred.stlouisfed.org/docs/api/api_key.html
    """
    if not config.FRED_ENABLED or not config.FRED_API_KEY:
        return None
    
    _rate_limit()
    
    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    
    params = {
        "series_id": series_id,
        "api_key": config.FRED_API_KEY,
        "file_type": "json",
        "observation_start": start_date,
        "observation_end": end_date,
    }
    
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations", 
                        params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        observations = data.get("observations", [])
        result = []
        for obs in observations:
            val = obs.get("value", ".")
            if val != "." and val:  # "." means missing
                try:
                    result.append({
                        "date": obs.get("date"),
                        "value": float(val),
                    })
                except (ValueError, TypeError):
                    pass
        return result
    except Exception as e:
        print(f"[FRED] Series {series_id} failed: {e}")
        return None


def get_latest_value(series_name: str) -> Optional[float]:
    """Get the latest value for a named series.
    
    Args:
        series_name: Key from SERIES dict (e.g., 'UNEMPLOYMENT', 'CPI_ALL')
    
    Returns:
        Latest value or None
    """
    series_id = SERIES.get(series_name.upper())
    if not series_id:
        print(f"[FRED] Unknown series: {series_name}")
        return None
    
    data = _get_series(series_id)
    if data:
        return data[-1]["value"]
    return None


def get_macro_snapshot() -> Dict[str, Optional[float]]:
    """Get a snapshot of key macro indicators.
    
    Returns:
        Dict with latest values for key economic indicators
    """
    snapshot = {}
    
    # Key indicators to fetch
    key_indicators = [
        "UNEMPLOYMENT",
        "CPI_CORE",
        "FED_FUNDS",
        "TREASURY_10Y",
        "GDP_GROWTH",
        "RETAIL_SALES",
        "CONSUMER_CONF",
    ]
    
    for name in key_indicators:
        snapshot[name.lower()] = get_latest_value(name)
    
    return snapshot


def get_yield_curve() -> Dict[str, Optional[float]]:
    """Get yield curve data (Treasury yields at different maturities).
    
    Returns:
        {3m, 2y, 10y, spread_10y_2y}
    """
    data_3m = _get_series(SERIES["TREASURY_3M"])
    data_2y = _get_series(SERIES["TREASURY_2Y"])
    data_10y = _get_series(SERIES["TREASURY_10Y"])
    
    result = {
        "3m": data_3m[-1]["value"] if data_3m else None,
        "2y": data_2y[-1]["value"] if data_2y else None,
        "10y": data_10y[-1]["value"] if data_10y else None,
    }
    
    if result["10y"] is not None and result["2y"] is not None:
        result["spread_10y_2y"] = result["10y"] - result["2y"]
    
    return result


def get_inflation_data() -> Dict[str, Optional[float]]:
    """Get inflation-related indicators.
    
    Returns:
        {cpi, core_cpi, pce, core_pce, inflation_expectations}
    """
    return {
        "cpi": get_latest_value("CPI_ALL"),
        "core_cpi": get_latest_value("CPI_CORE"),
        "pce": get_latest_value("PCE_ALL"),
        "core_pce": get_latest_value("PCE_CORE"),
        "inflation_expectations": get_latest_value("INFLATION_EXPECT"),
    }


def get_employment_data() -> Dict[str, Optional[float]]:
    """Get employment-related indicators.
    
    Returns:
        {unemployment_rate, nonfarm_payrolls, initial_claims}
    """
    return {
        "unemployment_rate": get_latest_value("UNEMPLOYMENT"),
        "nonfarm_payrolls": get_latest_value("NONFARM_PAYROLLS"),
        "initial_claims": get_latest_value("INITIAL_CLAIMS"),
    }


if __name__ == "__main__":
    # Quick test
    print("=== FRED API Test ===")
    
    # Test macro snapshot
    print("\nMacro Snapshot:")
    snapshot = get_macro_snapshot()
    for k, v in snapshot.items():
        if v is not None:
            print(f"  {k}: {v}")
    
    # Test yield curve
    print("\nYield Curve:")
    yc = get_yield_curve()
    for k, v in yc.items():
        if v is not None:
            print(f"  {k}: {v:.3f}%")
    
    # Test inflation
    print("\nInflation:")
    inf = get_inflation_data()
    for k, v in inf.items():
        if v is not None:
            print(f"  {k}: {v}")
