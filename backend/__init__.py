"""
cryptoautomation.backend — Trading automation backend package.

Modules:
- config: Central configuration (symbols, API keys, risk params)
- token_registry: Unified symbol metadata registry (160 symbols, 3 venues)
- database: SQLite persistence layer
- execution: Broker routing (Binance/Alpaca/Exness MT5 + paper)
- risk: Position sizing, slippage, Kelly, circuit breaker
- math_engine: Bayesian signal fusion, regime detection, Kelly sizing
- strategy: Factor-based trading strategies
- bot: Orchestrator (AI/news → verification → execution → monitoring)
- market_data: Cross-source price verification (Alpaca)
- collector: Historical data backfill (Binance)
- backtester: Event-driven backtest with math_engine signals
- optimizer: Parameter optimization (HRP, parallel)
- server: FastAPI REST + WebSocket API
- ai_agents: LLM agent pipeline (news → chart → review)
- news_analyst: Groq news sentiment + 6-layer confirmation
- verification: Data quality gates
- calendar_guard: Macro event blackout windows
- dxy_filter: DXY master filter
- arbitrator: Intent deduplication + single-source routing
- circuit_breaker: Tiered drawdown step-down
- tutor: Educational topic catalogue
- portfolio: Position tracking + P&L
"""