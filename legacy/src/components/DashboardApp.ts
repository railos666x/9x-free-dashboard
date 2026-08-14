import { ChartRenderer } from '../charts/ChartRenderer.js';
import { BinanceWebSocket } from '../ws/BinanceWebSocket.js';
import { generateSignals, getLatestSignal, calculateAllIndicators } from '../charts/signals.js';
import type { ChartConfig, MarketData, SignalMarker, SupportResistanceLevel, SignalZone, IndicatorValues } from '../utils/types.js';

export class DashboardApp {
  private renderer: ChartRenderer;
  private ws: BinanceWebSocket;
  private config: ChartConfig;
  private historicalData: MarketData[] = [];
  private isLoading = false;
  private signalZones: SignalZone[] = [];
  private signalMarkers: SignalMarker[] = [];
  private srLevels: SupportResistanceLevel[] = [];

  private elements: {
    canvas: HTMLCanvasElement;
    symbolSelect: HTMLSelectElement;
    timeframeSelect: HTMLSelectElement;
    haToggle: HTMLInputElement;
    zonesToggle: HTMLInputElement;
    srToggle: HTMLInputElement;
    signalsToggle: HTMLInputElement;
    connectBtn: HTMLButtonElement;
    statusEl: HTMLElement;
    loadingEl: HTMLElement;
    signalsList: HTMLElement;
    srList: HTMLElement;
    marketInfo: HTMLElement;
    indicatorsPanel: HTMLElement;
    latestSignalEl: HTMLElement;
  };

  constructor() {
    this.config = {
      showHeikinAshi: true,
      showZones: true,
      showSignals: true,
      showSR: true,
      timeframe: '1m',
      symbol: 'BTCUSDT',
    };

    this.elements = this.getElements();
    this.renderer = new ChartRenderer(this.elements.canvas, this.config);
    this.ws = new BinanceWebSocket({
      url: 'wss://stream.binance.com:9443/ws',
      symbol: this.config.symbol,
      timeframe: this.config.timeframe,
    });

    this.bindEvents();
    this.loadHistoricalData();
    this.setupWebSocket();
  }

  private getElements() {
    return {
      canvas: document.getElementById('chart-canvas') as HTMLCanvasElement,
      symbolSelect: document.getElementById('symbol-select') as HTMLSelectElement,
      timeframeSelect: document.getElementById('timeframe-select') as HTMLSelectElement,
      haToggle: document.getElementById('ha-toggle') as HTMLInputElement,
      zonesToggle: document.getElementById('zones-toggle') as HTMLInputElement,
      srToggle: document.getElementById('sr-toggle') as HTMLInputElement,
      signalsToggle: document.getElementById('signals-toggle') as HTMLInputElement,
      connectBtn: document.getElementById('connect-btn') as HTMLButtonElement,
      statusEl: document.getElementById('connection-status') as HTMLElement,
      loadingEl: document.getElementById('loading-indicator') as HTMLElement,
      signalsList: document.getElementById('signals-list') as HTMLElement,
      srList: document.getElementById('sr-list') as HTMLElement,
      marketInfo: document.getElementById('market-info') as HTMLElement,
      indicatorsPanel: document.getElementById('indicators-panel') as HTMLElement,
      latestSignalEl: document.getElementById('latest-signal') as HTMLElement,
    };
  }

