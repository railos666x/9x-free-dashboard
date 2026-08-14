import type { MarketData, HeikinAshiData, SignalZone, SupportResistanceLevel, SignalMarker, ChartConfig } from '../utils/types.js';
import { calculateHeikinAshi, detectTrend } from './heikinashi.js';
import { calculateSignalZones, detectSupportResistance } from './zones.js';

export class ChartRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private data: MarketData[] = [];
  private haData: HeikinAshiData[] = [];
  private zones: SignalZone[] = [];
  private srLevels: SupportResistanceLevel[] = [];
  private markers: SignalMarker[] = [];
  private config: ChartConfig;
  private width: number = 0;
  private height: number = 0;
  private dpr: number = 1;
  private priceScale: number = 0;
  private timeScale: number = 0;
  private minPrice: number = 0;
  private maxPrice: number = 0;
  private animationId: number | null = null;
  private hoverMarker: SignalMarker | null = null;
  private hoverZone: SignalZone | null = null;
  private hoverSR: SupportResistanceLevel | null = null;

  constructor(canvas: HTMLCanvasElement, config: Partial<ChartConfig> = {}) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D context not supported');
    this.ctx = ctx;
    this.dpr = window.devicePixelRatio || 1;

    this.config = {
      showHeikinAshi: true,
      showZones: true,
      showSignals: true,
      showSR: true,
      timeframe: '1m',
      symbol: 'BTCUSDT',
      ...config,
    };

    this.resize();
    window.addEventListener('resize', () => this.resize());
    this.bindMouseEvents();
  }

  private bindMouseEvents() {
    this.canvas.addEventListener('mousemove', (e) => this.onMouseMove(e));
    this.canvas.addEventListener('mouseleave', () => this.onMouseLeave());
    this.canvas.addEventListener('click', (e) => this.onClick(e));
  }

  private onMouseMove(e: MouseEvent) {
    const rect = this.canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) * this.dpr;
    const y = (e.clientY - rect.top) * this.dpr;

    this.hoverMarker = null;
    this.hoverZone = null;
    this.hoverSR = null;

    if (this.data.length === 0) return;

    const displayData = this.config.showHeikinAshi ? this.haData : this.data;
    const chartW = this.canvas.width / this.dpr;
    const chartH = this.canvas.height / this.dpr;

    // Check markers
    if (this.config.showSignals) {
      for (const marker of this.markers) {
        const idx = displayData.findIndex(d => d.timestamp >= marker.timestamp);
        if (idx === -1) continue;
        const mx = this.timeToX(idx) * this.dpr;
        const price = this.config.showHeikinAshi ? (displayData[idx] as HeikinAshiData).haClose : displayData[idx].close;
        const my = this.priceToY(price) * this.dpr;
        if (Math.hypot(mx - x, my - y) < 15 * this.dpr) {
          this.hoverMarker = marker;
          break;
        }
      }
    }

    // Check zones
    if (this.config.showZones && !this.hoverMarker) {
      for (const zone of this.zones) {
        const startIdx = this.data.findIndex(d => d.timestamp >= zone.startTime);
        const endIdx = zone.endTime
          ? this.data.findIndex(d => d.timestamp >= zone.endTime)
          : this.data.length - 1;
        if (startIdx === -1) continue;

        const x1 = this.timeToX(startIdx) * this.dpr;
        const x2 = this.timeToX(Math.max(startIdx, endIdx)) * this.dpr;
        const y1 = this.priceToY(zone.entryPrice) * this.dpr;
        const y2 = this.priceToY(zone.tpPrice) * this.dpr;
        const y3 = this.priceToY(zone.slPrice) * this.dpr;

        const minY = Math.min(y1, y2, y3);
        const maxY = Math.max(y1, y2, y3);
        if (x >= x1 && x <= x2 && y >= minY && y <= maxY) {
          this.hoverZone = zone;
          break;
        }
      }
    }

    // Check S/R levels
    if (this.config.showSR && !this.hoverMarker && !this.hoverZone) {
      for (const level of this.srLevels) {
        const ly = this.priceToY(level.price) * this.dpr;
        if (Math.abs(y - ly) < 8 * this.dpr) {
          this.hoverSR = level;
          break;
        }
      }
    }

    this.render();
  }

  private onMouseLeave() {
    this.hoverMarker = null;
    this.hoverZone = null;
    this.hoverSR = null;
    this.render();
  }

  private onClick(e: MouseEvent) {
    // Could add click-to-place-order or click-to-add-signal here
  }

  // Public getters for tooltip
  getHoverMarker(): SignalMarker | null { return this.hoverMarker; }
  getHoverZone(): SignalZone | null { return this.hoverZone; }
  getHoverSR(): SupportResistanceLevel | null { return this.hoverSR; }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    this.width = rect.width * this.dpr;
    this.height = rect.height * this.dpr;
    this.canvas.width = this.width;
    this.canvas.height = this.height;
    this.canvas.style.width = `${rect.width}px`;
    this.canvas.style.height = `${rect.height}px`;
    this.ctx.scale(this.dpr, this.dpr);
    this.render();
  }

  setData(data: MarketData[]) {
    this.data = data;
    this.haData = calculateHeikinAshi(data);
    this.calculateScales();
    this.render();
  }

  setConfig(config: Partial<ChartConfig>) {
    this.config = { ...this.config, ...config };
    this.render();
  }

  addSignalZone(entry: number, tp: number, sl: number, startTime: number, endTime?: number) {
    const newZones = calculateSignalZones(entry, tp, sl, startTime, endTime);
    this.zones.push(...newZones);
    this.render();
  }

  addSignalMarker(marker: SignalMarker) {
    this.markers.push(marker);
    this.render();
  }

  autoDetectSR() {
    this.srLevels = detectSupportResistance(this.data);
    this.render();
  }

  private calculateScales() {
    if (this.data.length === 0) return;

    const displayData = this.config.showHeikinAshi ? this.haData : this.data;
    this.minPrice = Math.min(...displayData.map(d => this.config.showHeikinAshi ? (d as HeikinAshiData).haLow : d.low));
    this.maxPrice = Math.max(...displayData.map(d => this.config.showHeikinAshi ? (d as HeikinAshiData).haHigh : d.high));

    const padding = (this.maxPrice - this.minPrice) * 0.1;
    this.minPrice -= padding;
    this.maxPrice += padding;

    const chartWidth = this.canvas.width / this.dpr - 80;
    const chartHeight = this.canvas.height / this.dpr - 60;

    this.priceScale = chartHeight / (this.maxPrice - this.minPrice);
    this.timeScale = chartWidth / Math.max(1, displayData.length - 1);
  }

  private priceToY(price: number): number {
    const chartHeight = this.canvas.height / this.dpr - 60;
    return 30 + (this.maxPrice - price) * this.priceScale;
  }

  private timeToX(index: number): number {
    return 60 + index * this.timeScale;
  }

  render() {
    const ctx = this.ctx;
    const w = this.canvas.width / this.dpr;
    const h = this.canvas.height / this.dpr;

    ctx.clearRect(0, 0, w, h);

    // Background
    ctx.fillStyle = '#0B0F1A';
    ctx.fillRect(0, 0, w, h);

    // Grid
    this.drawGrid(ctx, w, h);

    if (this.data.length === 0) return;

    const displayData = this.config.showHeikinAshi ? this.haData : this.data;

    // Zones (drawn first, behind candles)
    if (this.config.showZones) this.drawZones(ctx);

    // Support/Resistance
    if (this.config.showSR) this.drawSupportResistance(ctx);

    // Candles
    this.drawCandles(ctx, displayData);

    // Signal markers
    if (this.config.showSignals) this.drawMarkers(ctx, displayData);

    // Price scale
    this.drawPriceScale(ctx, h);

    // Time scale
    this.drawTimeScale(ctx, w, displayData);

    // Trend indicator
    this.drawTrendIndicator(ctx, w);
  }

  private drawGrid(ctx: CanvasRenderingContext2D, w: number, h: number) {
    ctx.strokeStyle = '#1E293B';
    ctx.lineWidth = 1;

    // Horizontal grid lines
    for (let i = 0; i <= 8; i++) {
      const y = 30 + (h - 90) * i / 8;
      ctx.beginPath();
      ctx.moveTo(60, y);
      ctx.lineTo(w - 20, y);
      ctx.stroke();
    }

    // Vertical grid lines
    for (let i = 0; i <= 10; i++) {
      const x = 60 + (w - 80) * i / 10;
      ctx.beginPath();
      ctx.moveTo(x, 30);
      ctx.lineTo(x, h - 30);
      ctx.stroke();
    }
  }

  private drawZones(ctx: CanvasRenderingContext2D) {
    for (const zone of this.zones) {
      const startIdx = this.data.findIndex(d => d.timestamp >= zone.startTime);
      const endIdx = zone.endTime
        ? this.data.findIndex(d => d.timestamp >= zone.endTime)
        : this.data.length - 1;

      if (startIdx === -1) continue;

      const x1 = this.timeToX(startIdx);
      const x2 = this.timeToX(Math.max(startIdx, endIdx));
      const y1 = this.priceToY(zone.entryPrice);
      const y2 = this.priceToY(zone.tpPrice);
      const y3 = this.priceToY(zone.slPrice);

      // TP Zone (green)
      ctx.fillStyle = zone.color;
      ctx.globalAlpha = zone.opacity;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y1);
      ctx.lineTo(x2, y2);
      ctx.lineTo(x1, y2);
      ctx.closePath();
      ctx.fill();

      // SL Zone (red)
      const slColor = zone.type === 'long' ? '#FF1744' : '#FF1744';
      ctx.fillStyle = slColor;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y1);
      ctx.lineTo(x2, y3);
      ctx.lineTo(x1, y3);
      ctx.closePath();
      ctx.fill();

      ctx.globalAlpha = 1;
    }
  }

  private drawSupportResistance(ctx: CanvasRenderingContext2D) {
    for (const level of this.srLevels) {
      const y = this.priceToY(level.price);
      const w = this.canvas.width / this.dpr;

      ctx.strokeStyle = level.type === 'support' ? '#00C853' : '#FF1744';
      ctx.lineWidth = 1 + level.strength;
      ctx.setLineDash([5, 5]);
      ctx.globalAlpha = 0.5 + level.strength * 0.3;

      ctx.beginPath();
      ctx.moveTo(60, y);
      ctx.lineTo(w - 20, y);
      ctx.stroke();

      // Label
      ctx.fillStyle = ctx.strokeStyle;
      ctx.globalAlpha = 0.8;
      ctx.font = '10px monospace';
      ctx.textAlign = 'right';
      ctx.fillText(`${level.type === 'support' ? 'S' : 'R'}: ${level.price.toFixed(2)}`, 55, y + 3);

      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
  }

  private drawCandles(ctx: CanvasRenderingContext2D, data: (MarketData | HeikinAshiData)[]) {
    const candleWidth = Math.max(1, this.timeScale * 0.7);

    for (let i = 0; i < data.length; i++) {
      const candle = data[i];
      const x = this.timeToX(i);
      const isHA = this.config.showHeikinAshi;

      const open = isHA ? (candle as HeikinAshiData).haOpen : candle.open;
      const close = isHA ? (candle as HeikinAshiData).haClose : candle.close;
      const high = isHA ? (candle as HeikinAshiData).haHigh : candle.high;
      const low = isHA ? (candle as HeikinAshiData).haLow : candle.low;

      const isBullish = close >= open;
      const color = isBullish ? '#00C853' : '#FF1744';

      const yOpen = this.priceToY(open);
      const yClose = this.priceToY(close);
      const yHigh = this.priceToY(high);
      const yLow = this.priceToY(low);

      // Wick
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yHigh);
      ctx.lineTo(x, yLow);
      ctx.stroke();

      // Body
      const bodyHeight = Math.max(1, Math.abs(yClose - yOpen));
      ctx.fillStyle = color;
      ctx.fillRect(x - candleWidth / 2, Math.min(yOpen, yClose), candleWidth, bodyHeight);
    }
  }

  private drawMarkers(ctx: CanvasRenderingContext2D, data: (MarketData | HeikinAshiData)[]) {
    for (const marker of this.markers) {
      const idx = data.findIndex(d => d.timestamp >= marker.timestamp);
      if (idx === -1) continue;

      const x = this.timeToX(idx);
      const price = this.config.showHeikinAshi
        ? (data[idx] as HeikinAshiData).haClose
        : data[idx].close;
      const y = this.priceToY(price);

      ctx.fillStyle = marker.type === 'buy' ? '#00C853' : '#FF1744';
      ctx.beginPath();
      if (marker.type === 'buy') {
        ctx.moveTo(x, y + 8);
        ctx.lineTo(x - 6, y + 18);
        ctx.lineTo(x + 6, y + 18);
      } else {
        ctx.moveTo(x, y - 8);
        ctx.lineTo(x - 6, y - 18);
        ctx.lineTo(x + 6, y - 18);
      }
      ctx.closePath();
      ctx.fill();
    }
  }

  private drawPriceScale(ctx: CanvasRenderingContext2D, h: number) {
    ctx.fillStyle = '#64748B';
    ctx.font = '11px monospace';
    ctx.textAlign = 'right';

    for (let i = 0; i <= 8; i++) {
      const price = this.maxPrice - (this.maxPrice - this.minPrice) * i / 8;
      const y = 30 + (h - 90) * i / 8;
      ctx.fillText(price.toFixed(2), 55, y + 4);
    }
  }

  private drawTimeScale(ctx: CanvasRenderingContext2D, w: number, data: (MarketData | HeikinAshiData)[]) {
    ctx.fillStyle = '#64748B';
    ctx.font = '10px monospace';
    ctx.textAlign = 'center';

    const step = Math.max(1, Math.floor(data.length / 10));
    for (let i = 0; i < data.length; i += step) {
      const x = this.timeToX(i);
      const date = new Date(data[i].timestamp);
      ctx.fillText(`${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`, x, h - 15);
    }
  }

  private drawTrendIndicator(ctx: CanvasRenderingContext2D, w: number) {
    const trend = detectTrend(this.haData);
    const colors = { bullish: '#00C853', bearish: '#FF1744', neutral: '#FFB300' };

    ctx.fillStyle = colors[trend];
    ctx.font = 'bold 12px monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`${this.config.symbol} | ${this.config.timeframe} | ${trend.toUpperCase()}`, 65, 22);
  }

  destroy() {
    if (this.animationId) cancelAnimationFrame(this.animationId);
    window.removeEventListener('resize', () => this.resize());
  }
}