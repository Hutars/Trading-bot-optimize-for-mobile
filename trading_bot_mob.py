import ccxt
import pandas as pd
import ta
import time
import logging
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
from datetime import datetime, timezone, timedelta

# ── Timezone UTC+3 ──────────────────────────────────────────
def local_time():
    tz = timezone(timedelta(hours=3))
    return datetime.now(tz).strftime('%H:%M:%S')

logging.basicConfig(filename='paper_trades.log', level=logging.INFO, format='%(asctime)s - %(message)s')

@st.cache_resource
def get_exchange():
    return ccxt.binance({'enableRateLimit': True})

exchange = get_exchange()

symbol = 'ETH/USDT'
PORTFOLIO_FILE = 'paper_portfolio.json'

TIMEFRAME_COOLDOWN = {'1m': 60, '3m': 180, '5m': 300, '15m': 900, '1h': 3600}
MAX_LOG_ENTRIES = 200

# ════════════════════════════════════════════════════════════
# CSS — засича ширината на браузъра и прилага различен стил
# ════════════════════════════════════════════════════════════
RESPONSIVE_CSS = """
<style>
/* Лог стил — общ */
.log-entry {
    font-size: 0.78rem;
    padding: 4px 6px;
    margin-bottom: 4px;
    border-left: 3px solid #444;
    background: rgba(255,255,255,0.04);
    border-radius: 4px;
    line-height: 1.4;
    word-break: break-word;
}

/* ── МОБИЛЕН стил (екран < 768px) ── */
@media (max-width: 768px) {
    div.stButton > button {
        height: 3.2rem !important;
        font-size: 1rem !important;
        font-weight: 600 !important;
        border-radius: 10px !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.1rem !important;
        font-weight: 700 !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.68rem !important;
    }
    .stNumberInput input, .stSelectbox select {
        font-size: 1rem !important;
        height: 2.8rem !important;
    }
    .stCheckbox label {
        font-size: 1rem !important;
    }
    h1 {
        font-size: 1.3rem !important;
        margin-bottom: 0.2rem !important;
    }
    .stPlotlyChart {
        margin-top: -10px;
    }
}
</style>

<script>
// Засича ширината на прозореца и записва в sessionStorage
// Streamlit я чете чрез query_params при следващ rerun
(function() {
    const w = window.innerWidth;
    const isMobile = w < 768;
    const current = new URLSearchParams(window.location.search).get('mobile');
    const should  = isMobile ? '1' : '0';
    if (current !== should) {
        const url = new URL(window.location.href);
        url.searchParams.set('mobile', should);
        window.history.replaceState({}, '', url);
        // Лек timeout преди rerun за да се запише URL-а
        setTimeout(() => window.location.reload(), 50);
    }
})();
</script>
"""

# ── Portfolio I/O ────────────────────────────────────────────
def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, 'r') as f:
                data = json.load(f)
                if 'paper_usdt' in data:
                    data.setdefault('total_profit_usdt', 0.0)
                    data.setdefault('total_loss_usdt', 0.0)
                    data.setdefault('winning_trades', 0)
                    data.setdefault('losing_trades', 0)
                    data.setdefault('bot_active', True)
                    data.setdefault('timeframe', '5m')
                    data.setdefault('rsi_buy_level', 36)
                    data.setdefault('rsi_sell_level', 65)
                    data.setdefault('trailing_input', 2.0)
                    data.setdefault('auto_buy_amount_eth', 0.5)
                    data.setdefault('logs', [])
                    return data
        except Exception:
            pass
    return {
        "paper_usdt": 15000.0, "paper_eth": 1.0, "highest_price": 0.0,
        "avg_buy_price": 2000.0, "total_profit_usdt": 0.0, "total_loss_usdt": 0.0,
        "winning_trades": 0, "losing_trades": 0, "bot_active": True,
        "timeframe": "5m", "rsi_buy_level": 36, "rsi_sell_level": 65,
        "trailing_input": 2.0, "auto_buy_amount_eth": 0.5, "logs": []
    }

