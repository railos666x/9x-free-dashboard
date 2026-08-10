"""
Bloomberg-style terminal dashboard.

Reads the SQLite database written by collector.py / news.py (or run_live.py)
and renders a dark, dense, multi-panel screen: watchlist, price chart with
indicators, a live news feed with sentiment badges, and market monitors.

Run:
    streamlit run dashboard.py
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp
import requests
import streamlit as st

import config
import database
import indicators

# ----------------------------------------------------------------------
# Page setup + terminal styling
# ----------------------------------------------------------------------
st.set_page_config(
    page_title="cryptoautomation",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    /* Dark terminal feel */
    html, body, [data-testid="stAppViewContainer"] { background: #0a0e12; }
    [data-testid="stHeader"] { background: transparent; }
    .term-title {
        font-family: 'Consolas', 'Courier New', monospace;
        color: #00d9a6; font-size: 22px; font-weight: bold;
        letter-spacing: 1px; border-bottom: 1px solid #1d2935;
        padding-bottom: 6px; margin-bottom: 4px;
    }
    .term-sub {
        font-family: 'Consolas', monospace; color: #8b98a5; font-size: 12px;
    }
    .badge-bull { color: #00d9a6; font-weight: bold; }
    .badge-bear { color: #ff6b6b; font-weight: bold; }
    .badge-neu  { color: #8b98a5; }
    .badge-imp  { color: #ffd23f; font-weight: bold; }
    .news-item {
        font-family: 'Segoe UI', sans-serif; font-size: 13px;
        border-left: 2px solid #1d2935; padding-left: 8px;
        margin-bottom: 10px; color: #d8e0e8;
    }
    .news-src { color: #5f6b76; font-size: 11px; }
    </style>
    """,
    unsafe_allow_html=True,
)

DB = database.connect()
database.init_db(DB)


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def format_price(x) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{x:,.2f}" if x >= 1 else f"{x:.6f}"


def age_label(published: str) -> str:
    """'3m ago', '2h ago', ... from an ISO timestamp."""
    try:
        ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
        delta = datetime.now(ts.tzinfo) - ts
        secs = max(int(delta.total_seconds()), 0)
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m"
        if secs < 86400:
            return f"{secs // 3600}h"
        return f"{secs // 86400}d"
    except Exception:
        return "?"


def sentiment_badge(sentiment: str) -> str:
    """HTML badge for a sentiment value."""
    icons = {
        "bullish": ("🟢", "badge-bull", "BULL"),
        "bearish": ("🔴", "badge-bear", "BEAR"),
        "neutral": ("⚪", "badge-neu", "NEUTRAL"),
        "important": ("⭐", "badge-imp", "IMPORTANT"),
    }
    icon, cls, label = icons.get(sentiment, icons["neutral"])
    return f'<span class="{cls}">{icon} {label}</span>'


def pct_color(value: float) -> str:
    return "#00d9a6" if value >= 0 else "#ff6b6b"


# ----------------------------------------------------------------------
# Sidebar controls
# ----------------------------------------------------------------------
st.sidebar.markdown('<div class="term-title">CONTROLS</div>', unsafe_allow_html=True)

symbol = st.sidebar.selectbox("Symbol", config.SYMBOLS, index=0)
interval = st.sidebar.selectbox(
    "Timeframe",
    config.INTERVALS,
    index=config.INTERVALS.index(config.DEFAULT_INTERVAL),
)
num_candles = st.sidebar.slider("Candles to show", 60, 1000, 300, step=20)

st.sidebar.markdown("**Chart overlays**")
show_sma = st.sidebar.checkbox("SMA 20/50", value=True)
show_bb = st.sidebar.checkbox("Bollinger Bands", value=False)
show_indicators = st.sidebar.checkbox("RSI + MACD panels", value=True)

st.sidebar.markdown("**News**")
news_filter = st.sidebar.selectbox(
    "Sentiment filter", ["all", "bullish", "bearish", "important", "neutral"]
)
review_symbol = st.sidebar.selectbox(
    "Review mode (news for one coin)",
    ["all"] + [s.split("/")[0] for s in config.SYMBOLS],
)
news_limit = st.sidebar.slider("News items", 10, 50, 25)

