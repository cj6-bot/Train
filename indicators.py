"""
نظام التصويت - 10 مؤشرات فنية
القاعدة: 8/10 = LONG | أقل من 8 = MONITORING
"""

import numpy as np
import pandas as pd
import pandas_ta as ta


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"]  = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"] = macd.iloc[:, 0]
        df["macd_signal"] = macd.iloc[:, 2]
        df["macd_hist"]   = macd.iloc[:, 1]
    df["ema20"] = ta.ema(df["close"], length=20)
    df["ema50"] = ta.ema(df["close"], length=50)
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        df["bb_upper"]  = bb.iloc[:, 0]
        df["bb_middle"] = bb.iloc[:, 1]
        df["bb_lower"]  = bb.iloc[:, 2]
    stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3, smooth_k=3)
    if stoch is not None:
        df["stoch_k"] = stoch.iloc[:, 0]
        df["stoch_d"] = stoch.iloc[:, 1]
    adx = ta.adx(df["high"], df["low"], df["close"], length=14)
    if adx is not None:
        df["adx"] = adx.iloc[:, 0]
    df["cci"] = ta.cci(df["high"], df["low"], df["close"], length=20)
    ichi = ta.ichimoku(df["high"], df["low"], df["close"], tenkan=9, kijun=26, senkou=52)
    if ichi is not None and len(ichi) == 2:
        span_df = ichi[1]
        df["ichi_span_a"] = span_df.iloc[:, 0].reindex(df.index)
        df["ichi_span_b"] = span_df.iloc[:, 1].reindex(df.index)
    df["obv"] = ta.obv(df["close"], df["volume"])
    df["atr"]  = ta.atr(df["high"], df["low"], df["close"], length=14)
    return df


def _f(val):
    try:
        v = float(val)
        return None if (np.isnan(v) or np.isinf(v)) else v
    except Exception:
        return None


# ─── فلتر الجلسة ── تداول XAU فقط London + NY ──────────────────────────────
def is_trading_session(ts=None) -> bool:
    """True = London أو NY session (07:00-16:00 UTC)"""
    import datetime
    if ts is None:
        ts = datetime.datetime.utcnow()
    elif hasattr(ts, 'to_pydatetime'):
        ts = ts.to_pydatetime()
    hour = ts.hour
    return 7 <= hour < 16