def save_portfolio():
    data = {
        "paper_usdt": st.session_state.paper_usdt,
        "paper_eth": st.session_state.paper_eth,
        "highest_price": st.session_state.highest_price,
        "avg_buy_price": st.session_state.avg_buy_price,
        "total_profit_usdt": st.session_state.total_profit_usdt,
        "total_loss_usdt": st.session_state.total_loss_usdt,
        "winning_trades": st.session_state.winning_trades,
        "losing_trades": st.session_state.losing_trades,
        "bot_active": st.session_state.get('bot_active_state', True),
        "timeframe": st.session_state.get('timeframe_state', '5m'),
        "rsi_buy_level": st.session_state.get('rsi_buy_state', 36),
        "rsi_sell_level": st.session_state.get('rsi_sell_state', 65),
        "trailing_input": st.session_state.get('trailing_state', 2.0),
        "auto_buy_amount_eth": st.session_state.get('auto_amount_state', 0.5),
        "logs": st.session_state.logs[-MAX_LOG_ENTRIES:]
    }
    with open(PORTFOLIO_FILE, 'w') as f:
        json.dump(data, f)

# ── Data fetching ────────────────────────────────────────────
def fetch_data(symbol, timeframe):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=300)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms') + pd.Timedelta(hours=3)
    return df.tail(100).reset_index(drop=True)

def fetch_ticker():
    return exchange.fetch_ticker(symbol)['last']

# ════════════════════════════════════════════════════════════
# FRAGMENT 1 — Метрики (10 сек)
# ════════════════════════════════════════════════════════════
@st.fragment(run_every=10)
def live_price_widget(is_mobile):
    try:
        price = fetch_ticker()
        st.session_state.live_price = price
    except Exception:
        st.session_state.live_price = None

    timeframe  = st.session_state.get('timeframe_state', '5m')
    cached_rsi = st.session_state.get('last_rsi', None)
    live_price = st.session_state.get('live_price', None)

    rsi_str   = f"{cached_rsi:.2f}" if cached_rsi is not None else "—"
    price_str = f"${live_price:,.2f}" if live_price else "—"

    total_trades = st.session_state.get('winning_trades', 0) + st.session_state.get('losing_trades', 0)
    win_rate = (st.session_state.get('winning_trades', 0) / total_trades * 100) if total_trades > 0 else 0.0
    loss   = st.session_state.get('total_loss_usdt', 0.0)
    profit = st.session_state.get('total_profit_usdt', 0.0)
    pf_str = f"{profit/loss:.2f}" if loss > 0 else ("∞" if profit > 0 else "N/A")

    usdt_str = f"${st.session_state.get('paper_usdt', 0):,.2f}"
    eth_str  = f"{st.session_state.get('paper_eth', 0):.4f}"

    if is_mobile:
        # 2 реда × 3 колони — по-четимо на тесен екран
        r1c1, r1c2, r1c3 = st.columns(3)
        r2c1, r2c2, r2c3 = st.columns(3)
        r1c1.metric("⚡ Цена ETH",           price_str)
        r1c2.metric(f"📊 RSI ({timeframe})", rsi_str)
        r1c3.metric("💰 USDT",               f"${st.session_state.get('paper_usdt', 0):,.0f}")
        r2c1.metric("🪙 ETH",                eth_str)
        r2c2.metric("📈 Win Rate",            f"{win_rate:.1f}%", help=f"Сделки: {total_trades}")
        r2c3.metric("📊 Profit Factor",       pf_str)
    else:
        # 1 ред × 6 колони — като оригинала на компютър
        c1, c2, c3, c4, c5, c6 = st.columns([1, 1, 1.3, 1.2, 1, 1])
        c1.metric("⚡ Цена ETH",              price_str)
        c2.metric(f"📊 RSI ({timeframe})",    rsi_str)
        c3.metric("💰 Виртуален Портфейл",   usdt_str)
        c4.metric("🪙 Наличен ETH",           eth_str)
        c5.metric("📈 Win Rate",              f"{win_rate:.1f}%", help=f"Сделки: {total_trades}")
        c6.metric("📊 Profit Factor",         pf_str)