st.sidebar.markdown("---")
st.sidebar.markdown(
    f'<div class="term-sub">DB: {database.count_candles(DB)} candles</div>',
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------
# Main body (auto-refreshes)
# ----------------------------------------------------------------------
@st.fragment(run_every=config.DASHBOARD_REFRESH_SECONDS)
def render() -> None:
    # --- Header -------------------------------------------------------
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.markdown(
        f'<div class="term-title">📡 CRYPTOAUTOMATION TERMINAL</div>'
        f'<div class="term-sub">local time {now} · {symbol} · {interval} · '
        f'auto-refresh {config.DASHBOARD_REFRESH_SECONDS}s</div>',
        unsafe_allow_html=True,
    )

    # --- Load data ----------------------------------------------------
    candles = database.load_candles(DB, symbol, interval, limit=num_candles)
    df = indicators.add_all(candles)

    news_df = database.load_news(
        DB,
        limit=news_limit,
        sentiment=None if news_filter == "all" else news_filter,
        symbol=None if review_symbol == "all" else review_symbol,
    )

    col_watch, col_main, col_news = st.columns([1.0, 2.2, 1.3], gap="small")

    # ================== LEFT: watchlist ===============================
    with col_watch:
        st.markdown('<div class="term-sub">WATCHLIST</div>', unsafe_allow_html=True)
        for s in config.SYMBOLS:
            wdf = indicators.add_all(database.load_candles(DB, s, interval, limit=100))
            if wdf.empty:
                st.markdown(
                    f'<div style="font-family:Consolas;font-size:12px;color:#5f6b76;">'
                    f'{s} — no data yet</div>',
                    unsafe_allow_html=True,
                )
                continue
            last = wdf["close"].iloc[-1]
            prev = wdf["close"].iloc[-2] if len(wdf) > 1 else last
            chg = (last - prev) / prev * 100
            rsi_val = wdf["rsi"].iloc[-1]
            rsi_color = pct_color(50 - rsi_val) if not pd.isna(rsi_val) else "#8b98a5"
            selected = "▶" if s == symbol else " "
            st.markdown(
                f'<div style="font-family:Consolas;font-size:12px;margin-bottom:6px;">'
                f'{selected} <b>{s}</b>  {format_price(last)} '
                f'<span style="color:{pct_color(chg)}">{chg:+.2f}%</span> · RSI '
                f'<span style="color:{rsi_color}">'
                f'{"-" if pd.isna(rsi_val) else f"{rsi_val:.0f}"}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("---")

        # Fear & Greed index
        try:
            fg = requests.get(config.FEAR_GREED_API, timeout=10).json()
            val = int(fg["data"][0]["value"])
            cls = "badge-bull" if val >= 50 else "badge-bear"
            st.markdown(
                f'<div class="term-sub">FEAR & GREED</div>'
                f'<div style="font-family:Consolas;font-size:22px;">'
                f'<span class="{cls}">{val}</span></div>',
                unsafe_allow_html=True,
            )
        except Exception:
            pass

    # ================== CENTER: price chart ===========================
    with col_main:
        if df.empty:
            st.warning("No candles yet — run `python collector.py backfill` first.")
            return

        df["ts"] = pd.to_datetime(df["open_time"], unit="ms")

        n_rows = 4 if show_indicators else 2
        row_heights = [0.52, 0.12, 0.18, 0.18] if show_indicators else [0.85, 0.15]
        titles = ["", "VOL", "RSI", "MACD"] if show_indicators else ["", "VOL"]

        fig = sp.make_subplots(
            rows=n_rows,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.02,
            row_heights=row_heights,
            subplot_titles=titles,
        )

        fig.add_trace(
            go.Candlestick(
                x=df["ts"], open=df["open"], high=df["high"],
                low=df["low"], close=df["close"], name="price",
                increasing_line_color="#00d9a6", increasing_fillcolor="#00d9a6",
                decreasing_line_color="#ff6b6b", decreasing_fillcolor="#ff6b6b",
            ),
            row=1, col=1,
        )

        if show_sma:
            fig.add_trace(go.Scatter(x=df["ts"], y=df["sma20"], name="SMA20",
                                     line=dict(color="#ffd23f", width=1)), row=1, col=1)
            fig.add_trace(go.Scatter(x=df["ts"], y=df["sma50"], name="SMA50",
                                     line=dict(color="#b39df2", width=1)), row=1, col=1)
        if show_bb:
            fig.add_trace(go.Scatter(x=df["ts"], y=df["bb_up"], name="BB+",
                                     line=dict(color="#5f6b76", width=1, dash="dot")),
                          row=1, col=1)
            fig.add_trace(go.Scatter(x=df["ts"], y=df["bb_low"], name="BB-",
                                     line=dict(color="#5f6b76", width=1, dash="dot")),
                          row=1, col=1)

        vol_colors = [
            "#00d9a6" if c >= o else "#ff6b6b"
            for o, c in zip(df["open"], df["close"])
        ]
        fig.add_trace(go.Bar(x=df["ts"], y=df["volume"], name="volume",
                             marker_color=vol_colors, opacity=0.35), row=2, col=1)

        if show_indicators:
            fig.add_trace(go.Scatter(x=df["ts"], y=df["rsi"], name="RSI",
                                     line=dict(color="#ffd23f", width=1)), row=3, col=1)
            fig.add_hline(y=70, line_color="#ff6b6b", line_dash="dot", row=3, col=1)
            fig.add_hline(y=30, line_color="#00d9a6", line_dash="dot", row=3, col=1)
            fig.add_trace(go.Bar(x=df["ts"], y=df["macd_hist"], name="MACD hist",
                                 marker_color="#5f6b76"), row=4, col=1)
            fig.add_trace(go.Scatter(x=df["ts"], y=df["macd"], name="MACD",
                                     line=dict(color="#00d9a6", width=1)), row=4, col=1)
            fig.add_trace(go.Scatter(x=df["ts"], y=df["macd_signal"], name="SIG",
                                     line=dict(color="#ff6b6b", width=1)), row=4, col=1)

        fig.update_layout(
            height=760,
            xaxis_rangeslider_visible=False,
            template="plotly_dark",
            paper_bgcolor="#0a0e12",
            plot_bgcolor="#0a0e12",
            margin=dict(l=10, r=10, t=10, b=10),
            showlegend=False,
            font=dict(family="Consolas", size=11),
        )
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(showgrid=False)
        st.plotly_chart(fig, use_container_width=True)

        # Metrics strip
        last = df["close"].iloc[-1]
        prev = df["close"].iloc[-2] if len(df) > 1 else last
        chg = (last - prev) / prev * 100
        hi, lo = df["high"].max(), df["low"].min()
        rsi_now = df["rsi"].iloc[-1]
        atr_now = df["atr"].iloc[-1]
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Last", format_price(last), f"{chg:+.2f}%")
        m2.metric("Range (shown)", f"{format_price(lo)} – {format_price(hi)}")
        m3.metric("RSI(14)", "-" if pd.isna(rsi_now) else f"{rsi_now:.1f}")
        m4.metric("ATR(14)", "-" if pd.isna(atr_now) else format_price(atr_now))
        m5.metric("Volume (last bar)", format_price(df["volume"].iloc[-1]))
        m6.metric("Bars", f"{len(df)}")

    # ================== RIGHT: news terminal ===========================
    with col_news:
        st.markdown(
            f'<div class="term-sub">NEWS TERMINAL · filter={news_filter} · '
            f'review={"all coins" if review_symbol == "all" else review_symbol}</div>',
            unsafe_allow_html=True,
        )
        if news_df.empty:
            st.markdown(
                '<div class="news-item">No stories yet — run `python news.py fetch`.</div>',
                unsafe_allow_html=True,
            )
        else:
            for _, row in news_df.iterrows():
                badge = sentiment_badge(row["sentiment"])
                src = row["source"] or "?"
                age = age_label(row["published_at"])
                syms = (
                    f" · <span class='badge-imp'>{row['symbols']}</span>"
                    if row["symbols"] else ""
                )
                st.markdown(
                    f'<div class="news-item">{badge} '
                    f'<a href="{row["url"]}" target="_blank" '
                    f'style="color:#d8e0e8;text-decoration:none;">{row["title"]}</a>'
                    f'<div class="news-src">{src} · {age} ago{syms}</div></div>',
                    unsafe_allow_html=True,
                )


render()
