import type { MarketData, SignalMarker } from '../utils/types.js';

export interface IndicatorValues {
  rsi: number[];
  macd: { macd: number[]; signal: number[]; histogram: number[] };
  ema: { fast: number[]; slow: number[] };
  bollinger: { upper: number[]; middle: number[]; lower: number[] };
  volume: { sma: number[]; ratio: number[] };
}

export function calculateRSI(data: MarketData[], period: number = 14): number[] {
  if (data.length < period + 1) return [];

  const gains: number[] = [];
  const losses: number[] = [];

  for (let i = 1; i < data.length; i++) {
    const change = data[i].close - data[i - 1].close;
    gains.push(change > 0 ? change : 0);
    losses.push(change < 0 ? -change : 0);
  }

  const rsi: number[] = [];
  let avgGain = gains.slice(0, period).reduce((a, b) => a + b, 0) / period;
  let avgLoss = losses.slice(0, period).reduce((a, b) => a + b, 0) / period;

  for (let i = period; i < gains.length; i++) {
    avgGain = (avgGain * (period - 1) + gains[i]) / period;
    avgLoss = (avgLoss * (period - 1) + losses[i]) / period;

    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    rsi.push(100 - 100 / (1 + rs));
  }

  // Pad beginning with nulls
  return new Array(period).fill(NaN).concat(rsi);
}

export function calculateEMA(data: MarketData[], period: number): number[] {
  if (data.length === 0) return [];

  const k = 2 / (period + 1);
  const ema: number[] = [];
  let prevEMA = data[0].close;

  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      ema.push(data[0].close);
    } else {
      prevEMA = data[i].close * k + prevEMA * (1 - k);
      ema.push(prevEMA);
    }
  }

  return ema;
}

export function calculateMACD(
  data: MarketData[],
  fastPeriod: number = 12,
  slowPeriod: number = 26,
  signalPeriod: number = 9
): { macd: number[]; signal: number[]; histogram: number[] } {
  const fastEMA = calculateEMA(data, fastPeriod);
  const slowEMA = calculateEMA(data, slowPeriod);

  const macdLine: number[] = [];
  for (let i = 0; i < data.length; i++) {
    macdLine.push(fastEMA[i] - slowEMA[i]);
  }

  const signalLine = calculateEMA(
    data.map((d, i) => ({ ...d, close: macdLine[i] })),
    signalPeriod
  );

  const histogram: number[] = [];
  for (let i = 0; i < data.length; i++) {
    histogram.push(macdLine[i] - signalLine[i]);
  }

  return { macd: macdLine, signal: signalLine, histogram };
}

export function calculateBollingerBands(
  data: MarketData[],
  period: number = 20,
  stdDev: number = 2
): { upper: number[]; middle: number[]; lower: number[] } {
  const upper: number[] = [];
  const middle: number[] = [];
  const lower: number[] = [];

  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      upper.push(NaN);
      middle.push(NaN);
      lower.push(NaN);
      continue;
    }

    const slice = data.slice(i - period + 1, i + 1);
    const avg = slice.reduce((sum, d) => sum + d.close, 0) / period;
    const variance = slice.reduce((sum, d) => sum + Math.pow(d.close - avg, 2), 0) / period;
    const sd = Math.sqrt(variance);

    middle.push(avg);
    upper.push(avg + stdDev * sd);
    lower.push(avg - stdDev * sd);
  }

  return { upper, middle, lower };
}

export function calculateVolumeSMA(data: MarketData[], period: number = 20): { sma: number[]; ratio: number[] } {
  const sma: number[] = [];
  const ratio: number[] = [];

  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      sma.push(NaN);
      ratio.push(NaN);
      continue;
    }

    const slice = data.slice(i - period + 1, i + 1);
    const avgVol = slice.reduce((sum, d) => sum + d.volume, 0) / period;
    sma.push(avgVol);
    ratio.push(data[i].volume / avgVol);
  }

  return { sma, ratio };
}

export function calculateAllIndicators(data: MarketData[]): IndicatorValues {
  return {
    rsi: calculateRSI(data, 14),
    macd: calculateMACD(data, 12, 26, 9),
    ema: {
      fast: calculateEMA(data, 9),
      slow: calculateEMA(data, 21),
    },
    bollinger: calculateBollingerBands(data, 20, 2),
    volume: calculateVolumeSMA(data, 20),
  };
}

export interface SignalCondition {
  name: string;
  check: (indicators: IndicatorValues, data: MarketData[], index: number) => boolean;
  weight: number;
}

