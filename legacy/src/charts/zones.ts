import type { MarketData, SignalZone, SupportResistanceLevel } from '../utils/types.js';

export function calculateSignalZones(
  entryPrice: number,
  tpPrice: number,
  slPrice: number,
  startTime: number,
  endTime?: number
): SignalZone[] {
  const isLong = tpPrice > entryPrice;
  const zones: SignalZone[] = [];

  // TP Zone (Entry to TP) - Green
  zones.push({
    id: `tp-${startTime}`,
    type: isLong ? 'long' : 'short',
    entryPrice,
    tpPrice,
    slPrice,
    startTime,
    endTime,
    color: '#00C853',
    opacity: 0.15,
  });

  // SL Zone (Entry to SL) - Red
  zones.push({
    id: `sl-${startTime}`,
    type: isLong ? 'long' : 'short',
    entryPrice,
    tpPrice,
    slPrice,
    startTime,
    endTime,
    color: '#FF1744',
    opacity: 0.15,
  });

  return zones;
}

export function detectSupportResistance(
  data: MarketData[],
  lookback: number = 20,
  minTouches: number = 2,
  threshold: number = 0.002
): SupportResistanceLevel[] {
  if (data.length < lookback * 2) return [];

  const levels: Map<number, SupportResistanceLevel> = new Map();
  const highs: number[] = [];
  const lows: number[] = [];

  for (let i = lookback; i < data.length - lookback; i++) {
    const candle = data[i];
    const prevHighs = data.slice(i - lookback, i).map(c => c.high);
    const nextHighs = data.slice(i + 1, i + lookback + 1).map(c => c.high);
    const prevLows = data.slice(i - lookback, i).map(c => c.low);
    const nextLows = data.slice(i + 1, i + lookback + 1).map(c => c.low);

    const isSwingHigh = candle.high >= Math.max(...prevHighs) && candle.high >= Math.max(...nextHighs);
    const isSwingLow = candle.low <= Math.min(...prevLows) && candle.low <= Math.min(...nextLows);

    if (isSwingHigh) highs.push(candle.high);
    if (isSwingLow) lows.push(candle.low);
  }

  // Cluster nearby levels
  const clusterLevels = (prices: number[], type: 'support' | 'resistance') => {
    const sorted = [...prices].sort((a, b) => a - b);
    const clusters: number[][] = [];

    for (const price of sorted) {
      let found = false;
      for (const cluster of clusters) {
        const avg = cluster.reduce((a, b) => a + b, 0) / cluster.length;
        if (Math.abs(price - avg) / avg < threshold) {
          cluster.push(price);
          found = true;
          break;
        }
      }
      if (!found) clusters.push([price]);
    }

    for (const cluster of clusters) {
      if (cluster.length >= minTouches) {
        const avgPrice = cluster.reduce((a, b) => a + b, 0) / cluster.length;
        const key = Math.round(avgPrice * 10000);
        const existing = levels.get(key);
        if (!existing || existing.touches < cluster.length) {
          levels.set(key, {
            price: avgPrice,
            type,
            strength: cluster.length / minTouches,
            touches: cluster.length,
            timestamp: Date.now(),
          });
        }
      }
    }
  };

  clusterLevels(highs, 'resistance');
  clusterLevels(lows, 'support');

  return Array.from(levels.values()).sort((a, b) => b.strength - a.strength);
}