def vote(df: pd.DataFrame, check_session: bool = True) -> dict:
    if len(df) < 3:
        return _empty_result()

    r  = df.iloc[-1]
    r2 = df.iloc[-2]

    # ── فلتر الجلسة ──────────────────────────────────────────────────────────
    ts = r.get("timestamp", None) if isinstance(r, dict) else getattr(r, "timestamp", None)
    if check_session and not is_trading_session(ts):
        return {**_empty_result(), "action": "⏰ خارج جلسة التداول (07-16 UTC)", "session_ok": False}

    details = {}
    votes   = 0

    # 1. RSI — اختراق 50 ─────────────────────────────────────────────────────
    rsi  = _f(r["rsi"]); rsi2 = _f(r2["rsi"])
    if rsi is not None and rsi2 is not None:
        if rsi > 50 and rsi2 <= 50:
            details["RSI"] = {"status": "✅ اختراق 50 صعوداً", "vote": True, "value": f"{rsi:.1f}"}; votes += 1
        elif rsi > 50:
            details["RSI"] = {"status": "🟡 فوق 50 (دعم)", "vote": True, "value": f"{rsi:.1f}"}; votes += 1
        else:
            details["RSI"] = {"status": "⏳ تحت 50", "vote": False, "value": f"{rsi:.1f}"}
    else:
        details["RSI"] = {"status": "❓ لا بيانات", "vote": False, "value": "—"}

    # 2. MACD — تقاطع تحت الصفر ──────────────────────────────────────────────
    m = _f(r["macd"]); ms = _f(r["macd_signal"])
    m2= _f(r2["macd"]); ms2=_f(r2["macd_signal"])
    if None not in (m, ms, m2, ms2):
        crossed = m > ms and m2 <= ms2
        if crossed and m < 0:
            details["MACD"] = {"status": "✅ تقاطع صعودي تحت الصفر", "vote": True, "value": f"{m:.5f}"}; votes += 1
        elif m > ms:
            details["MACD"] = {"status": "🟡 MACD > إشارة", "vote": True, "value": f"{m:.5f}"}; votes += 1
        else:
            details["MACD"] = {"status": "⏳ تحت خط الإشارة", "vote": False, "value": f"{m:.5f}"}
    else:
        details["MACD"] = {"status": "❓ لا بيانات", "vote": False, "value": "—"}

    # 3. EMA 20/50 ────────────────────────────────────────────────────────────
    e20=_f(r["ema20"]); e50=_f(r["ema50"])
    e202=_f(r2["ema20"]); e502=_f(r2["ema50"])
    close=_f(r["close"])
    if None not in (e20, e50, e202, e502, close):
        if e20 > e50 and e202 <= e502 and close > e50:
            details["EMA"] = {"status": "✅ تقاطع ذهبي + فوق EMA50", "vote": True, "value": f"{e20:.2f}"}; votes += 1
        elif e20 > e50 and close > e50:
            details["EMA"] = {"status": "🟡 EMA20 > EMA50", "vote": True, "value": f"{e20:.2f}"}; votes += 1
        elif close < e50:
            details["EMA"] = {"status": "🔴 سعر تحت EMA50 — ممنوع", "vote": False, "value": f"{e50:.2f}"}
        else:
            details["EMA"] = {"status": "⏳ انتظار التقاطع", "vote": False, "value": f"{e20:.2f}"}
    else:
        details["EMA"] = {"status": "❓ لا بيانات", "vote": False, "value": "—"}

    # 4. Bollinger Bands ──────────────────────────────────────────────────────
    bbu=_f(r["bb_upper"]); bbm=_f(r["bb_middle"]); bbl=_f(r["bb_lower"])
    bbl2=_f(r2["bb_lower"]); close2=_f(r2["close"])
    if None not in (bbu, bbm, bbl, close, close2, bbl2):
        if close2 <= bbl2 and close > bbl:
            details["BB"] = {"status": "✅ ارتداد من الخط السفلي", "vote": True, "value": f"{bbm:.2f}"}; votes += 1
        elif close > bbm:
            details["BB"] = {"status": "✅ فوق خط المنتصف", "vote": True, "value": f"{bbm:.2f}"}; votes += 1
        else:
            details["BB"] = {"status": "⏳ بين الخطوط", "vote": False, "value": f"{bbl:.2f}"}
    else:
        details["BB"] = {"status": "❓ لا بيانات", "vote": False, "value": "—"}

    # 5. Stochastic ───────────────────────────────────────────────────────────
    sk=_f(r["stoch_k"]); sd=_f(r["stoch_d"])
    sk2=_f(r2["stoch_k"]); sd2=_f(r2["stoch_d"])
    if None not in (sk, sd, sk2, sd2):
        if sk > sd and sk2 <= sd2 and sk < 20:
            details["Stoch"] = {"status": "✅ تقاطع صعودي تحت 20", "vote": True, "value": f"{sk:.1f}"}; votes += 1
        elif sk > sd and sk < 25:
            details["Stoch"] = {"status": "🟡 K > D تحت 25", "vote": True, "value": f"{sk:.1f}"}; votes += 1
        else:
            details["Stoch"] = {"status": "⏳ انتظار", "vote": False, "value": f"{sk:.1f}"}
    else:
        details["Stoch"] = {"status": "❓ لا بيانات", "vote": False, "value": "—"}

    # 6. ADX ──────────────────────────────────────────────────────────────────
    adx = _f(r["adx"])
    if adx is not None:
        if adx >= 35:
            details["ADX"] = {"status": f"✅ ترند قوي جداً ({adx:.1f})", "vote": True, "value": f"{adx:.1f}"}; votes += 1
        elif adx >= 25:
            details["ADX"] = {"status": f"✅ ترند قوي ({adx:.1f})", "vote": True, "value": f"{adx:.1f}"}; votes += 1
        else:
            details["ADX"] = {"status": f"🔴 ضعيف ({adx:.1f})", "vote": False, "value": f"{adx:.1f}"}
    else:
        details["ADX"] = {"status": "❓ لا بيانات", "vote": False, "value": "—"}

    # 7. CCI ──────────────────────────────────────────────────────────────────
    cci=_f(r["cci"]); cci2=_f(r2["cci"])
    if cci is not None and cci2 is not None:
        if cci > -100 and cci2 <= -100:
            details["CCI"] = {"status": "✅ اختراق -100 (دخول الحيتان)", "vote": True, "value": f"{cci:.1f}"}; votes += 1
        elif cci > -100:
            details["CCI"] = {"status": "🟡 فوق -100", "vote": True, "value": f"{cci:.1f}"}; votes += 1
        else:
            details["CCI"] = {"status": "⏳ تحت -100", "vote": False, "value": f"{cci:.1f}"}
    else:
        details["CCI"] = {"status": "❓ لا بيانات", "vote": False, "value": "—"}

    # 8. Ichimoku ─────────────────────────────────────────────────────────────
    spa=_f(r.get("ichi_span_a")); spb=_f(r.get("ichi_span_b"))
    if spa is not None and spb is not None and close is not None:
        cloud_top = max(spa, spb)
        if close > cloud_top:
            details["Ichimoku"] = {"status": "✅ فوق السحابة", "vote": True, "value": f"{cloud_top:.2f}"}; votes += 1
        elif close < min(spa, spb):
            details["Ichimoku"] = {"status": "🔴 تحت السحابة — الحيتان يبيعون", "vote": False, "value": f"{cloud_top:.2f}"}
        else:
            details["Ichimoku"] = {"status": "⏳ داخل السحابة", "vote": False, "value": f"{cloud_top:.2f}"}
    else:
        if e50 and close and close > e50:
            details["Ichimoku"] = {"status": "🟡 فوق EMA50 (بديل)", "vote": True, "value": f"{e50:.2f}"}; votes += 1
        else:
            details["Ichimoku"] = {"status": "⏳ بيانات غير كافية", "vote": False, "value": "—"}

    # 9. OBV ──────────────────────────────────────────────────────────────────
    obv=_f(r["obv"]); obv2=_f(r2["obv"])
    if obv is not None and obv2 is not None and close is not None and close2 is not None:
        if obv > obv2 and close > close2:
            details["OBV"] = {"status": "✅ OBV + سعر يصعدان", "vote": True, "value": f"{obv:.0f}"}; votes += 1
        elif obv > obv2:
            details["OBV"] = {"status": "🟡 OBV يصعد", "vote": True, "value": f"{obv:.0f}"}; votes += 1
        else:
            details["OBV"] = {"status": "⏳ OBV هابط", "vote": False, "value": f"{obv:.0f}"}
    else:
        details["OBV"] = {"status": "❓ لا بيانات", "vote": False, "value": "—"}

    # 10. ATR — SL/TP ديناميكي + Trailing ───────────────────────────────────
    atr = _f(r["atr"])
    entry_price = close if close else 0
    if atr and entry_price:
        sl_dist = atr * 1.5
        tp_dist = sl_dist * 5          # ← RR 1:5 (بدل 1:3)
        sl_price = round(entry_price - sl_dist, 2)
        tp_price = round(entry_price + tp_dist, 2)
        sl_pct   = round(sl_dist / entry_price * 100, 3)
        tp_pct   = round(tp_dist / entry_price * 100, 3)
        trail_step = round(atr * 0.5, 2)   # خطوة الـ Trailing Stop
        details["ATR"] = {
            "status": f"📏 ATR={atr:.4f} | SL -{sl_pct}% | TP +{tp_pct}% | Trail={trail_step}",
            "vote": True, "value": f"{atr:.4f}",
        }
        votes += 1
    else:
        sl_price = tp_price = sl_pct = tp_pct = trail_step = 0
        details["ATR"] = {"status": "❓ ATR غير متاح", "vote": False, "value": "—"}
        trail_step = 0

    # ── الحكم النهائي — رفعنا الـ threshold إلى 8 ──────────────────────────
    THRESHOLD = 8
    action = "LONG 🚀" if votes >= THRESHOLD else f"⏳ مراقبة — {votes}/10 أصوات"

    return {
        "votes":       votes,
        "max_votes":   10,
        "threshold":   THRESHOLD,
        "action":      action,
        "details":     details,
        "entry_price": round(entry_price, 4),
        "sl_price":    sl_price,
        "tp_price":    tp_price,
        "sl_pct":      sl_pct,
        "tp_pct":      tp_pct,
        "atr":         atr or 0,
        "trail_step":  trail_step,
        "session_ok":  True,
    }


def _empty_result():
    return {
        "votes": 0, "max_votes": 10, "threshold": 8,
        "action": "⏳ بيانات غير كافية",
        "details": {}, "entry_price": 0,
        "sl_price": 0, "tp_price": 0,
        "sl_pct": 0, "tp_pct": 0, "atr": 0,
        "trail_step": 0, "session_ok": True,
  }