# ════════════════════════════════════════════════════════════
# FRAGMENT 2 — Графика + логика (30 сек)
# ════════════════════════════════════════════════════════════
@st.fragment(run_every=30)
def main_dashboard(is_mobile):
    try:
        timeframe           = st.session_state.get('timeframe_state', '5m')
        rsi_buy_level       = st.session_state.get('rsi_buy_state', 36)
        rsi_sell_level      = st.session_state.get('rsi_sell_state', 65)
        trailing_input      = st.session_state.get('trailing_state', 2.0)
        trailing_pct        = trailing_input / 100.0
        auto_buy_amount_eth = st.session_state.get('auto_amount_state', 0.5)
        bot_active          = st.session_state.get('bot_active_state', True)
        wait_time           = TIMEFRAME_COOLDOWN.get(timeframe, 300)

        df            = fetch_data(symbol, timeframe)
        current_price = df['close'].iloc[-1]
        current_rsi   = df['rsi'].iloc[-1]
        current_time  = time.time()

        st.session_state.last_rsi = current_rsi

        # ── Trailing stop ──────────────────────────────────
        if st.session_state.paper_eth > 0.001 and st.session_state.avg_buy_price > 0:
            if st.session_state.highest_price < st.session_state.avg_buy_price:
                st.session_state.highest_price = st.session_state.avg_buy_price
            if current_price > st.session_state.highest_price:
                st.session_state.highest_price = current_price

            stop_level    = st.session_state.highest_price * (1 - trailing_pct)
            unrealized_pl = ((current_price - st.session_state.avg_buy_price) / st.session_state.avg_buy_price) * 100

            st.session_state.risk_avg_price      = st.session_state.avg_buy_price
            st.session_state.risk_unrealized_pl  = unrealized_pl
            st.session_state.risk_stop_level     = stop_level
            st.session_state.risk_trailing_input = trailing_input

            if current_price <= stop_level:
                pnl = st.session_state.paper_eth * (current_price - st.session_state.avg_buy_price)
                if pnl >= 0:
                    st.session_state.total_profit_usdt += pnl
                    st.session_state.winning_trades += 1
                else:
                    st.session_state.total_loss_usdt += abs(pnl)
                    st.session_state.losing_trades += 1
                st.session_state.paper_usdt += st.session_state.paper_eth * current_price
                msg = f"[{local_time()}] [TRAILING STOP] | {st.session_state.paper_eth:.4f} ETH на ${current_price:.2f} (P/L: {unrealized_pl:+.2f}%) | RSI: {current_rsi:.2f}"
                st.session_state.logs.append(msg)
                logging.info(msg)
                st.session_state.paper_eth     = 0
                st.session_state.highest_price = 0
                st.session_state.avg_buy_price = 0
                save_portfolio()
                st.rerun()

        # ── Авто логика ────────────────────────────────────
        st.session_state.bot_is_stopped = False
        if bot_active:
            if (current_rsi <= rsi_buy_level and
                    (current_time - st.session_state.last_trade_time) > wait_time):
                cost = auto_buy_amount_eth * current_price
                if st.session_state.paper_usdt >= cost:
                    new_eth = st.session_state.paper_eth + auto_buy_amount_eth
                    st.session_state.avg_buy_price = (
                        (st.session_state.paper_eth * st.session_state.avg_buy_price) +
                        (auto_buy_amount_eth * current_price)
                    ) / new_eth
                    st.session_state.paper_usdt     -= cost
                    st.session_state.paper_eth       = new_eth
                    st.session_state.last_trade_time = current_time
                    st.session_state.highest_price   = max(st.session_state.highest_price, current_price)
                    msg = f"[{local_time()}] [АВТО КУПУВА] | {auto_buy_amount_eth} ETH | ${current_price:.2f} | RSI: {current_rsi:.2f}"
                    st.session_state.logs.append(msg)
                    logging.info(msg)
                    save_portfolio()
                    st.rerun()

            elif (current_rsi >= rsi_sell_level and
                  st.session_state.paper_eth >= 0.1 and
                  (current_time - st.session_state.last_trade_time) > wait_time):
                sell_amount = min(auto_buy_amount_eth, st.session_state.paper_eth)
                pnl = sell_amount * (current_price - st.session_state.avg_buy_price)
                if pnl >= 0:
                    st.session_state.total_profit_usdt += pnl
                    st.session_state.winning_trades += 1
                else:
                    st.session_state.total_loss_usdt += abs(pnl)
                    st.session_state.losing_trades += 1
                st.session_state.paper_usdt += sell_amount * current_price
                st.session_state.paper_eth  -= sell_amount
                st.session_state.last_trade_time = current_time
                unrealized_pl = ((current_price - st.session_state.avg_buy_price) / st.session_state.avg_buy_price) * 100
                msg = f"[{local_time()}] [ЧАСТИЧНА ПРОДАЖБА] | {sell_amount} ETH | ${current_price:.2f} | P/L: {unrealized_pl:+.2f}% | RSI: {current_rsi:.2f}"
                st.session_state.logs.append(msg)
                logging.info(msg)
                if st.session_state.paper_eth < 0.001:
                    st.session_state.avg_buy_price = 0
                    st.session_state.highest_price = 0
                save_portfolio()
                st.rerun()
        else:
            st.session_state.bot_is_stopped = True

        # ── Графика ────────────────────────────────────────
        if is_mobile:
            # На мобилен — toggle за свещници, по-ниска графика
            show_candles = st.toggle("📈 Свещникова графика", value=False)
            chart_height = 380 if show_candles else 240
        else:
            # На компютър — винаги двоен панел, по-висока графика
            show_candles = True
            chart_height = 650

        if show_candles:
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                vertical_spacing=0.06, row_heights=[0.6, 0.4])
            fig.add_trace(go.Candlestick(
                x=df['timestamp'], open=df['open'], high=df['high'],
                low=df['low'], close=df['close'], name="Цена"), row=1, col=1)
            rsi_row = 2
        else:
            fig = make_subplots(rows=1, cols=1)
            rsi_row = 1

        fig.add_trace(go.Scatter(
            x=df['timestamp'], y=df['rsi'], mode='lines',
            name='RSI', line=dict(color='orange', width=2)), row=rsi_row, col=1)
        fig.add_hline(y=rsi_sell_level, line_dash="dash", line_color="red",
                      annotation_text=f"Продажби ({rsi_sell_level})", row=rsi_row, col=1)
        fig.add_hline(y=rsi_buy_level, line_dash="dash", line_color="green",
                      annotation_text=f"Покупки ({rsi_buy_level})", row=rsi_row, col=1)
        if show_candles:
            fig.add_hline(y=current_price, line_dash="dot", line_color="cyan", row=1, col=1)

        right_margin = 70 if is_mobile else 120
        fig.update_layout(
            title=f"ETH/USDT ({timeframe}) + RSI",
            xaxis_rangeslider_visible=False,
            height=chart_height,
            margin=dict(l=0, r=right_margin, t=35, b=30),
            hovermode="x unified",
            legend=dict(orientation="h", y=1.08, x=0),
            annotations=[
                dict(x=1.01, y=current_price if show_candles else current_rsi,
                     yref="y1" if show_candles else "y",
                     xref="paper",
                     text=f"👉 ${current_price:,.2f}" if show_candles else f"RSI {current_rsi:.2f}",
                     showarrow=False,
                     font=dict(size=12 if is_mobile else 14, color="cyan", family="Arial Black"),
                     xanchor="left", yanchor="middle"),
                *([ dict(x=1.01, y=current_rsi, yref="y2", xref="paper",
                         text=f"📊 {current_rsi:.2f}", showarrow=False,
                         font=dict(size=12 if is_mobile else 13, color="orange", family="Arial Black"),
                         xanchor="left", yanchor="middle") ] if show_candles else [])
            ],
        )
        fig.update_yaxes(side="right")
        if show_candles:
            fig.update_yaxes(range=[0, 100], row=2, col=1)

        # ── Лог ───────────────────────────────────────────
        if is_mobile:
            # На мобилен — лог под графиката, компактен
            st.plotly_chart(fig, use_container_width=True)
            st.markdown("#### 📜 Лог")
            log_container = st
        else:
            # На компютър — лог вдясно от графиката (оригинален layout)
            chart_col, log_col = st.columns([3, 1])
            with chart_col:
                st.plotly_chart(fig, use_container_width=True)
            log_container = log_col
            log_container.subheader("📜 Лог на сделките")

        if st.session_state.logs:
            for log in reversed(st.session_state.logs[-12:]):
                if "КУПУВА" in log:
                    color = "#2ecc71"
                elif "ПРОДАЖБА" in log or "PANIC" in log or "STOP" in log:
                    color = "#e74c3c"
                else:
                    color = "#95a5a6"
                log_container.markdown(
                    f'<div class="log-entry" style="border-left-color:{color}">{log}</div>',
                    unsafe_allow_html=True
                )
        else:
            log_container.info("Няма транзакции.")

    except Exception as e:
        st.error(f"Грешка: {e}")
        logging.error(f"СИСТЕМНА ГРЕШКА: {e}")


