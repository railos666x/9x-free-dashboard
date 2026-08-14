import type { MarketData, HeikinAshiData } from './types.js';

export function calculateHeikinAshi(data: MarketData[]): HeikinAshiData[] {
  if (data.length === 0) return [];

  const result: HeikinAshiData[] = [];
  let prevHAOpen = (data[0].open + data[0].close) / 2;
  let prevHAClose = (data[0].open + data[0].high + data[0].low + data[0].close) / 4;

  for (let i = 0; i < data.length; i++) {
    const candle = data[i];

    const haClose = (candle.open + candle.high + candle.low + candle.close) / 4;
    const haOpen = i === 0 ? prevHAOpen : (prevHAOpen + prevHAClose) / 2;
    const haHigh = Math.max(candle.high, haOpen, haClose);
    const haLow = Math.min(candle.low, haOpen, haClose);

    result.push({
      timestamp: candle.timestamp,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      haOpen,
      haClose,
      haHigh,
      haLow,
    });

    prevHAOpen = haOpen;
    prevHAClose = haClose;
  }

  return result;
}

export function detectTrend(haData: HeikinAshiData[], lookback: number = 10): 'bullish' | 'bearish' | 'neutral' {
  if (haData.length < lookback) return 'neutral';

  const recent = haData.slice(-lookback);
  let bullishCount = 0;
  let bearishCount = 0;

  for (const candle of recent) {
    if (candle.haClose > candle.haOpen) bullishCount++;
    else if (candle.haClose < candle.haOpen) bearishCount++;
  }

  if (bullishCount > bearishCount * 1.5) return 'bullish';
  if (bearishCount > bullishCount * 1.5) return 'bearish';
  return 'neutral';
}