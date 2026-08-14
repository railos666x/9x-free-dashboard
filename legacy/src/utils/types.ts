export interface MarketData {
  symbol: string;
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface HeikinAshiData {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  haOpen: number;
  haClose: number;
  haHigh: number;
  haLow: number;
}

export interface SignalZone {
  id: string;
  type: 'long' | 'short';
  entryPrice: number;
  tpPrice: number;
  slPrice: number;
  startTime: number;
  endTime?: number;
  color: string;
  opacity: number;
}

export interface SupportResistanceLevel {
  price: number;
  type: 'support' | 'resistance';
  strength: number;
  touches: number;
  timestamp: number;
}

export interface SignalMarker {
  id: string;
  timestamp: number;
  price: number;
  type: 'buy' | 'sell';
  reason: string;
  confidence: number;
}

export interface ChartConfig {
  showHeikinAshi: boolean;
  showZones: boolean;
  showSignals: boolean;
  showSR: boolean;
  timeframe: string;
  symbol: string;
}

export interface IndicatorValues {
  rsi: number[];
  macd: { macd: number[]; signal: number[]; histogram: number[] };
  ema: { fast: number[]; slow: number[] };
  bollinger: { upper: number[]; middle: number[]; lower: number[] };
  volume: { sma: number[]; ratio: number[] };
}