# ════════════════════════════════════════════════════════════
# MAIN APP
# ════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Trading Bot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="auto",   # авто: отворен на компютър, затворен на мобилен
)

st.markdown(RESPONSIVE_CSS, unsafe_allow_html=True)

# Засичане на устройството чрез query param ?mobile=1 / ?mobile=0
# JavaScript в RESPONSIVE_CSS го записва автоматично
is_mobile = st.query_params.get("mobile", "0") == "1"

# ── Парола ──────────────────────────────────────────────────
def check_password():
    if st.session_state.get("password_correct", False):
        return True
    st.title("🔒 Вход")
    pwd = st.text_input("Парола:", type="password", key="password_field")
    if st.button("Влизане", use_container_width=True):
        if pwd == "admin123":
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("❌ Грешна парола!")
    return False

if not check_password():
    st.stop()

st.title("🤖 Крипто Трейдинг Бот")

# ── Инициализация ────────────────────────────────────────────
if 'paper_usdt' not in st.session_state:
    d = load_portfolio()
    st.session_state.paper_usdt        = d["paper_usdt"]
    st.session_state.paper_eth         = d["paper_eth"]
    st.session_state.highest_price     = d["highest_price"]
    st.session_state.avg_buy_price     = d["avg_buy_price"]
    st.session_state.total_profit_usdt = d["total_profit_usdt"]
    st.session_state.total_loss_usdt   = d["total_loss_usdt"]
    st.session_state.winning_trades    = d["winning_trades"]
    st.session_state.losing_trades     = d["losing_trades"]
    st.session_state.logs              = d["logs"]
    st.session_state.bot_active_state  = d["bot_active"]
    st.session_state.timeframe_state   = d["timeframe"]
    st.session_state.rsi_buy_state     = d["rsi_buy_level"]
    st.session_state.rsi_sell_state    = d["rsi_sell_level"]
    st.session_state.trailing_state    = d["trailing_input"]
    st.session_state.auto_amount_state = d["auto_buy_amount_eth"]
    st.session_state.last_trade_time   = 0.0
    st.session_state.confirm_reset     = False
    st.session_state.last_rsi          = None
    save_portfolio()

