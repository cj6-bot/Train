import streamlit as st
import pandas as pd
import plotly.express as px
from engine import BotEngine

st.set_page_config(page_title="Gate.io Trading Bot", layout="wide")

st.title("🚀 Gate.io Trading Bot (Streamlit UI)")

# --- API Credentials ---
st.sidebar.header("API Settings")
api_key = st.sidebar.text_input("API Key")
api_secret = st.sidebar.text_input("API Secret", type="password")
use_testnet = st.sidebar.checkbox("Use Testnet", value=True)

# --- Bot Initialization ---
if st.sidebar.button("Connect"):
    bot = BotEngine(api_key, api_secret, testnet=use_testnet)
    result = bot.connect()
    if result["ok"]:
        st.success(f"Connected! Balance: {result['balance']}")
        st.session_state["bot"] = bot
    else:
        st.error(f"Connection failed: {result['error']}")

# --- Trading Controls ---
if "bot" in st.session_state:
    bot = st.session_state["bot"]

    st.sidebar.header("Trading Controls")
    symbols = st.sidebar.text_area("Symbols (comma separated)", "BTC_USDT,ETH_USDT").split(",")

    if st.sidebar.button("Start Scan & Trade"):
        bot.running = True
        actions = bot.scan_and_trade(symbols)
        for act in actions:
            st.write(act)

    if st.sidebar.button("Stop Bot"):
        bot.running = False
        st.warning("Bot stopped.")

    # --- Monitor Positions ---
    st.subheader("📊 Open Trades")
    if bot.open_trades:
        df = pd.DataFrame(bot.open_trades.values())
        st.dataframe(df)

        fig = px.scatter(df, x="entry_price", y="tp", color="symbol",
                         size="size_contracts", hover_data=["sl", "trail_step"])
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No open trades.")

    st.subheader("📜 Trade Log")
    if bot.trade_log:
        st.dataframe(pd.DataFrame(bot.trade_log))
    else:
        st.info("No trades yet.")