export const LONG_CONDITIONS: SignalCondition[] = [
  {
    name: 'RSI Oversold',
    weight: 2,
    check: (ind, data, i) => ind.rsi[i] < 35 && ind.rsi[i] > 0,
  },
  {
    name: 'MACD Bullish Cross',
    weight: 3,
    check: (ind, data, i) => i > 0 && ind.macd.histogram[i] > 0 && ind.macd.histogram[i - 1] <= 0,
  },
  {
    name: 'EMA Bullish Cross',
    weight: 2,
    check: (ind, data, i) => i > 0 && ind.ema.fast[i] > ind.ema.slow[i] && ind.ema.fast[i - 1] <= ind.ema.slow[i - 1],
  },
  {
    name: 'Price Above EMA Fast',
    weight: 1,
    check: (ind, data, i) => data[i].close > ind.ema.fast[i],
  },
  {
    name: 'Bollinger Bounce Low',
    weight: 2,
    check: (ind, data, i) => data[i].low <= ind.bollinger.lower[i] * 1.005 && data[i].close > ind.bollinger.lower[i],
  },
  {
    name: 'Volume Spike',
    weight: 1.5,
    check: (ind, data, i) => ind.volume.ratio[i] > 1.5,
  },
  {
    name: 'Heikin Ashi Green',
    weight: 1,
    check: (ind, data, i) => data[i].close > data[i].open,
  },
];

export const SHORT_CONDITIONS: SignalCondition[] = [
  {
    name: 'RSI Overbought',
    weight: 2,
    check: (ind, data, i) => ind.rsi[i] > 65 && ind.rsi[i] < 100,
  },
  {
    name: 'MACD Bearish Cross',
    weight: 3,
    check: (ind, data, i) => i > 0 && ind.macd.histogram[i] < 0 && ind.macd.histogram[i - 1] >= 0,
  },
  {
    name: 'EMA Bearish Cross',
    weight: 2,
    check: (ind, data, i) => i > 0 && ind.ema.fast[i] < ind.ema.slow[i] && ind.ema.fast[i - 1] >= ind.ema.slow[i - 1],
  },
  {
    name: 'Price Below EMA Fast',
    weight: 1,
    check: (ind, data, i) => data[i].close < ind.ema.fast[i],
  },
  {
    name: 'Bollinger Bounce High',
    weight: 2,
    check: (ind, data, i) => data[i].high >= ind.bollinger.upper[i] * 0.995 && data[i].close < ind.bollinger.upper[i],
  },
  {
    name: 'Volume Spike',
    weight: 1.5,
    check: (ind, data, i) => ind.volume.ratio[i] > 1.5,
  },
  {
    name: 'Heikin Ashi Red',
    weight: 1,
    check: (ind, data, i) => data[i].close < data[i].open,
  },
];

export function generateSignals(data: MarketData[]): SignalMarker[] {
  if (data.length < 50) return [];

  const indicators = calculateAllIndicators(data);
  const signals: SignalMarker[] = [];
  const minBarsBetweenSignals = 10;
  let lastSignalIndex = -minBarsBetweenSignals;

  for (let i = 30; i < data.length - 1; i++) {
    if (i - lastSignalIndex < minBarsBetweenSignals) continue;

    // Check long conditions
    let longScore = 0;
    let longReasons: string[] = [];
    for (const cond of LONG_CONDITIONS) {
      try {
        if (cond.check(indicators, data, i)) {
          longScore += cond.weight;
          longReasons.push(cond.name);
        }
      } catch {}
    }

    // Check short conditions
    let shortScore = 0;
    let shortReasons: string[] = [];
    for (const cond of SHORT_CONDITIONS) {
      try {
        if (cond.check(indicators, data, i)) {
          shortScore += cond.weight;
          shortReasons.push(cond.name);
        }
      } catch {}
    }

    const maxPossibleScore = Math.max(
      LONG_CONDITIONS.reduce((s, c) => s + c.weight, 0),
      SHORT_CONDITIONS.reduce((s, c) => s + c.weight, 0)
    );

    const longConfidence = longScore / maxPossibleScore;
    const shortConfidence = shortScore / maxPossibleScore;

    // Require minimum score and confidence
    if (longScore >= 6 && longConfidence > shortConfidence && longConfidence > 0.35) {
      const entry = data[i].close;
      const atr = calculateATR(data, i, 14);
      const tp = entry + atr * 2;
      const sl = entry - atr * 1.5;

      signals.push({
        id: `signal-${data[i].timestamp}`,
        timestamp: data[i].timestamp,
        price: entry,
        type: 'buy',
        reason: longReasons.join(' + '),
        confidence: Math.min(longConfidence, 0.95),
      });
      lastSignalIndex = i;
    } else if (shortScore >= 6 && shortConfidence > longConfidence && shortConfidence > 0.35) {
      const entry = data[i].close;
      const atr = calculateATR(data, i, 14);
      const tp = entry - atr * 2;
      const sl = entry + atr * 1.5;

      signals.push({
        id: `signal-${data[i].timestamp}`,
        timestamp: data[i].timestamp,
        price: entry,
        type: 'sell',
        reason: shortReasons.join(' + '),
        confidence: Math.min(shortConfidence, 0.95),
      });
      lastSignalIndex = i;
    }
  }

  return signals;
}

function calculateATR(data: MarketData[], index: number, period: number): number {
  if (index < period) return data[index].close * 0.01;

  let sum = 0;
  for (let i = index - period + 1; i <= index; i++) {
    const high = data[i].high;
    const low = data[i].low;
    const prevClose = i > 0 ? data[i - 1].close : data[i].close;
    const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
    sum += tr;
  }
  return sum / period;
}

export function getLatestSignal(data: MarketData[]): SignalMarker | null {
  const signals = generateSignals(data);
  return signals.length > 0 ? signals[signals.length - 1] : null;
}