# ════════════════════════════════════════════════════════════
# SIDEBAR — еднакъв на двете устройства, само настройки
# ════════════════════════════════════════════════════════════
st.sidebar.header("⚙️ Настройки на Бота")
st.sidebar.checkbox("🤖 Автоматичен режим (ON/OFF)", key="bot_active_state", on_change=save_portfolio)

tf_options = ['1m', '3m', '5m', '15m', '1h']
tf_idx = tf_options.index(st.session_state.timeframe_state) if st.session_state.timeframe_state in tf_options else 2
st.sidebar.selectbox("Таймфрейм:", tf_options, index=tf_idx, key="timeframe_state", on_change=save_portfolio)
st.sidebar.number_input("RSI Ниво за покупка:", min_value=10, max_value=50, key="rsi_buy_state", step=1, on_change=save_portfolio)
st.sidebar.number_input("RSI Ниво за продажба:", min_value=50, max_value=90, key="rsi_sell_state", step=1, on_change=save_portfolio)
st.sidebar.number_input("Trailing Stop Loss (%):", min_value=0.1, max_value=20.0, key="trailing_state", step=0.1, format="%.1f", on_change=save_portfolio)
st.sidebar.number_input("Автоматично количество ETH:", min_value=0.001, max_value=10.0, key="auto_amount_state", step=0.05, format="%.3f", on_change=save_portfolio)

