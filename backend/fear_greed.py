"""
Alternative.me Crypto Fear & Greed Index
Free, no key required. Commercial use allowed with attribution.
"""

import requests
from datetime import datetime
from typing import Optional

BASE_URL = "https://api.alternative.me/fng/"


def get_fear_greed_index(limit: int = 1) -> dict:
    """Get Fear & Greed Index.
    limit: Number of historical days (1-365)
    Returns: {value, classification, timestamp}
    """
    params = {"limit": min(limit, 365), "format": "json"}
    
    try:
        r = requests.get(BASE_URL, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "data" in data and data["data"]:
            results = []
            for item in data["data"]:
                results.append({
                    "value": int(item.get("value", 0)),
                    "classification": item.get("value_classification", ""),
                    "timestamp": datetime.fromtimestamp(
                        int(item.get("timestamp", 0))
                    ).strftime("%Y-%m-%d"),
                    "time_until_update": item.get("time_until_update"),
                })
            
            return {
                "current": results[0] if results else None,
                "history": results,
                "source": "alternative.me",
                "attribution": "Powered by Alternative.me",
            }
    except Exception as e:
        print(f"[FearGreed] index error: {e}")
    
    return None


def get_current() -> dict:
    """Get current Fear & Greed value only."""
    data = get_fear_greed_index(1)
    if data and data.get("current"):
        return data["current"]
    return None


def get_sentiment_signal() -> dict:
    """Convert Fear & Greed to trading signal.
    - Extreme Fear (<25): Contrarian BUY signal
    - Fear (25-45): Cautious BUY
    - Neutral (45-55): HOLD
    - Greed (55-75): Cautious SELL / reduce
    - Extreme Greed (>75): Contrarian SELL signal
    """
    data = get_current()
    if not data:
        return None
    
    value = data["value"]
    
    if value < 25:
        signal = "strong_buy"
        reason = "Extreme Fear — contrarian buy opportunity"
    elif value < 45:
        signal = "buy"
        reason = "Fear — cautious accumulation"
    elif value < 55:
        signal = "neutral"
        reason = "Neutral market sentiment"
    elif value < 75:
        signal = "sell"
        reason = "Greed — consider taking profits"
    else:
        signal = "strong_sell"
        reason = "Extreme Greed — contrarian sell signal"
    
    return {
        "value": value,
        "classification": data["classification"],
        "signal": signal,
        "reason": reason,
        "timestamp": data["timestamp"],
    }


def get_historical_trend(days: int = 7) -> dict:
    """Get sentiment trend over past N days."""
    data = get_fear_greed_index(days)
    if not data or not data.get("history"):
        return None
    
    history = data["history"]
    values = [h["value"] for h in history]
    
    if len(values) < 2:
        return None
    
    current = values[0]
    avg = sum(values) / len(values)
    change = current - values[-1]  # vs oldest in range
    
    if change > 10:
        trend = "improving"
    elif change < -10:
        trend = "deteriorating"
    else:
        trend = "stable"
    
    return {
        "current": current,
        "average": round(avg, 1),
        "change": change,
        "trend": trend,
        "days": days,
        "history": history,
    }


if __name__ == "__main__":
    print("=== Crypto Fear & Greed Index Test ===")
    
    print("\nCurrent Index:")
    current = get_current()
    if current:
        print(f"  Value: {current['value']}")
        print(f"  Classification: {current['classification']}")
        print(f"  Date: {current['timestamp']}")
    
    print("\nSentiment Signal:")
    signal = get_sentiment_signal()
    if signal:
        print(f"  Signal: {signal['signal']}")
        print(f"  Reason: {signal['reason']}")
    
    print("\n7-Day Trend:")
    trend = get_historical_trend(7)
    if trend:
        print(f"  Current: {trend['current']}")
        print(f"  Average: {trend['average']}")
        print(f"  Change: {trend['change']:+d}")
        print(f"  Trend: {trend['trend']}")
