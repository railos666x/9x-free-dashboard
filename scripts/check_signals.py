"""Check what signals the AI is generating."""
import sys
sys.path.insert(0, 'backend')

import database
import news_analyst
import config

conn = database.connect()

# Force fresh analysis
print("Refreshing analysis...")
news_analyst.analyze_market(conn, force=True)

print("\n=== Signal Check for Trading Universe ===")
print(f"Trading universe: {len(config.TRADING_UNIVERSE)} symbols")
print(f"AI_SENTIMENT_LONG: {config.AI_SENTIMENT_LONG}")
print(f"AI_SENTIMENT_SHORT: {config.AI_SENTIMENT_SHORT}")
print(f"AI_CONFIDENCE_FLOOR: {config.AI_CONFIDENCE_FLOOR}")
print()

signals = []
for symbol in config.TRADING_UNIVERSE:
    try:
        s = news_analyst.signal_for(conn, symbol)
        action = s.get('action')
        conf = s.get('confidence', 0)
        score = s.get('score', 0)
        sl_pct = s.get('stop_loss_pct', 0)
        tp_pct = s.get('take_profit_pct', 0)
        reason = s.get('reason', '')[:60]
        
        if action in ('LONG', 'SHORT'):
            signals.append({
                'symbol': symbol,
                'action': action,
                'confidence': conf,
                'score': score,
                'sl_pct': sl_pct,
                'tp_pct': tp_pct,
                'reason': reason
            })
            print(f"  {symbol:12s} {action:5s} conf={conf:.2f} score={score:+.2f} SL={sl_pct}% TP={tp_pct}%")
            print(f"    {reason}")
        elif conf > 0.2:  # Show near-misses
            print(f"  {symbol:12s} {action:5s} conf={conf:.2f} score={score:+.2f} (below threshold)")
            print(f"    {reason}")
    except Exception as e:
        print(f"  {symbol:12s} ERROR: {e}")

print(f"\n=== Summary ===")
print(f"LONG signals: {len([s for s in signals if s['action'] == 'LONG'])}")
print(f"SHORT signals: {len([s for s in signals if s['action'] == 'SHORT'])}")
print(f"Total actionable: {len(signals)}")

if signals:
    print("\n=== Top Signals ===")
    for s in sorted(signals, key=lambda x: x['confidence'], reverse=True)[:5]:
        print(f"  {s['symbol']}: {s['action']} conf={s['confidence']:.2f} SL={s['sl_pct']}% TP={s['tp_pct']}%")