# Риск мониторинг
if st.session_state.paper_eth > 0.001 and st.session_state.avg_buy_price > 0:
    st.sidebar.markdown("---")
    st.sidebar.subheader("🛡️ Мониторинг на риска")
    avg  = st.session_state.get('risk_avg_price', st.session_state.avg_buy_price)
    pl   = st.session_state.get('risk_unrealized_pl', 0.0)
    stop = st.session_state.get('risk_stop_level', 0.0)
    ti   = st.session_state.get('risk_trailing_input', st.session_state.get('trailing_state', 2.0))
    st.sidebar.write(f"Средна цена: **${avg:.2f}**")
    st.sidebar.write(f"Текущ P/L: **{pl:+.2f}%**")
    st.sidebar.write(f"Ниво на Стоп ({ti}%): **${stop:.2f}**")

if st.session_state.get('bot_is_stopped', False):
    st.sidebar.warning("⚠️ Автоматичният бот е СПРЯН.")

# ── Ръчно управление ─────────────────────────────────────────
# Компютър: в sidebar (оригинал)
# Мобилен:  expander в основното тяло
def render_manual_controls(container):
    manual_amount = container.number_input(
        "Количество ETH:", min_value=0.001, max_value=100.0,
        value=0.5, step=0.01, format="%.3f", key="manual_amount_input"
    )
    try:
        cp  = fetch_ticker()
        rsi = st.session_state.get('last_rsi', 0.0) or 0.0
        container.caption(f"Цена: **${cp:,.2f}** | Стойност: **${manual_amount * cp:,.2f} USDT**")

        b1, b2, b3 = container.columns(3)

        if b1.button("🟩 КУПУВА", use_container_width=True, key="btn_buy"):
            cost = manual_amount * cp
            if st.session_state.paper_usdt >= cost:
                new_eth = st.session_state.paper_eth + manual_amount
                st.session_state.avg_buy_price = (
                    (st.session_state.paper_eth * st.session_state.avg_buy_price) +
                    (manual_amount * cp)
                ) / new_eth
                st.session_state.paper_usdt   -= cost
                st.session_state.paper_eth     = new_eth
                st.session_state.highest_price = max(st.session_state.highest_price, cp)
                msg = f"[{local_time()}] 👤 МАНУАЛ КУПУВА | {manual_amount:.3f} ETH | ${cp:.2f} | RSI: {rsi:.2f}"
                st.session_state.logs.append(msg); logging.info(msg)
                save_portfolio(); st.rerun()
            else:
                container.error("Няма достатъчно USDT!")

        if b2.button("🟨 ПРОДАВА", use_container_width=True, key="btn_sell"):
            if st.session_state.paper_eth >= manual_amount:
                pnl = manual_amount * (cp - st.session_state.avg_buy_price)
                if pnl >= 0:
                    st.session_state.total_profit_usdt += pnl; st.session_state.winning_trades += 1
                else:
                    st.session_state.total_loss_usdt += abs(pnl); st.session_state.losing_trades += 1
                st.session_state.paper_usdt += manual_amount * cp
                st.session_state.paper_eth  -= manual_amount
                msg = f"[{local_time()}] 👤 МАНУАЛ ПРОДАЖБА | {manual_amount:.3f} ETH | ${cp:.2f} | RSI: {rsi:.2f}"
                st.session_state.logs.append(msg); logging.info(msg)
                if st.session_state.paper_eth < 0.001:
                    st.session_state.avg_buy_price = 0; st.session_state.highest_price = 0
                save_portfolio(); st.rerun()
            else:
                container.error("Нямате толкова ETH!")

        if b3.button("🚨 ПАНИК", use_container_width=True, type="primary", key="btn_panic"):
            if st.session_state.paper_eth > 0:
                pnl = st.session_state.paper_eth * (cp - st.session_state.avg_buy_price)
                if pnl >= 0:
                    st.session_state.total_profit_usdt += pnl; st.session_state.winning_trades += 1
                else:
                    st.session_state.total_loss_usdt += abs(pnl); st.session_state.losing_trades += 1
                st.session_state.paper_usdt   += st.session_state.paper_eth * cp
                msg = f"[{local_time()}] 🚨 PANIC SELL | {st.session_state.paper_eth:.4f} ETH на ${cp:.2f} | RSI: {rsi:.2f}"
                st.session_state.logs.append(msg); logging.info(msg)
                st.session_state.paper_eth     = 0
                st.session_state.avg_buy_price = 0
                st.session_state.highest_price = 0
                save_portfolio(); st.rerun()
    except Exception as e:
        container.error(f"Грешка: {e}")

