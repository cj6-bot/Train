"""
محرك التداول — Gate.io USDT Perpetual Futures
مارجن ثابت: $100 | رافعة: 100x | Trailing Stop | فلتر الجلسة
"""

import logging, time, hmac, hashlib, requests
from datetime import datetime
import pandas as pd
from indicators import compute_all, vote

logger = logging.getLogger(__name__)

FIXED_MARGIN   = 100
FIXED_LEVERAGE = 100
VOTE_THRESHOLD = 8          # ← رفعناه لـ 8
SETTLE         = "usdt"
LIVE_REST      = "https://api.gateio.ws/api/v4"
TESTNET_REST   = "https://fx-api-testnet.gateio.ws/api/v4"
PUBLIC_REST    = "https://api.gateio.ws/api/v4"
KLINES_LIMIT   = 200


class GateClient:
    def __init__(self, api_key, api_secret, testnet=True):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.base       = TESTNET_REST if testnet else LIVE_REST
        self.session    = requests.Session()

    def _headers(self, method, path, query="", body=""):
        ts        = str(int(time.time()))
        body_hash = hashlib.sha512(body.encode()).hexdigest()
        msg       = f"{method}\n{path}\n{query}\n{body_hash}\n{ts}"
        sig       = hmac.new(self.api_secret.encode(), msg.encode(), hashlib.sha512).hexdigest()
        return {"KEY": self.api_key, "Timestamp": ts, "SIGN": sig,
                "Content-Type": "application/json", "Accept": "application/json"}

    def get(self, path, params=None):
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        url   = self.base + path + ("?" + query if query else "")
        r     = self.session.get(url, headers=self._headers("GET", path, query), timeout=10)
        r.raise_for_status(); return r.json()

    def post(self, path, body):
        import json as _j
        b   = _j.dumps(body)
        url = self.base + path
        r   = self.session.post(url, headers=self._headers("POST", path, "", b), data=b, timeout=10)
        r.raise_for_status(); return r.json()

    def delete(self, path, params=None):
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        url   = self.base + path + ("?" + query if query else "")
        r     = self.session.delete(url, headers=self._headers("DELETE", path, query), timeout=10)
        r.raise_for_status(); return r.json()


def fetch_public_klines(symbol, interval="5m", limit=200):
    url    = f"{PUBLIC_REST}/futures/{SETTLE}/candlesticks"
    params = {"contract": symbol, "interval": interval, "limit": limit}
    try:
        r   = requests.get(url, params=params, timeout=10); r.raise_for_status()
        raw = r.json()
        if not raw: return None
        rows = [{"timestamp": pd.to_datetime(int(c["t"]), unit="s"),
                 "open": float(c.get("o",0)), "high": float(c.get("h",0)),
                 "low":  float(c.get("l",0)), "close": float(c.get("c",0)),
                 "volume": float(c.get("v",0))} for c in raw]
        df = pd.DataFrame(rows)
        return df[df["close"] > 0].reset_index(drop=True)
    except Exception as e:
        logger.warning(f"fetch_public_klines({symbol}): {e}"); return None


def list_futures_contracts():
    try:
        r = requests.get(f"{PUBLIC_REST}/futures/{SETTLE}/contracts", timeout=10)
        r.raise_for_status(); return r.json()
    except Exception as e:
        logger.warning(f"list_contracts: {e}"); return []


def to_gate_symbol(symbol):
    s = symbol.upper().strip()
    if "_" in s: return s
    if s.endswith("USDT"): return s[:-4] + "_USDT"
    return s


