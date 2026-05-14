"""
باك تيست — Gate.io USDT Perpetual
بيانات حقيقية من Gate.io Public API | RR 1:5 | Trailing Stop | فلتر الجلسة
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from indicators import compute_all, vote, is_trading_session
from bot_engine import fetch_public_klines

FIXED_MARGIN   = 100
FIXED_LEVERAGE = 100
VOTE_THRESHOLD = 8      # ← 8 بدل 7
RR_RATIO       = 5      # ← 1:5 بدل 1:3


def fetch_gate_data(symbol="XAU_USDT", interval="5m", days=7):
    try:
        mins_per = int(interval.replace("m","").replace("h","")) * (60 if "h" in interval else 1)
    except Exception:
        mins_per = 5
    limit = min(days * 24 * (60 // mins_per), 2000)
    df = fetch_public_klines(symbol, interval, limit)
    if df is not None and len(df) > 0:
        print(f"[Backtest] ✅ {len(df)} شمعة حقيقية من Gate.io لـ {symbol}")
        return df
    return None


def generate_simulation(symbol="XAU_USDT", days=7, interval_minutes=5):
    np.random.seed(42)
    n = days * 24 * (60 // interval_minutes)
    configs = {
        "XAU_USDT": {"base": 3280.0, "vol": 0.010, "drift": 0.00004},
        "BTC_USDT": {"base": 97000.0,"vol": 0.020, "drift": 0.00006},
        "ETH_USDT": {"base": 2600.0, "vol": 0.025, "drift": 0.00005},
    }
    cfg = configs.get(symbol, {"base": 100.0, "vol": 0.02, "drift": 0.00004})
    vol = cfg["vol"] / (24 * 60 / interval_minutes) ** 0.5
    times  = pd.date_range(datetime(2026,5,7), periods=n, freq=f"{interval_minutes}min")
    ret    = np.random.normal(cfg["drift"], vol, n)
    idx    = np.random.choice(n, size=max(1, int(n*0.018)), replace=False)
    ret[idx] += np.random.choice([-vol*3, vol*4, vol*5, -vol*3], size=len(idx))
    prices = cfg["base"] * np.cumprod(1 + ret)
    rows   = []
    for i, (t, p) in enumerate(zip(times, prices)):
        noise = p * vol * 0.5
        o=p; c=p*(1+np.random.normal(0,vol*0.3))
        h=max(o,c)+abs(np.random.normal(0,noise))
        l=min(o,c)-abs(np.random.normal(0,noise))
        v=np.random.uniform(50,500)*(1+abs(ret[i])*30)
        rows.append({"timestamp":t,"open":round(o,4),"high":round(h,4),
                     "low":round(l,4),"close":round(c,4),"volume":round(v,2)})
    return pd.DataFrame(rows)


def run_backtest(df, symbol="XAU_USDT"):
    df = compute_all(df.copy())
    needed = ["rsi","macd","ema20","ema50","bb_lower","stoch_k","adx","cci","atr"]
    df = df.dropna(subset=[c for c in needed if c in df.columns]).reset_index(drop=True)
    if len(df) < 60:
        return {"error": "بيانات غير كافية"}

    balance = initial_bal = 10_000.0
    in_trade = False
    entry = sl = tp = qty = trail_high = trail_step = 0.0
    entry_t = entry_votes = None
    trades = []; equity = []
    win_streak = loss_streak = max_win = max_loss = 0

    for i in range(60, len(df)):
        window = df.iloc[:i+1]
        row    = df.iloc[i]
        close  = float(row["close"])
        ts     = row["timestamp"]
        equity.append({"time": ts, "equity": round(balance, 2)})

        if in_trade:
            # ── Trailing Stop ─────────────────────────────────────────────
            if close > trail_high + trail_step and trail_step > 0:
                move       = close - trail_high
                sl        += move
                trail_high = close

            if close >= tp:
                pnl = (tp - entry) * qty; balance += FIXED_MARGIN + pnl
                trades.append({"entry_time":entry_t,"exit_time":ts,"entry_price":round(entry,4),
                                "exit_price":round(tp,4),"qty":round(qty,6),"margin":FIXED_MARGIN,
                                "pnl_usdt":round(pnl,2),"pnl_pct":round(pnl/FIXED_MARGIN*100,2),
                                "result":"✅ TP","votes":entry_votes})
                win_streak+=1; loss_streak=0; max_win=max(max_win,win_streak); in_trade=False
            elif close <= sl:
                pnl = (sl - entry) * qty; balance += FIXED_MARGIN + pnl
                trades.append({"entry_time":entry_t,"exit_time":ts,"entry_price":round(entry,4),
                                "exit_price":round(sl,4),"qty":round(qty,6),"margin":FIXED_MARGIN,
                                "pnl_usdt":round(pnl,2),"pnl_pct":round(pnl/FIXED_MARGIN*100,2),
                                "result":"🛑 SL","votes":entry_votes})
                loss_streak+=1; win_streak=0; max_loss=max(max_loss,loss_streak); in_trade=False
            continue

        if balance < FIXED_MARGIN: break

        # ── فلتر الجلسة ──────────────────────────────────────────────────
        if not is_trading_session(ts): continue

        result = vote(window, check_session=False)
        v      = result["votes"]
        if v >= VOTE_THRESHOLD:
            atr_val     = result["atr"] or close * 0.001
            sl_dist     = atr_val * 1.5
            tp_dist     = sl_dist * RR_RATIO   # 1:5
            entry       = close
            sl          = round(close - sl_dist, 4)
            tp          = round(close + tp_dist, 4)
            qty         = round((FIXED_MARGIN * FIXED_LEVERAGE) / entry, 6)
            trail_step  = round(atr_val * 0.5, 4)
            trail_high  = close
            balance    -= FIXED_MARGIN
            in_trade    = True
            entry_t     = ts
            entry_votes = v

    if in_trade:
        last_c = float(df.iloc[-1]["close"])
        pnl    = (last_c - entry) * qty; balance += FIXED_MARGIN + pnl
        trades.append({"entry_time":entry_t,"exit_time":df.iloc[-1]["timestamp"],
                       "entry_price":round(entry,4),"exit_price":round(last_c,4),
                       "qty":round(qty,6),"margin":FIXED_MARGIN,
                       "pnl_usdt":round(pnl,2),"pnl_pct":round(pnl/FIXED_MARGIN*100,2),
                       "result":"🔵 Force","votes":entry_votes})

    df_t  = pd.DataFrame(trades) if trades else pd.DataFrame()
    total = len(df_t)
    wins  = int((df_t["pnl_usdt"] > 0).sum()) if total else 0
    loss  = total - wins
    wr    = round(wins/total*100,1) if total else 0
    net   = round(balance - initial_bal, 2)
    roi   = round(net/initial_bal*100, 2)
    avg_w = round(df_t[df_t["pnl_usdt"]>0]["pnl_usdt"].mean(),2) if wins else 0
    avg_l = round(df_t[df_t["pnl_usdt"]<0]["pnl_usdt"].mean(),2) if loss else 0
    best  = round(df_t["pnl_usdt"].max(),2) if total else 0
    worst = round(df_t["pnl_usdt"].min(),2) if total else 0
    peak  = initial_bal; max_dd = 0
    for eq in [e["equity"] for e in equity]:
        if eq > peak: peak = eq
        dd = (peak-eq)/peak*100
        if dd > max_dd: max_dd = dd
    pf = round(abs(avg_w*wins)/max(abs(avg_l*loss),0.01),2)

    return {
        "summary": {
            "symbol": symbol, "platform": "Gate.io USDT Perpetual",
            "period": f"7 أيام ({df.iloc[0]['timestamp'].strftime('%Y-%m-%d')} → {df.iloc[-1]['timestamp'].strftime('%Y-%m-%d')})",
            "interval": "5 دقائق", "rr_ratio": f"1:{RR_RATIO}",
            "threshold": f"{VOTE_THRESHOLD}/10",
            "initial_balance": initial_bal, "final_balance": round(balance,2),
            "net_profit": net, "roi_pct": roi,
            "total_trades": total, "wins": wins, "losses": loss, "win_rate": wr,
            "avg_win_usdt": avg_w, "avg_loss_usdt": avg_l,
            "best_trade": best, "worst_trade": worst,
            "max_drawdown": round(max_dd,2),
            "max_win_streak": max_win, "max_loss_streak": max_loss,
            "profit_factor": pf,
        },
        "trades": df_t,
        "equity_curve": pd.DataFrame(equity),
        "price_df": df,
    }


def plot_equity_curve(equity_df, summary):
    color = "#00ff88" if summary["net_profit"] >= 0 else "#ff2d55"
    r,g,b = int(color[1:3],16),int(color[3:5],16),int(color[5:7],16)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=equity_df["time"],y=equity_df["equity"],
        mode="lines",line=dict(color=color,width=2),fill="tozeroy",
        fillcolor=f"rgba({r},{g},{b},0.07)",name="رصيد المحفظة"))
    fig.add_hline(y=summary["initial_balance"],line_dash="dash",line_color="#6b7494",
                  annotation_text="رصيد البداية")
    fig.update_layout(title=f"📈 منحنى الأرباح — {summary['symbol']} (Gate.io | RR 1:{RR_RATIO})",
        paper_bgcolor="#05070d",plot_bgcolor="#05070d",font=dict(color="#c8d0e8"),
        xaxis=dict(gridcolor="#1c2035"),yaxis=dict(gridcolor="#1c2035",tickprefix="$"),
        margin=dict(l=10,r=10,t=40,b=10),height=300)
    return fig

def plot_trade_results(df_trades, symbol=""):
    if df_trades.empty: return go.Figure()
    colors=["#00ff88" if p>0 else "#ff2d55" for p in df_trades["pnl_usdt"]]
    fig=go.Figure(go.Bar(x=list(range(1,len(df_trades)+1)),y=df_trades["pnl_usdt"],
        marker_color=colors,name="PnL"))
    fig.update_layout(title=f"💹 PnL لكل صفقة — {symbol}",
        paper_bgcolor="#05070d",plot_bgcolor="#05070d",font=dict(color="#c8d0e8"),
        xaxis=dict(gridcolor="#1c2035"),yaxis=dict(gridcolor="#1c2035",tickprefix="$"),
        margin=dict(l=10,r=10,t=40,b=10),height=260)
    return fig

def plot_candlestick(price_df, trades_df, symbol=""):
    sample=price_df.iloc[-288:].copy()
    fig=go.Figure()
    fig.add_trace(go.Candlestick(x=sample["timestamp"],open=sample["open"],high=sample["high"],
        low=sample["low"],close=sample["close"],increasing_line_color="#00ff88",
        decreasing_line_color="#ff2d55",name=symbol))
    if "ema20" in sample.columns:
        fig.add_trace(go.Scatter(x=sample["timestamp"],y=sample["ema20"],
            line=dict(color="#ffbe0b",width=1),name="EMA20"))
    if "ema50" in sample.columns:
        fig.add_trace(go.Scatter(x=sample["timestamp"],y=sample["ema50"],
            line=dict(color="#06b6d4",width=1),name="EMA50"))
    if not trades_df.empty:
        recent=trades_df[trades_df["entry_time"]>=sample["timestamp"].iloc[0]]
        if not recent.empty:
            fig.add_trace(go.Scatter(x=recent["entry_time"],y=recent["entry_price"],
                mode="markers",marker=dict(color="#00ff88",size=10,symbol="triangle-up"),name="دخول"))
            fig.add_trace(go.Scatter(x=recent["exit_time"],y=recent["exit_price"],
                mode="markers",marker=dict(color=["#00ff88" if r=="✅ TP" else "#ff2d55"
                    for r in recent["result"]],size=10,symbol="triangle-down"),name="خروج"))
    fig.update_layout(title=f"🕯️ {symbol} — آخر 24 ساعة",paper_bgcolor="#05070d",
        plot_bgcolor="#05070d",font=dict(color="#c8d0e8"),
        xaxis=dict(gridcolor="#1c2035",rangeslider_visible=False),
        yaxis=dict(gridcolor="#1c2035",tickprefix="$"),
        margin=dict(l=10,r=10,t=40,b=10),height=400,legend=dict(bgcolor="#111422"))
    return fig