def render_reset(container):
    if not st.session_state.get('confirm_reset', False):
        if container.button("🗑️ RESET БОТ", use_container_width=True, type="primary", key="btn_reset"):
            st.session_state.confirm_reset = True; st.rerun()
    else:
        container.warning("Сигурни ли сте? Всички данни ще бъдат изтрити!")
        cy, cn = container.columns(2)
        if cy.button("✅ Да", use_container_width=True, key="btn_reset_yes"):
            reset_data = {
                "paper_usdt": 15000.0, "paper_eth": 1.0, "highest_price": 0.0,
                "avg_buy_price": 2000.0, "total_profit_usdt": 0.0, "total_loss_usdt": 0.0,
                "winning_trades": 0, "losing_trades": 0, "bot_active": True,
                "timeframe": "5m", "rsi_buy_level": 36, "rsi_sell_level": 65,
                "trailing_input": 2.0, "auto_buy_amount_eth": 0.5,
                "logs": ["[СИСТЕМА] Нулирано."]
            }
            with open(PORTFOLIO_FILE, 'w') as f:
                json.dump(reset_data, f)
            st.toast("Портфейлът беше занулен!")
            for k in list(st.session_state.keys()):
                if k != "password_correct": del st.session_state[k]
            st.rerun()
        if cn.button("❌ Отказ", use_container_width=True, key="btn_reset_no"):
            st.session_state.confirm_reset = False; st.rerun()

if is_mobile:
    # Мобилен: бутоните в expander-и в основното тяло
    with st.expander("🕹️ Ръчно управление", expanded=False):
        render_manual_controls(st)
    with st.expander("🗑️ Нулиране", expanded=False):
        render_reset(st)
else:
    # Компютър: бутоните в sidebar (оригинален layout)
    st.sidebar.markdown("---")
    st.sidebar.header("🕹️ Ръчно управление")
    render_manual_controls(st.sidebar)
    st.sidebar.markdown("---")
    st.sidebar.subheader("🚨 Нулиране")
    render_reset(st.sidebar)

st.markdown("---")

# ── Рендиране на фрагментите ─────────────────────────────────
live_price_widget(is_mobile)
main_dashboard(is_mobile)