class BotEngine:
    def __init__(self, api_key, api_secret, testnet=True):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.testnet    = testnet
        self.client     = None
        self.connected  = False
        self.running    = False
        self.trade_log   = []
        self.open_trades = {}
        self.last_vote   = {}
        # Trailing Stop tracking
        self.trail_prices = {}   # symbol → highest price since entry

    def connect(self):
        try:
            self.client = GateClient(self.api_key, self.api_secret, self.testnet)
            data = self.client.get(f"/futures/{SETTLE}/accounts")
            bal  = {
                "available":   round(float(data.get("available", 0)), 2),
                "total":       round(float(data.get("total", 0)), 2),
                "pnl":         round(float(data.get("unrealised_pnl", 0)), 2),
                "margin_used": round(float(data.get("position_margin", 0)), 2),
            }
            self.connected = True
            return {"ok": True, "balance": bal, "testnet": self.testnet}
        except Exception as e:
            self.connected = False
            return {"ok": False, "error": str(e)}

    def get_balance(self):
        if not self.client: return {}
        try:
            data = self.client.get(f"/futures/{SETTLE}/accounts")
            return {
                "available":   round(float(data.get("available", 0)), 2),
                "total":       round(float(data.get("total", 0)), 2),
                "pnl":         round(float(data.get("unrealised_pnl", 0)), 2),
                "margin_used": round(float(data.get("position_margin", 0)), 2),
            }
        except Exception as e:
            logger.warning(f"get_balance: {e}"); return {}

    def fetch_klines(self, symbol, interval="5m", limit=KLINES_LIMIT):
        return fetch_public_klines(to_gate_symbol(symbol), interval, limit)

    def get_current_price(self, symbol):
        sym = to_gate_symbol(symbol)
        try:
            r = requests.get(f"{PUBLIC_REST}/futures/{SETTLE}/contracts/{sym}", timeout=5)
            r.raise_for_status()
            d = r.json()
            return float(d.get("last_price", d.get("mark_price", 0)))
        except Exception as e:
            logger.warning(f"get_price({sym}): {e}"); return None

    def get_quanto_multiplier(self, symbol):
        sym = to_gate_symbol(symbol)
        try:
            r = requests.get(f"{PUBLIC_REST}/futures/{SETTLE}/contracts/{sym}", timeout=5)
            r.raise_for_status()
            return float(r.json().get("quanto_multiplier", 1.0))
        except Exception:
            return 1.0

    def analyse(self, symbol, interval="5m"):
        df = self.fetch_klines(symbol, interval, KLINES_LIMIT)
        if df is None or len(df) < 60: return None
        df = compute_all(df)
        result = vote(df, check_session=True)
        result["symbol"]   = to_gate_symbol(symbol)
        result["interval"] = interval
        result["time"]     = datetime.now().strftime("%H:%M:%S")
        self.last_vote[symbol] = result
        return result

    def set_leverage(self, symbol):
        sym = to_gate_symbol(symbol)
        try:
            self.client.post(
                f"/futures/{SETTLE}/positions/{sym}/leverage",
                {"leverage": str(FIXED_LEVERAGE), "cross_leverage_limit": "0"}
            ); return True
        except Exception as e:
            logger.warning(f"set_leverage({sym}): {e}"); return False

    def open_long(self, symbol, vote_result):
        if not self.client: return None
        sym = to_gate_symbol(symbol)
        if sym in self.open_trades: return None
        try:
            entry_price = vote_result["entry_price"] or self.get_current_price(symbol)
            if not entry_price: return None

            qm       = self.get_quanto_multiplier(symbol)
            notional = FIXED_MARGIN * FIXED_LEVERAGE
            size     = int(notional / (entry_price * qm))
            if size <= 0: size = 1

            sl = round(vote_result["sl_price"], 2)
            tp = round(vote_result["tp_price"], 2)
            trail_step = vote_result.get("trail_step", 0)

            self.set_leverage(symbol)

            # أمر الدخول
            order = self.client.post(f"/futures/{SETTLE}/orders", {
                "contract": sym, "size": size, "price": "0", "tif": "ioc"
            })
            exec_price = float(order.get("fill_price", entry_price) or entry_price)
            exec_size  = int(order.get("size", size))

            # SL (≤ sl_price)
            try:
                self.client.post(f"/futures/{SETTLE}/price_orders", {
                    "initial": {"contract": sym, "size": 0, "price": "0",
                                "tif": "ioc", "close": True, "reduce_only": True},
                    "trigger": {"strategy_type": 0, "price_type": 0,
                                "price": str(sl), "rule": 2, "expiration": 86400}
                })
            except Exception as e:
                logger.warning(f"SL order: {e}")

            # TP (≥ tp_price)
            try:
                self.client.post(f"/futures/{SETTLE}/price_orders", {
                    "initial": {"contract": sym, "size": 0, "price": "0",
                                "tif": "ioc", "close": True, "reduce_only": True},
                    "trigger": {"strategy_type": 0, "price_type": 0,
                                "price": str(tp), "rule": 1, "expiration": 86400}
                })
            except Exception as e:
                logger.warning(f"TP order: {e}")

            trade_info = {
                "id": len(self.trade_log) + 1, "symbol": sym, "side": "LONG",
                "entry_price": round(exec_price, 4), "size_contracts": exec_size,
                "margin_usdt": FIXED_MARGIN,
                "notional_usdt": round(exec_size * exec_price * qm, 2),
                "leverage": FIXED_LEVERAGE,
                "tp": tp, "sl": sl, "trail_step": trail_step,
                "current_sl": sl,          # SL يتحرك مع Trailing
                "votes": vote_result["votes"], "status": "OPEN",
                "open_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "pnl_usdt": None, "pnl_pct": None,
            }
            self.open_trades[sym] = trade_info
            self.trail_prices[sym] = exec_price   # نبدأ تتبع الـ Trailing
            self.trade_log.append(trade_info)

            logger.info(
                f"✅ OPEN LONG {sym} @ {exec_price:.4f} | "
                f"TP={tp} | SL={sl} | Trail={trail_step} | "
                f"Votes={vote_result['votes']}/10"
            )
            return trade_info
        except Exception as e:
            logger.error(f"open_long({sym}): {e}"); return None

    def update_trailing_stop(self, symbol):
        """
        يحدّث الـ Trailing Stop:
        إذا ارتفع السعر بمقدار trail_step → يرفع الـ SL بنفس المقدار
        """
        sym = to_gate_symbol(symbol)
        if sym not in self.open_trades: return

        t          = self.open_trades[sym]
        trail_step = t.get("trail_step", 0)
        if trail_step <= 0: return

        current_price = self.get_current_price(symbol)
        if not current_price: return

        prev_high = self.trail_prices.get(sym, t["entry_price"])

        if current_price > prev_high + trail_step:
            move         = current_price - prev_high
            new_sl       = round(t["current_sl"] + move, 2)
            self.trail_prices[sym] = current_price
            t["current_sl"] = new_sl

            # إلغاء SL القديم وإرسال الجديد
            try:
                self.client.delete(
                    f"/futures/{SETTLE}/price_orders",
                    {"contract": sym, "side": "ask"}
                )
            except Exception:
                pass
            try:
                self.client.post(f"/futures/{SETTLE}/price_orders", {
                    "initial": {"contract": sym, "size": 0, "price": "0",
                                "tif": "ioc", "close": True, "reduce_only": True},
                    "trigger": {"strategy_type": 0, "price_type": 0,
                                "price": str(new_sl), "rule": 2, "expiration": 86400}
                })
                logger.info(f"🔄 Trailing SL updated: {sym} → {new_sl}")
            except Exception as e:
                logger.warning(f"Trail SL update {sym}: {e}")

    def close_position(self, symbol, reason="MANUAL"):
        sym = to_gate_symbol(symbol)
        if sym not in self.open_trades: return None
        try:
            t    = self.open_trades[sym]
            size = t["size_contracts"]
            try:
                self.client.delete(f"/futures/{SETTLE}/price_orders",
                                   {"contract": sym, "side": "ask"})
            except Exception:
                pass
            order = self.client.post(f"/futures/{SETTLE}/orders", {
                "contract": sym, "size": -size,
                "price": "0", "tif": "ioc", "reduce_only": True
            })
            exit_price = float(
                order.get("fill_price") or self.get_current_price(symbol) or t["entry_price"]
            )
            qm       = self.get_quanto_multiplier(symbol)
            pnl_usdt = (exit_price - t["entry_price"]) * size * qm
            pnl_pct  = pnl_usdt / FIXED_MARGIN * 100
            t.update({
                "status": "CLOSED", "exit_price": round(exit_price, 4),
                "pnl_usdt": round(pnl_usdt, 2), "pnl_pct": round(pnl_pct, 2),
                "close_reason": reason,
                "close_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            del self.open_trades[sym]
            self.trail_prices.pop(sym, None)
            logger.info(f"🔒 CLOSE {sym} @ {exit_price:.4f} | PnL=${pnl_usdt:.2f} | {reason}")
            return t
        except Exception as e:
            logger.error(f"close_position({sym}): {e}"); return None

    def get_open_positions(self):
        if not self.client: return []
        try:
            positions = self.client.get(f"/futures/{SETTLE}/positions")
            return [p for p in positions if float(p.get("size", 0)) != 0]
        except Exception as e:
            logger.warning(f"get_positions: {e}"); return []

    def monitor_positions(self):
        updates = []
        try:
            live = {p["contract"]: p for p in self.get_open_positions()}
            for sym in list(self.open_trades.keys()):
                self.update_trailing_stop(sym)   # ← تحديث Trailing Stop
                if sym not in live:
                    t = self.open_trades[sym]
                    t.update({"status": "CLOSED (TP/SL)",
                               "close_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    updates.append(t)
                    del self.open_trades[sym]
                    self.trail_prices.pop(sym, None)
        except Exception as e:
            logger.warning(f"monitor_positions: {e}")
        return updates

    def scan_and_trade(self, symbols, interval="5m"):
        actions = []
        for symbol in symbols:
            if not self.running: break
            sym    = to_gate_symbol(symbol)
            result = self.analyse(symbol, interval)
            if result is None:
                actions.append({"type": "ERROR", "symbol": sym, "msg": "لا بيانات"}); continue
            if not result.get("session_ok", True):
                actions.append({"type": "SESSION", "symbol": sym,
                                 "msg": "خارج جلسة التداول"}); continue
            v = result["votes"]
            if v >= VOTE_THRESHOLD and sym not in self.open_trades:
                trade = self.open_long(symbol, result)
                if trade:
                    actions.append({"type": "OPEN", "symbol": sym, "votes": v, "trade": trade})
                    continue
            actions.append({"type": "SCAN", "symbol": sym, "votes": v, "result": result})
            time.sleep(0.4)
        return actions

    def get_available_contracts(self):
        contracts = list_futures_contracts()
        return [c["name"] for c in contracts
                if not c.get("in_delisting") and float(c.get("order_size_min", 1)) >= 0]