  private bindEvents() {
    this.elements.symbolSelect.addEventListener('change', (e) => {
      const symbol = (e.target as HTMLSelectElement).value;
      this.config.symbol = symbol;
      this.ws.disconnect();
      this.ws = new BinanceWebSocket({
        url: 'wss://stream.binance.com:9443/ws',
        symbol,
        timeframe: this.config.timeframe,
      });
      this.setupWebSocket();
      this.loadHistoricalData();
    });

    this.elements.timeframeSelect.addEventListener('change', (e) => {
      const timeframe = (e.target as HTMLSelectElement).value;
      this.config.timeframe = timeframe;
      this.ws.disconnect();
      this.ws = new BinanceWebSocket({
        url: 'wss://stream.binance.com:9443/ws',
        symbol: this.config.symbol,
        timeframe,
      });
      this.setupWebSocket();
      this.loadHistoricalData();
    });

    this.elements.haToggle.addEventListener('change', (e) => {
      this.config.showHeikinAshi = (e.target as HTMLInputElement).checked;
      this.renderer.setConfig(this.config);
    });

    this.elements.zonesToggle.addEventListener('change', (e) => {
      this.config.showZones = (e.target as HTMLInputElement).checked;
      this.renderer.setConfig(this.config);
    });

    this.elements.srToggle.addEventListener('change', (e) => {
      this.config.showSR = (e.target as HTMLInputElement).checked;
      this.renderer.setConfig(this.config);
    });

    this.elements.signalsToggle.addEventListener('change', (e) => {
      this.config.showSignals = (e.target as HTMLInputElement).checked;
      this.renderer.setConfig(this.config);
    });

    this.elements.connectBtn.addEventListener('click', () => this.toggleConnection());

    // Demo signal button
    document.getElementById('demo-signal')?.addEventListener('click', () => this.addDemoSignal());
    document.getElementById('detect-sr')?.addEventListener('click', () => this.renderer.autoDetectSR());
  }

  private setupWebSocket() {
    this.ws.onData((data) => this.onMarketData(data));
    this.ws.onStatus((connected) => this.onConnectionStatus(connected));
    this.ws.onError((error) => console.error('WS Error:', error));
    this.ws.connect();
  }

  private async loadHistoricalData() {
    this.setLoading(true);
    try {
      const interval = this.config.timeframe.replace('m', 'm').replace('h', 'h').replace('d', 'd');
      const response = await fetch(
        `https://api.binance.com/api/v3/klines?symbol=${this.config.symbol}&interval=${interval}&limit=500`
      );
      const data = await response.json();

      this.historicalData = data.map((k: any[]) => ({
        symbol: this.config.symbol,
        timestamp: k[0],
        open: parseFloat(k[1]),
        high: parseFloat(k[2]),
        low: parseFloat(k[3]),
        close: parseFloat(k[4]),
        volume: parseFloat(k[5]),
      }));

      this.renderer.setData(this.historicalData);
      this.generateAndRenderSignals();
      this.updateIndicatorsPanel();
      this.updateLatestSignal();
    } catch (error) {
      console.error('Failed to load historical data:', error);
    } finally {
      this.setLoading(false);
    }
  }

  private onMarketData(data: MarketData) {
    // Add new candle or update last
    const lastIndex = this.historicalData.findIndex(d => d.timestamp === data.timestamp);
    if (lastIndex >= 0) {
      this.historicalData[lastIndex] = data;
    } else {
      this.historicalData.push(data);
      // Keep last 500 candles
      if (this.historicalData.length > 500) {
        this.historicalData.shift();
      }
    }
    this.renderer.setData(this.historicalData);
    this.generateAndRenderSignals();
    this.updateIndicatorsPanel();
    this.updateLatestSignal();
    this.updateMarketInfo();
  }

  private generateAndRenderSignals() {
    if (this.historicalData.length < 50) return;

    const signals = generateSignals(this.historicalData);

    // Clear old signals and zones
    this.signalMarkers = [];
    this.signalZones = [];

    // Add new signals
    for (const signal of signals) {
      const atr = this.calculateATR(this.historicalData, this.historicalData.findIndex(d => d.timestamp === signal.timestamp), 14);
      const isLong = signal.type === 'buy';
      const tp = isLong ? signal.price + atr * 2 : signal.price - atr * 2;
      const sl = isLong ? signal.price - atr * 1.5 : signal.price + atr * 1.5;

      const zones = this.renderer.addSignalZone(signal.price, tp, sl, signal.timestamp);
      this.signalZones.push(...zones);
      this.renderer.addSignalMarker(signal);
      this.signalMarkers.push(signal);
    }

    this.updateSignalsList();
    this.updateSRList(); // Also update SR when data changes
  }

