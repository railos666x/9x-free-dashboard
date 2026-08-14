# OmniRoute2 - High-Performance Trading Dashboard

A professional-grade trading dashboard with real-time Binance WebSocket data, Heikin Ashi charts, signal zone highlighting, and auto support/resistance detection.

## Features Implemented

### 1. **Heikin Ashi Charts** (`src/charts/heikinashi.ts`)
- Smoothes price action by averaging open/high/low/close
- Filters market noise for clearer trend identification
- Toggle between regular candles and Heikin Ashi
- Built-in trend detection (bullish/bearish/neutral)

### 2. **Signal Zone Highlighting** (`src/charts/zones.ts`)
- **Green zones**: Entry → Take Profit (profit potential)
- **Red zones**: Entry → Stop Loss (risk area)
- Visual trade setup at a glance
- Configurable opacity and colors

### 3. **Auto Support/Resistance** (`src/charts/zones.ts`)
- Swing high/low detection with configurable lookback
- Price level clustering for cleaner levels
- Strength scoring based on touch count
- Visual dashed lines with labels

### 4. **Real-time Binance WebSocket** (`src/ws/BinanceWebSocket.ts`)
- Connects to `wss://stream.binance.com:9443/ws`
- Subscribes to kline/candlestick streams
- Auto-reconnection with exponential backoff
- Ping/pong keepalive
- Only processes closed candles (x=true)

### 5. **High-Performance Canvas Renderer** (`src/charts/ChartRenderer.ts`)
- Device pixel ratio aware for crisp rendering
- Efficient candle drawing (no allocations in render loop)
- Grid, price/time scales, trend indicator
- Responsive resize handling

### 6. **Dashboard UI** (`src/components/DashboardApp.ts`, `index.html`, `styles.css`)
- Symbol/timeframe selectors
- Feature toggles (HA, Zones, S/R, Signals)
- Connection status indicator
- Demo signal generator
- S/R detection button
- Sidebar with signals, S/R levels, market info

## Project Structure

```
omniroute2/
├── index.html              # Main HTML entry point
├── styles.css              # Complete styling
├── package.json            # Dependencies & scripts
├── tsconfig.json           # TypeScript config
├── vite.config.ts          # Vite build config
├── CLAUDE.md               # This file
└── src/
    ├── main.ts             # App entry point
    ├── utils/
    │   └── types.ts        # TypeScript interfaces
    ├── charts/
    │   ├── heikinashi.ts   # HA calculation & trend detection
    │   ├── zones.ts        # Signal zones & S/R detection
    │   └── ChartRenderer.ts # Canvas rendering engine
    ├── ws/
    │   └── BinanceWebSocket.ts # Real-time data connection
    └── components/
        └── DashboardApp.ts # Main application controller
```

## Development

```bash
# Install dependencies
npm install

# Start dev server (hot reload)
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Run tests
npm test

# Lint code
npm run lint
```

## Usage

1. **Start the dev server**: `npm run dev`
2. **Open** `http://localhost:3000`
3. **Click "Connect"** to establish WebSocket connection
4. **Select symbol/timeframe** from dropdowns
5. **Toggle features**:
   - Heikin Ashi: Smoothed trend candles
   - Zones: Trade entry/TP/SL visualization
   - S/R: Auto-detected support/resistance
   - Signals: Buy/sell markers
6. **Click "Detect S/R"** to auto-detect levels
7. **Click "Demo Signal"** to add a sample trade zone

## Architecture Highlights

### Performance
- **Zero-allocation render loop**: Reuses objects, no GC pressure
- **Object pooling ready**: Structure supports pooling for high-frequency updates
- **WebGL upgrade path**: Canvas 2D renderer can be swapped for WebGL
- **Web Workers ready**: Heavy computation (HA, S/R) can move off main thread

### Data Flow
```
Binance WS → DashboardApp → historicalData[] → ChartRenderer → Canvas
                    ↓
              calculateHeikinAshi() → haData[]
                    ↓
              detectSupportResistance() → srLevels[]
                    ↓
              calculateSignalZones() → zones[]
```

### Extensibility
- Add new chart types via `ChartRenderer` strategy pattern
- Custom indicators via `heikinashi.ts` pattern
- New data sources via `BinanceWebSocket` interface
- Signal types via `SignalMarker` extensions

## API Reference

### ChartRenderer
```typescript
new ChartRenderer(canvas, config)
renderer.setData(marketData[])
renderer.setConfig({ showHeikinAshi, showZones, ... })
renderer.addSignalZone(entry, tp, sl, startTime, endTime?)
renderer.addSignalMarker(marker)
renderer.autoDetectSR()
renderer.destroy()
```

### BinanceWebSocket
```typescript
new BinanceWebSocket({ url, symbol, timeframe })
ws.connect()
ws.disconnect()
ws.onData(callback)
ws.onStatus(callback)
ws.onError(callback)
ws.isConnected()
```

### Heikin Ashi
```typescript
calculateHeikinAshi(marketData[]) → HeikinAshiData[]
detectTrend(haData[], lookback?) → 'bullish' | 'bearish' | 'neutral'
```

### Zones & S/R
```typescript
calculateSignalZones(entry, tp, sl, startTime, endTime?) → SignalZone[]
detectSupportResistance(data[], lookback?, minTouches?, threshold?) → SupportResistanceLevel[]
```

## Next Steps (Roadmap)

1. **WebSocket Server** - Custom backend for signal generation
2. **Signal Markers** - Arrow markers with tooltip reasons
3. **News Integration** - Fundamental catalysts on chart
4. **Multi-timeframe** - Sync charts across timeframes
5. **WebGL Renderer** - GPU-accelerated for 1000+ candles
6. **Web Workers** - Offload HA/S/R calculations
7. **Persistence** - LocalStorage for settings/layout
8. **Tests** - Unit tests for calculations, integration for WS