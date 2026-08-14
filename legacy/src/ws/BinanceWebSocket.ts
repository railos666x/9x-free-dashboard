import type { MarketData } from '../utils/types.js';

export interface WebSocketConfig {
  url: string;
  symbol: string;
  timeframe: string;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
}

export type DataCallback = (data: MarketData) => void;
export type StatusCallback = (connected: boolean) => void;
export type ErrorCallback = (error: Error) => void;

export class BinanceWebSocket {
  private ws: WebSocket | null = null;
  private config: WebSocketConfig;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private subscriptions: Set<string> = new Set();

  private onDataCallback: DataCallback | null = null;
  private onStatusCallback: StatusCallback | null = null;
  private onErrorCallback: ErrorCallback | null = null;

  constructor(config: WebSocketConfig) {
    this.config = {
      reconnectInterval: 3000,
      maxReconnectAttempts: 10,
      ...config,
    };
  }

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return;

    try {
      this.ws = new WebSocket(this.config.url);
      this.ws.binaryType = 'arraybuffer';

      this.ws.onopen = () => {
        console.log('[WS] Connected');
        this.reconnectAttempts = 0;
        this.subscribe(this.config.symbol, this.config.timeframe);
        this.startPing();
        this.onStatusCallback?.(true);
      };

      this.ws.onmessage = (event) => this.handleMessage(event);

      this.ws.onclose = () => {
        console.log('[WS] Disconnected');
        this.stopPing();
        this.onStatusCallback?.(false);
        this.scheduleReconnect();
      };

      this.ws.onerror = (error) => {
        console.error('[WS] Error:', error);
        this.onErrorCallback?.(error as Error);
      };
    } catch (error) {
      console.error('[WS] Connection failed:', error);
      this.scheduleReconnect();
    }
  }

  private handleMessage(event: MessageEvent) {
    try {
      const data = typeof event.data === 'string'
        ? JSON.parse(event.data)
        : JSON.parse(new TextDecoder().decode(event.data));

      if (data.e === 'kline' && data.k) {
        const k = data.k;
        if (k.x) { // Only closed candles
          const candle: MarketData = {
            symbol: k.s,
            timestamp: k.t,
            open: parseFloat(k.o),
            high: parseFloat(k.h),
            low: parseFloat(k.l),
            close: parseFloat(k.c),
            volume: parseFloat(k.v),
          };
          this.onDataCallback?.(candle);
        }
      } else if (data.e === '24hrTicker') {
        // Handle ticker updates
      } else if (data.result === null && data.id) {
        // Subscription confirmed
      }
    } catch (error) {
      console.error('[WS] Parse error:', error);
    }
  }

  subscribe(symbol: string, timeframe: string) {
    const stream = `${symbol.toLowerCase()}@kline_${timeframe}`;
    if (this.subscriptions.has(stream)) return;

    this.subscriptions.add(stream);
    this.send({
      method: 'SUBSCRIBE',
      params: [stream],
      id: Date.now(),
    });
  }

  unsubscribe(symbol: string, timeframe: string) {
    const stream = `${symbol.toLowerCase()}@kline_${timeframe}`;
    if (!this.subscriptions.has(stream)) return;

    this.subscriptions.delete(stream);
    this.send({
      method: 'UNSUBSCRIBE',
      params: [stream],
      id: Date.now(),
    });
  }

  private send(message: object) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  private startPing() {
    this.pingTimer = setInterval(() => {
      this.send({ method: 'ping', id: Date.now() });
    }, 20000);
  }

  private stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect() {
    if (this.reconnectAttempts >= (this.config.maxReconnectAttempts || 10)) {
      console.error('[WS] Max reconnect attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.config.reconnectInterval! * Math.min(this.reconnectAttempts, 5);

    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  onData(callback: DataCallback) {
    this.onDataCallback = callback;
  }

  onStatus(callback: StatusCallback) {
    this.onStatusCallback = callback;
  }

  onError(callback: ErrorCallback) {
    this.onErrorCallback = callback;
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.stopPing();
    this.subscriptions.clear();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}