  private calculateATR(data: MarketData[], index: number, period: number): number {
    if (index < 0 || index >= data.length) return data[data.length - 1]?.close * 0.01 || 0;
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

  private updateIndicatorsPanel() {
    if (!this.elements.indicatorsPanel || this.historicalData.length < 30) return;

    const indicators = calculateAllIndicators(this.historicalData);
    const lastIdx = this.historicalData.length - 1;

    const rsi = indicators.rsi[lastIdx];
    const macd = indicators.macd.macd[lastIdx];
    const macdSignal = indicators.macd.signal[lastIdx];
    const macdHist = indicators.macd.histogram[lastIdx];
    const emaFast = indicators.ema.fast[lastIdx];
    const emaSlow = indicators.ema.slow[lastIdx];
    const bbUpper = indicators.bollinger.upper[lastIdx];
    const bbMiddle = indicators.bollinger.middle[lastIdx];
    const bbLower = indicators.bollinger.lower[lastIdx];
    const volRatio = indicators.volume.ratio[lastIdx];

    const lastPrice = this.historicalData[lastIdx].close;

    const rsiClass = rsi > 70 ? 'overbought' : rsi < 30 ? 'oversold' : 'neutral';
    const macdClass = macdHist > 0 ? 'bullish' : 'bearish';
    const emaClass = emaFast > emaSlow ? 'bullish' : 'bearish';
    const bbClass = lastPrice > bbUpper ? 'overbought' : lastPrice < bbLower ? 'oversold' : 'neutral';
    const volClass = volRatio > 1.5 ? 'high' : volRatio < 0.5 ? 'low' : 'normal';

    this.elements.indicatorsPanel.innerHTML = `
      <div class="indicator-row">
        <span class="ind-label">RSI (14)</span>
        <span class="ind-value ${rsiClass}">${isNaN(rsi) ? '—' : rsi.toFixed(1)}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">MACD</span>
        <span class="ind-value ${macdClass}">${isNaN(macd) ? '—' : macd.toFixed(4)}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">Signal</span>
        <span class="ind-value">${isNaN(macdSignal) ? '—' : macdSignal.toFixed(4)}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">Histogram</span>
        <span class="ind-value ${macdClass}">${isNaN(macdHist) ? '—' : macdHist.toFixed(4)}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">EMA 9</span>
        <span class="ind-value">${isNaN(emaFast) ? '—' : emaFast.toFixed(2)}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">EMA 21</span>
        <span class="ind-value">${isNaN(emaSlow) ? '—' : emaSlow.toFixed(2)}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">EMA Trend</span>
        <span class="ind-value ${emaClass}">${emaClass.toUpperCase()}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">BB Upper</span>
        <span class="ind-value">${isNaN(bbUpper) ? '—' : bbUpper.toFixed(2)}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">BB Mid</span>
        <span class="ind-value">${isNaN(bbMiddle) ? '—' : bbMiddle.toFixed(2)}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">BB Lower</span>
        <span class="ind-value">${isNaN(bbLower) ? '—' : bbLower.toFixed(2)}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">BB Position</span>
        <span class="ind-value ${bbClass}">${bbClass.toUpperCase()}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">Vol Ratio</span>
        <span class="ind-value ${volClass}">${isNaN(volRatio) ? '—' : volRatio.toFixed(2)}x</span>
      </div>
    `;
  }

  private updateLatestSignal() {
    if (!this.elements.latestSignalEl || this.historicalData.length < 50) return;

    const signal = getLatestSignal(this.historicalData);

    if (!signal) {
      this.elements.latestSignalEl.innerHTML = '<div class="empty-state">No signal generated</div>';
      return;
    }

    const atr = this.calculateATR(this.historicalData, this.historicalData.findIndex(d => d.timestamp === signal.timestamp), 14);
    const isLong = signal.type === 'buy';
    const tp = isLong ? signal.price + atr * 2 : signal.price - atr * 2;
    const sl = isLong ? signal.price - atr * 1.5 : signal.price + atr * 1.5;
    const rr = Math.abs((tp - signal.price) / (signal.price - sl));

    const age = Date.now() - signal.timestamp;
    const ageMin = Math.floor(age / 60000);
    const ageStr = ageMin < 60 ? `${ageMin}m ago` : `${Math.floor(ageMin / 60)}h ${ageMin % 60}m ago`;

    this.elements.latestSignalEl.innerHTML = `
      <div class="latest-signal ${signal.type}">
        <div class="signal-header">
          <span class="signal-direction">${signal.type === 'buy' ? '🟢 LONG' : '🔴 SHORT'}</span>
          <span class="signal-confidence">${(signal.confidence * 100).toFixed(0)}%</span>
        </div>
        <div class="signal-details">
          <div><strong>Entry:</strong> ${signal.price.toFixed(2)}</div>
          <div><strong>TP:</strong> ${tp.toFixed(2)}</div>
          <div><strong>SL:</strong> ${sl.toFixed(2)}</div>
          <div><strong>R:R:</strong> 1:${rr.toFixed(1)}</div>
          <div class="signal-reason"><strong>Why:</strong> ${signal.reason}</div>
          <div class="signal-age">${ageStr}</div>
        </div>
        <button class="btn btn-sm btn-primary" onclick="dashboardApp.applyLatestSignal()">Apply Signal</button>
      </div>
    `;
  }

  applyLatestSignal() {
    const signal = getLatestSignal(this.historicalData);
    if (!signal) return;

    const atr = this.calculateATR(this.historicalData, this.historicalData.findIndex(d => d.timestamp === signal.timestamp), 14);
    const isLong = signal.type === 'buy';
    const tp = isLong ? signal.price + atr * 2 : signal.price - atr * 2;
    const sl = isLong ? signal.price - atr * 1.5 : signal.price + atr * 1.5;

    const zones = this.renderer.addSignalZone(signal.price, tp, sl, signal.timestamp);
    this.signalZones.push(...zones);
    this.renderer.addSignalMarker(signal);
    this.signalMarkers.push(signal);
    this.updateSignalsList();
    this.updateLatestSignal();
  }

  private onConnectionStatus(connected: boolean) {
    this.elements.statusEl.textContent = connected ? '● Connected' : '○ Disconnected';
    this.elements.statusEl.style.color = connected ? '#00C853' : '#FF1744';
    this.elements.connectBtn.textContent = connected ? 'Disconnect' : 'Connect';
  }

  private toggleConnection() {
    if (this.ws.isConnected()) {
      this.ws.disconnect();
    } else {
      this.ws.connect();
    }
  }

  private setLoading(loading: boolean) {
    this.isLoading = loading;
    this.elements.loadingEl.style.display = loading ? 'block' : 'none';
  }

  private addDemoSignal() {
    if (this.historicalData.length === 0) return;

    const lastCandle = this.historicalData[this.historicalData.length - 1];
    const entry = lastCandle.close;
    const isLong = Math.random() > 0.5;
    const tp = isLong ? entry * 1.02 : entry * 0.98;
    const sl = isLong ? entry * 0.99 : entry * 1.01;

    const zones = this.renderer.addSignalZone(entry, tp, sl, lastCandle.timestamp);
    this.signalZones.push(...zones);

    const marker: SignalMarker = {
      id: `signal-${Date.now()}`,
      timestamp: lastCandle.timestamp,
      price: entry,
      type: isLong ? 'buy' : 'sell',
      reason: `Demo ${isLong ? 'Long' : 'Short'} Signal - RSI oversold + Volume spike`,
      confidence: 0.85,
    };
    this.renderer.addSignalMarker(marker);
    this.signalMarkers.push(marker);
    this.updateSignalsList();
    this.updateMarketInfo();
  }

  private updateSignalsList() {
    if (!this.elements.signalsList) return;

    if (this.signalMarkers.length === 0) {
      this.elements.signalsList.innerHTML = '<div class="empty-state">No active signals</div>';
      return;
    }

    this.elements.signalsList.innerHTML = this.signalMarkers
      .slice(-10)
      .reverse()
      .map(marker => {
        const zone = this.signalZones.find(z => z.id.includes(marker.id.replace('signal-', '')));
        const tp = zone?.tpPrice || 0;
        const sl = zone?.slPrice || 0;
        const rr = zone && zone.entryPrice ? Math.abs((tp - zone.entryPrice) / (zone.entryPrice - sl)) : 0;

        return `
          <div class="signal-item" data-id="${marker.id}">
            <div class="signal-type ${marker.type}">${marker.type === 'buy' ? 'LONG' : 'SHORT'}</div>
            <div class="signal-info">
              <span class="signal-price">Entry: ${marker.price.toFixed(2)}</span>
              <span class="signal-targets">TP: ${tp.toFixed(2)} | SL: ${sl.toFixed(2)} | R:R ${rr.toFixed(1)}</span>
              <span class="signal-reason">${marker.reason}</span>
            </div>
            <button class="btn btn-sm btn-danger" onclick="dashboardApp.removeSignal('${marker.id}')">×</button>
          </div>
        `;
      }).join('');
  }

  private updateSRList() {
    if (!this.elements.srList) return;

    if (this.srLevels.length === 0) {
      this.elements.srList.innerHTML = '<div class="empty-state">No S/R levels detected</div>';
      return;
    }

    this.elements.srList.innerHTML = this.srLevels
      .slice(0, 10)
      .map(level => `
        <div class="sr-item">
          <span class="sr-type ${level.type}">${level.type.toUpperCase()}</span>
          <span class="sr-price">${level.price.toFixed(2)}</span>
          <span class="sr-strength">${level.touches} touches (${(level.strength * 100).toFixed(0)}%)</span>
        </div>
      `).join('');
  }

  private updateMarketInfo() {
    if (!this.elements.marketInfo || this.historicalData.length === 0) return;

    const last = this.historicalData[this.historicalData.length - 1];
    const prev = this.historicalData[this.historicalData.length - 2];
    const change = prev ? ((last.close - prev.close) / prev.close * 100) : 0;
    const changeClass = change >= 0 ? 'positive' : 'negative';
    const changeSign = change >= 0 ? '+' : '';

    const high24h = Math.max(...this.historicalData.slice(-1440).map(d => d.high));
    const low24h = Math.min(...this.historicalData.slice(-1440).map(d => d.low));
    const volume24h = this.historicalData.slice(-1440).reduce((sum, d) => sum + d.volume, 0);

    this.elements.marketInfo.innerHTML = `
      <div class="info-item">
        <span class="info-label">Last Price</span>
        <span class="info-value">${last.close.toFixed(2)}</span>
      </div>
      <div class="info-item">
        <span class="info-label">24h Change</span>
        <span class="info-value ${changeClass}">${changeSign}${change.toFixed(2)}%</span>
      </div>
      <div class="info-item">
        <span class="info-label">24h High</span>
        <span class="info-value">${high24h.toFixed(2)}</span>
      </div>
      <div class="info-item">
        <span class="info-label">24h Low</span>
        <span class="info-value">${low24h.toFixed(2)}</span>
      </div>
      <div class="info-item">
        <span class="info-label">Volume (24h)</span>
        <span class="info-value">${(volume24h / 1e6).toFixed(1)}M</span>
      </div>
      <div class="info-item">
        <span class="info-label">Spread</span>
        <span class="info-value">${((high24h - low24h) / last.close * 100).toFixed(2)}%</span>
      </div>
    `;
  }

  removeSignal(id: string) {
    this.signalMarkers = this.signalMarkers.filter(m => m.id !== id);
    this.signalZones = this.signalZones.filter(z => !z.id.includes(id.replace('signal-', '')));
    this.updateSignalsList();
    // Re-render chart without the signal
    this.renderer.setData(this.historicalData);
  }

  destroy() {
    this.ws.disconnect();
    this.renderer.destroy();
  }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  const app = new DashboardApp();
  (window as any).dashboardApp = app;
});