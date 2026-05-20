"""
app.py — Streamlit P&L Dashboard
──────────────────────────────────
Run locally:   streamlit run src/dashboard/app.py
Deploy:        Add as second Railway service pointing to this file

Shows:
  - Live equity curve
  - P&L by hour / day / week / month
  - Trade history table (+ / - highlighted)
  - Best & worst trades
  - Grid state indicator
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit_authenticator as stauth
import yaml
import secrets
from yaml import SafeLoader
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.journal.trade_journal import TradeJournal


# ── Page Config ────────────────────────────────────────────────────

st.set_page_config(
    page_title="Grid Bot Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Authentication ─────────────────────────────────────────────────

def _check_auth():
    username = os.environ.get("DASHBOARD_USERNAME")
    password_hash = os.environ.get("DASHBOARD_PASSWORD_HASH")

    if not username or not password_hash:
        st.warning("Dashboard authentication not configured. Set DASHBOARD_USERNAME and DASHBOARD_PASSWORD_HASH environment variables.")
        st.stop()

    credentials = {
        "usernames": {
            username: {
                "name": username,
                "password": password_hash,
            }
        }
    }
    cookie = {
        "name": "grid_bot_dashboard",
        "key": os.environ.get("COOKIE_SECRET") or secrets.token_hex(32),
        "expiry_days": 30,
    }

    # Warn if using random cookie secret
    if not os.environ.get("COOKIE_SECRET"):
        logger.warning("COOKIE_SECRET not set — using random value. Sessions will not survive restarts.")

    authenticator = stauth.Authenticate(
        credentials=credentials,
        cookie_name=cookie["name"],
        cookie_key=cookie["key"],
        cookie_expiry_days=cookie["expiry_days"],
    )

    authenticator.login(location="main")

    if st.session_state.get("authentication_status") is not True:
        if st.session_state.get("authentication_status") is False:
            st.error("Username or password is incorrect")
        elif st.session_state.get("authentication_status") is None:
            st.info("Please enter your credentials")
        st.stop()

    return authenticator

authenticator = _check_auth()

with st.sidebar:
    st.write(f"Logged in as **{st.session_state['username']}**")
    if authenticator.logout("Logout", location="sidebar"):
        st.rerun()

    # ── Trading Pair Selector ────────────────────────────────────────
    st.markdown("---")

    # Get all unique pairs from the journal
    all_trades = journal.get_trades()
    unique_pairs = list(set([t["pair"] for t in all_trades])) if all_trades else ["BTC/USDT"]
    unique_pairs.sort()

    selected_pair = st.selectbox(
        "Trading Pair",
        options=unique_pairs,
        index=0,
        key="selected_pair"
    )

    # ── Portfolio Overview ────────────────────────────────────────────
    st.markdown("### Portfolio")

    if all_trades:
        # Show latest price for each pair
        for pair in unique_pairs:
            pair_trades = [t for t in all_trades if t["pair"] == pair]
            if pair_trades:
                latest_trade = pair_trades[0]  # Already sorted by timestamp DESC
                latest_price = latest_trade.get("exit_price", 0)
                st.metric(pair, f"${latest_price:.4f}")
    else:
        st.caption("No trade data yet")

# ── Dark Theme CSS ─────────────────────────────────────────────────

st.markdown("""
<style>
    body, .stApp { background-color: #0d1117; color: #e6edf3; }
    .metric-card {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 16px 20px;
        text-align: center;
    }
    .metric-label { font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 1px; }
    .metric-value { font-size: 28px; font-weight: 700; margin-top: 4px; }
    .positive { color: #3fb950; }
    .negative { color: #f85149; }
    .neutral  { color: #e6edf3; }
    .section-title { font-size: 16px; font-weight: 600; color: #8b949e;
                     text-transform: uppercase; letter-spacing: 1.5px;
                     margin: 24px 0 12px; border-bottom: 1px solid #30363d; padding-bottom: 8px; }
    div[data-testid="stDataFrame"] { border: 1px solid #30363d; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)


# ── Load Data ──────────────────────────────────────────────────────

@st.cache_resource
def get_journal():
    return TradeJournal()

journal = get_journal()


def color_pnl(val):
    if isinstance(val, (int, float)):
        color = "#3fb950" if val > 0 else "#f85149" if val < 0 else "#e6edf3"
        return f"color: {color}; font-weight: 600"
    return ""


def fmt_pnl(val):
    if isinstance(val, (int, float)):
        sign = "+" if val > 0 else ""
        return f"{sign}${val:.2f}"
    return val


# ── Mode Indicator ──────────────────────────────────────────────────

_env_mode = os.environ.get("ENV", "paper").lower()
_is_live = _env_mode == "live"
_mode_label = "LIVE TRADING" if _is_live else "PAPER TRADING"
_mode_emoji = "🔴" if _is_live else "🟡"
_mode_color = "#f85149" if _is_live else "#d29922"
_mode_border = "#da3633" if _is_live else "#9e6a03"

# ── Header ─────────────────────────────────────────────────────────

col_title, col_mode, col_refresh = st.columns([4, 1.5, 1])
with col_title:
    st.markdown(f"## 🤖 {selected_pair} Grid Bot — P&L Dashboard")
    st.caption(f"Last refreshed: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
with col_mode:
    st.markdown(
        f'<div style="text-align:center; margin-top:24px; padding:8px 16px; '
        f'border:2px solid {_mode_color}; border-radius:8px; background:rgba(0,0,0,0.3);">'
        f'<span style="font-size:20px;">{_mode_emoji}</span> '
        f'<span style="font-size:16px; font-weight:700; color:{_mode_color};">{_mode_label}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
with col_refresh:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ── Summary Cards ──────────────────────────────────────────────────

st.markdown('<div class="section-title">Performance Summary</div>', unsafe_allow_html=True)

# Get all trades and filter by selected pair
all_trades = journal.get_trades()
pair_trades = [t for t in all_trades if t["pair"] == selected_pair] if selected_pair != "All" else all_trades

# Helper to calculate summary from filtered trades
def calculate_summary(trades_list):
    if not trades_list:
        return {
            "total_trades": 0,
            "winning": 0,
            "losing": 0,
            "gross_pnl": 0,
            "total_fees": 0,
            "net_pnl": 0,
            "best_trade": 0,
            "worst_trade": 0,
            "avg_pnl": 0,
            "win_rate": 0
        }

    total = len(trades_list)
    winning = sum(1 for t in trades_list if t["net_pnl"] > 0)
    losing = sum(1 for t in trades_list if t["net_pnl"] < 0)
    gross_pnl = sum(t["gross_pnl"] for t in trades_list)
    total_fees = sum(t["fee"] for t in trades_list)
    net_pnl = sum(t["net_pnl"] for t in trades_list)
    best_trade = max((t["net_pnl"] for t in trades_list), default=0)
    worst_trade = min((t["net_pnl"] for t in trades_list), default=0)
    avg_pnl = net_pnl / total if total > 0 else 0
    win_rate = round((winning / total * 100), 1) if total > 0 else 0

    return {
        "total_trades": total,
        "winning": winning,
        "losing": losing,
        "gross_pnl": gross_pnl,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "avg_pnl": avg_pnl,
        "win_rate": win_rate
    }

def filter_trades_by_period(trades_list, period_start):
    return [t for t in trades_list if t["timestamp"] >= period_start]

# Calculate summaries for different periods
now = datetime.now(timezone.utc)
today_start = now.strftime("%Y-%m-%d 00:00:00")
week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
month_start = now.strftime("%Y-%m-%d 01 00:00:00")

today_trades = filter_trades_by_period(pair_trades, today_start)
week_trades = filter_trades_by_period(pair_trades, week_start)
month_trades = filter_trades_by_period(pair_trades, month_start)

today  = calculate_summary(today_trades)
week   = calculate_summary(week_trades)
month  = calculate_summary(month_trades)
alltime = calculate_summary(pair_trades)

def metric_card(label, value, is_pnl=True):
    if is_pnl:
        val_f = fmt_pnl(value or 0)
        cls = "positive" if (value or 0) > 0 else "negative" if (value or 0) < 0 else "neutral"
    else:
        val_f = str(value or 0)
        cls = "neutral"
    return f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value {cls}">{val_f}</div>
    </div>"""

c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
cards = [
    (c1, "Today Net PnL",   today["net_pnl"]),
    (c2, "This Week",       week["net_pnl"]),
    (c3, "This Month",      month["net_pnl"]),
    (c4, "All-Time Net",    alltime["net_pnl"]),
    (c5, "Total Trades",    alltime["total_trades"], False),
    (c6, "Win Rate",        f"{today['win_rate']}%", False),
    (c7, "Mode",            _mode_label, False),
]
for item in cards:
    col = item[0]
    label, value = item[1], item[2]
    is_pnl = item[3] if len(item) > 3 else True
    with col:
        if label == "Mode":
            st.markdown(f"""
            <div class="metric-card" style="border-color:{_mode_color};">
                <div class="metric-label">{label}</div>
                <div class="metric-value" style="color:{_mode_color};">{_mode_emoji} {value}</div>
            </div>""", unsafe_allow_html=True)
        elif is_pnl:
            st.markdown(metric_card(label, value, True), unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">{label}</div>
                <div class="metric-value neutral">{value}</div>
            </div>""", unsafe_allow_html=True)


# ── Equity Curve ───────────────────────────────────────────────────

st.markdown('<div class="section-title">Equity Curve</div>', unsafe_allow_html=True)

col_chart, col_period = st.columns([4, 1])
with col_period:
    days = st.selectbox("Period", [7, 14, 30, 60, 90], index=2, label_visibility="collapsed")

# Calculate equity curve from filtered trades
since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
period_trades = [t for t in pair_trades if t["timestamp"] >= since]

if period_trades:
    # Group by date and calculate daily PnL
    from collections import defaultdict
    daily_pnl = defaultdict(float)
    for t in period_trades:
        date = t["timestamp"][:10]  # Extract date part
        daily_pnl[date] += t["net_pnl"]

    # Sort dates and calculate cumulative PnL
    sorted_dates = sorted(daily_pnl.keys())
    cumulative = 0
    equity = []
    for date in sorted_dates:
        cumulative += daily_pnl[date]
        equity.append({
            "date": date,
            "daily_pnl": daily_pnl[date],
            "cumulative_pnl": cumulative
        })
else:
    equity = []

if equity:
    df_eq = pd.DataFrame(equity)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_eq["date"],
        y=df_eq["cumulative_pnl"],
        mode="lines",
        fill="tozeroy",
        line=dict(color="#3fb950", width=2),
        fillcolor="rgba(63, 185, 80, 0.1)",
        name="Cumulative PnL",
        hovertemplate="<b>%{x}</b><br>PnL: +$%{y:.2f}<extra></extra>",
    ))
    fig.add_bar(
        x=df_eq["date"],
        y=df_eq["daily_pnl"],
        name="Daily PnL",
        marker_color=["#3fb950" if v >= 0 else "#f85149" for v in df_eq["daily_pnl"]],
        opacity=0.6,
    )
    fig.update_layout(
        paper_bgcolor="#161b22",
        plot_bgcolor="#161b22",
        font=dict(color="#8b949e"),
        xaxis=dict(gridcolor="#21262d", showgrid=True),
        yaxis=dict(gridcolor="#21262d", showgrid=True, tickprefix="$"),
        legend=dict(bgcolor="#161b22", bordercolor="#30363d"),
        margin=dict(l=0, r=0, t=10, b=0),
        height=280,
    )
    with col_chart:
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No trade data yet. Start paper trading to see your equity curve.")


# ── Trade History Table ────────────────────────────────────────────

st.markdown('<div class="section-title">Trade History</div>', unsafe_allow_html=True)

col_filter1, col_filter2, col_filter3 = st.columns([2, 2, 4])
with col_filter1:
    side_filter = st.selectbox("Side", ["All", "BUY", "SELL"])
with col_filter2:
    result_filter = st.selectbox("Result", ["All", "Profitable ✅", "Loss ❌"])

trades = journal.get_trades()

# Filter by selected pair
if selected_pair != "All":
    trades = [t for t in trades if t["pair"] == selected_pair]

if trades:
    df = pd.DataFrame(trades)

    # Apply filters
    if side_filter != "All":
        df = df[df["side"] == side_filter]
    if result_filter == "Profitable ✅":
        df = df[df["net_pnl"] > 0]
    elif result_filter == "Loss ❌":
        df = df[df["net_pnl"] < 0]

    # Format display columns
    display = df[[
        "timestamp", "pair", "side", "entry_price", "exit_price",
        "quantity", "gross_pnl", "fee", "net_pnl",
        "grid_level", "duration_min", "rsi", "grid_state"
    ]].copy()

    display.columns = [
        "Time", "Pair", "Side", "Entry $", "Exit $",
        "Qty BTC", "Gross PnL", "Fee", "Net PnL",
        "Level", "Min", "RSI", "State"
    ]

    styled = display.style \
        .map(color_pnl, subset=["Net PnL", "Gross PnL"]) \
        .format({
            "Entry $":   "${:.2f}",
            "Exit $":    "${:.2f}",
            "Qty BTC":   "{:.5f}",
            "Gross PnL": lambda v: fmt_pnl(v),
            "Fee":       "-${:.4f}",
            "Net PnL":   lambda v: fmt_pnl(v),
            "RSI":       "{:.1f}",
        })

    st.dataframe(styled, use_container_width=True, height=400)

    col_count, col_csv = st.columns([5, 1])
    with col_count:
        st.caption(f"Showing {len(display)} trades")
    with col_csv:
        csv_data = df.to_csv(index=False).encode("utf-8")
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        st.download_button(
            "📥 Download CSV",
            data=csv_data,
            file_name=f"trades_export_{today_str}.csv",
            mime="text/csv",
            use_container_width=True,
        )
else:
    st.info("No trades logged yet.")


# ── Best & Worst Trades ────────────────────────────────────────────

st.markdown('<div class="section-title">Best & Worst Trades</div>', unsafe_allow_html=True)

# Get best and worst trades from filtered data
sorted_by_pnl = sorted(pair_trades, key=lambda x: x["net_pnl"], reverse=True)
best_trades = sorted_by_pnl[:5]
worst_trades = sorted_by_pnl[-5:] if len(sorted_by_pnl) >= 5 else sorted_by_pnl
worst_trades.reverse()  # Show worst first

bw = {"best": best_trades, "worst": worst_trades}
col_best, col_worst = st.columns(2)

def render_trade_list(trades_list, title, emoji):
    st.markdown(f"**{emoji} {title}**")
    if trades_list:
        for t in trades_list:
            sign = "+" if t["net_pnl"] > 0 else ""
            cls = "positive" if t["net_pnl"] > 0 else "negative"
            st.markdown(
                f'<span style="color:#8b949e">{t["timestamp"][:16]}</span> '
                f'| {t["side"]} @ ${t["exit_price"]:,.0f} '
                f'| <span class="{cls}" style="font-weight:600">{sign}${t["net_pnl"]:.2f}</span>',
                unsafe_allow_html=True
            )
    else:
        st.caption("No data yet")

with col_best:
    render_trade_list(bw["best"], "Top 5 Best Trades", "🏆")
with col_worst:
    render_trade_list(bw["worst"], "Top 5 Worst Trades", "💔")


# ── Period Breakdown ───────────────────────────────────────────────

st.markdown('<div class="section-title">Period Breakdown</div>', unsafe_allow_html=True)

hour_start = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

periods = {
    "⏰ This Hour":  calculate_summary(filter_trades_by_period(pair_trades, hour_start)),
    "📅 Today":      today,
    "📆 This Week":  week,
    "🗓 This Month": month,
    "🏦 All Time":   alltime,
}

period_data = []
for label, s in periods.items():
    period_data.append({
        "Period":       label,
        "Trades":       s["total_trades"] or 0,
        "Win Rate":     f"{s['win_rate']}%",
        "Gross PnL":    s["gross_pnl"] or 0,
        "Fees":         abs(s["total_fees"] or 0),
        "Net PnL":      s["net_pnl"] or 0,
        "Best Trade":   s["best_trade"] or 0,
        "Worst Trade":  s["worst_trade"] or 0,
    })

df_periods = pd.DataFrame(period_data)
styled_p = df_periods.style \
    .map(color_pnl, subset=["Net PnL", "Gross PnL", "Best Trade", "Worst Trade"]) \
    .format({
        "Gross PnL":   lambda v: fmt_pnl(v),
        "Fees":        "${:.2f}",
        "Net PnL":     lambda v: fmt_pnl(v),
        "Best Trade":  lambda v: fmt_pnl(v),
        "Worst Trade": lambda v: fmt_pnl(v),
    })

st.dataframe(styled_p, use_container_width=True, hide_index=True)

st.markdown("---")
st.caption(f"TA-Enhanced {selected_pair} Grid Bot · {_mode_label} · Hummingbot v2 · Binance FZE · Dubai, UAE")
