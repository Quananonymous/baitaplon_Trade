import json
import hmac
import hashlib
import time
import threading
import logging
import traceback
import random
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional, Any, Set

import requests
import websocket

# ----------------- LOGGER ----------------- #

logger = logging.getLogger("trading_bot_lib")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_formatter = logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

# ----------------- CẤU HÌNH ----------------- #

BINANCE_API_URL = "https://fapi.binance.com"
TELEGRAM_API_URL = "https://api.telegram.org"

MIN_24H_VOLUME_USDC = 500_000  # USDC
EXCLUDED_SYMBOLS = {"BTCDOMUSDC", "USDCUSDC", "BUSDUSDC", "FDUSDUSDC", "TUSDTUSDC"}

REQUEST_TIMEOUT = 10
WS_RECONNECT_DELAY = 5

_USDC_CACHE = {"last_update": 0, "symbols": []}
_USDC_CACHE_TTL = 60

_TICKER_CACHE = {"last_update": 0, "data": {}}
_TICKER_CACHE_TTL = 30


# ----------------- HÀM TIỆN ÍCH BINANCE ----------------- #

def _sign(params: Dict[str, Any], secret_key: str) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()


def _binance_request(
    method: str,
    path: str,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    signed: bool = False,
    base_url: str = BINANCE_API_URL,
) -> Dict[str, Any]:
    url = base_url + path
    headers = {"X-MBX-APIKEY": api_key} if api_key else {}
    params = params or {}

    if signed:
        if not api_secret:
            raise ValueError("Cần api_secret cho signed request")
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        params["signature"] = _sign(params, api_secret)

    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        elif method == "POST":
            resp = requests.post(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        elif method == "DELETE":
            resp = requests.delete(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if resp.status_code != 200:
            logger.error(f"Binance API error {resp.status_code}: {resp.text}")
            raise Exception(f"Binance API error {resp.status_code}: {resp.text}")

        return resp.json()
    except Exception as e:
        logger.error(f"Lỗi request Binance: {e}")
        raise


def send_telegram(
    text: str,
    chat_id: Optional[str] = None,
    bot_token: Optional[str] = None,
    parse_mode: str = "HTML",
    reply_markup: Optional[Dict[str, Any]] = None,
    default_chat_id: Optional[str] = None,
) -> None:
    if not bot_token:
        return
    if not chat_id:
        chat_id = default_chat_id
    if not chat_id:
        return

    try:
        url = f"{TELEGRAM_API_URL}/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)

        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            logger.error(f"Telegram API error {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"Lỗi gửi Telegram: {e}")


# ----------------- FRONTEND EVENT BUS ----------------- #

class FrontendEventBus:
    """
    EventBus để backend/frontend đọc log + dữ liệu realtime.
    """

    def __init__(self, max_events: int = 1000):
        self._events: List[Dict[str, Any]] = []
        self._subscribers: List[Any] = []
        self._lock = threading.Lock()
        self.max_events = max_events

    def publish(self, event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        event.setdefault("timestamp", time.time())
        with self._lock:
            self._events.append(event)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events:]
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"FrontendEventBus subscriber error: {e}")

    def get_recent(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            if limit <= 0:
                return []
            return self._events[-limit:].copy()

    def subscribe(self, callback) -> None:
        if not callable(callback):
            return
        with self._lock:
            self._subscribers.append(callback)


GLOBAL_EVENT_BUS = FrontendEventBus()


# ----------------- WEBSOCKET QUẢN LÝ GIÁ REALTIME ----------------- #

class WebSocketManager:
    """
    Mỗi symbol 1 WebSocket kline_1m, đẩy giá realtime cho bot.
    """

    def __init__(self):
        self.connections: Dict[str, websocket.WebSocketApp] = {}
        self.threads: Dict[str, threading.Thread] = {}
        self.callbacks: Dict[str, List[Any]] = defaultdict(list)
        self.lock = threading.Lock()
        self.running = True

    def _create_ws(self, symbol: str) -> websocket.WebSocketApp:
        symbol_lower = symbol.lower()
        stream_name = f"{symbol_lower}@kline_1m"
        url = f"wss://fstream.binance.com/ws/{stream_name}"

        def on_message(ws, message):
            try:
                data = json.loads(message)
                kline = data.get("k", {})
                price = float(kline.get("c", 0.0))
                with self.lock:
                    callbacks = list(self.callbacks.get(symbol, []))
                for cb in callbacks:
                    try:
                        cb(price, symbol)
                    except Exception as e:
                        logger.error(f"Lỗi callback price {symbol}: {e}")
            except Exception as e:
                logger.error(f"Lỗi parse WS {symbol}: {e}")

        def on_error(ws, error):
            logger.error(f"WS error [{symbol}]: {error}")

        def on_close(ws, *args):
            logger.warning(f"WS closed [{symbol}]")

        def on_open(ws):
            logger.info(f"WS opened [{symbol}]")

        return websocket.WebSocketApp(
            url, on_message=on_message, on_error=on_error, on_close=on_close, on_open=on_open
        )

    def _run_ws(self, symbol: str):
        while self.running:
            try:
                ws = self._create_ws(symbol)
                with self.lock:
                    self.connections[symbol] = ws
                ws.run_forever()
            except Exception as e:
                logger.error(f"Lỗi WS cho {symbol}: {e}")
            finally:
                with self.lock:
                    self.connections.pop(symbol, None)
                if self.running:
                    time.sleep(WS_RECONNECT_DELAY)

    def subscribe_price(self, symbol: str, callback) -> None:
        with self.lock:
            self.callbacks[symbol].append(callback)
            if symbol not in self.threads:
                t = threading.Thread(target=self._run_ws, args=(symbol,), daemon=True)
                self.threads[symbol] = t
                t.start()

    def unsubscribe_price(self, symbol: str, callback) -> None:
        with self.lock:
            if symbol in self.callbacks and callback in self.callbacks[symbol]:
                self.callbacks[symbol].remove(callback)

    def stop(self) -> None:
        self.running = False
        with self.lock:
            for ws in self.connections.values():
                try:
                    ws.close()
                except Exception:
                    pass
            self.connections.clear()


# ----------------- CÁC HÀM LẤY DỮ LIỆU TỪ BINANCE ----------------- #

def get_usdc_futures_symbols() -> List[str]:
    now = time.time()
    if now - _USDC_CACHE["last_update"] < _USDC_CACHE_TTL and _USDC_CACHE["symbols"]:
        return _USDC_CACHE["symbols"]

    try:
        data = _binance_request("GET", "/fapi/v1/exchangeInfo")
        symbols = []
        for s in data.get("symbols", []):
            if s.get("quoteAsset") == "USDC" and s.get("status") == "TRADING":
                symbol = s.get("symbol")
                if symbol not in EXCLUDED_SYMBOLS:
                    symbols.append(symbol)
        _USDC_CACHE["symbols"] = symbols
        _USDC_CACHE["last_update"] = now
        return symbols
    except Exception as e:
        logger.error(f"❌ Lỗi lấy danh sách coin: {e}")
        return _USDC_CACHE.get("symbols", [])


def get_24h_tickers() -> Dict[str, Dict[str, Any]]:
    now = time.time()
    if now - _TICKER_CACHE["last_update"] < _TICKER_CACHE_TTL and _TICKER_CACHE["data"]:
        return _TICKER_CACHE["data"]

    try:
        data = _binance_request("GET", "/fapi/v1/ticker/24hr")
        result = {item.get("symbol"): item for item in data}
        _TICKER_CACHE["data"] = result
        _TICKER_CACHE["last_update"] = now
        return result
    except Exception as e:
        logger.error(f"❌ Lỗi lấy 24h tickers: {e}")
        return _TICKER_CACHE.get("data", {})


def get_top_usdc_symbols_by_volume(limit: int = 20) -> List[str]:
    usdc_symbols = set(get_usdc_futures_symbols())
    tickers = get_24h_tickers()
    pairs = []
    for symbol, data in tickers.items():
        if symbol in usdc_symbols:
            try:
                vol = float(data.get("quoteVolume", 0))
                if vol >= MIN_24H_VOLUME_USDC:
                    pairs.append((symbol, vol))
            except Exception:
                continue
    pairs.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in pairs[:limit]]


def get_current_price(symbol: str) -> float:
    try:
        data = _binance_request("GET", "/fapi/v1/ticker/price", params={"symbol": symbol})
        return float(data["price"])
    except Exception:
        return 0.0


def get_balance(api_key: str, api_secret: str, asset: str = "USDC") -> float:
    data = _binance_request(
        "GET", "/fapi/v2/balance", api_key=api_key, api_secret=api_secret, signed=True
    )
    for item in data:
        if item.get("asset") == asset:
            try:
                return float(item.get("balance", 0))
            except Exception:
                return 0.0
    return 0.0


def set_leverage(api_key: str, api_secret: str, symbol: str, leverage: int) -> None:
    leverage = max(1, min(leverage, 125))
    try:
        _binance_request(
            "POST",
            "/fapi/v1/leverage",
            api_key=api_key,
            api_secret=api_secret,
            signed=True,
            params={"symbol": symbol, "leverage": leverage},
        )
        logger.info(f"[{symbol}] Đã đặt leverage = {leverage}x")
    except Exception as e:
        logger.error(f"Lỗi đặt leverage cho {symbol}: {e}")
        raise


def _futures_order(
    side: str,
    position_side: str,
    symbol: str,
    qty: float,
    api_key: str,
    api_secret: str,
    order_type: str = "MARKET",
    reduce_only: bool = False,
) -> Dict[str, Any]:
    params = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "quantity": f"{qty:.4f}",
        "positionSide": position_side,
        "reduceOnly": "true" if reduce_only else "false",
    }
    return _binance_request(
        "POST",
        "/fapi/v1/order",
        api_key=api_key,
        api_secret=api_secret,
        signed=True,
        params=params,
    )


def get_symbol_precision(symbol: str) -> Dict[str, Any]:
    data = _binance_request("GET", "/fapi/v1/exchangeInfo")
    for s in data.get("symbols", []):
        if s.get("symbol") == symbol:
            qty_precision = s.get("quantityPrecision", 3)
            price_precision = s.get("pricePrecision", 2)
            filters = s.get("filters", [])
            step_size = 0.0
            tick_size = 0.0
            for f in filters:
                if f.get("filterType") == "LOT_SIZE":
                    step_size = float(f.get("stepSize", 0))
                if f.get("filterType") == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0))
            return {
                "quantityPrecision": qty_precision,
                "pricePrecision": price_precision,
                "stepSize": step_size,
                "tickSize": tick_size,
            }
    return {
        "quantityPrecision": 3,
        "pricePrecision": 2,
        "stepSize": 0.001,
        "tickSize": 0.01,
    }


def round_quantity(symbol: str, qty: float) -> float:
    info = get_symbol_precision(symbol)
    step = info.get("stepSize", 0.001)
    if step <= 0:
        return round(qty, info.get("quantityPrecision", 3))
    return (qty // step) * step


def get_klines(symbol: str, interval: str = "1m", limit: int = 60) -> List[Dict[str, Any]]:
    """
    Lấy dữ liệu nến (OHLC) để vẽ biểu đồ.
    limit: 20–60 tuỳ frontend request.
    """
    limit = max(20, min(limit, 100))
    data = _binance_request(
        "GET",
        "/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    result = []
    for k in data:
        result.append(
            {
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )
    return result


# ----------------- COIN MANAGER & QUEUE ----------------- #

class CoinManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.symbol_to_bot: Dict[str, str] = {}
        self.bot_to_symbols: Dict[str, Set[str]] = defaultdict(set)

    def acquire_symbol(self, bot_id: str, symbol: str) -> bool:
        with self.lock:
            if symbol in self.symbol_to_bot:
                return False
            self.symbol_to_bot[symbol] = bot_id
            self.bot_to_symbols[bot_id].add(symbol)
            return True

    def release_symbol(self, bot_id: str, symbol: str) -> None:
        with self.lock:
            if self.symbol_to_bot.get(symbol) == bot_id:
                del self.symbol_to_bot[symbol]
            if symbol in self.bot_to_symbols.get(bot_id, set()):
                self.bot_to_symbols[bot_id].remove(symbol)

    def release_all_for_bot(self, bot_id: str) -> None:
        with self.lock:
            symbols = list(self.bot_to_symbols.get(bot_id, set()))
            for sym in symbols:
                if self.symbol_to_bot.get(sym) == bot_id:
                    del self.symbol_to_bot[sym]
            self.bot_to_symbols[bot_id].clear()

    def is_symbol_free(self, symbol: str) -> bool:
        with self.lock:
            return symbol not in self.symbol_to_bot


class BotExecutionCoordinator:
    """
    FIFO: tại 1 thời điểm chỉ 1 bot được quyền "tìm coin".
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.current_bot_id: Optional[str] = None
        self.queue: deque = deque()
        self.bot_status: Dict[str, str] = {}

    def request_turn(self, bot_id: str) -> None:
        with self.lock:
            if bot_id not in self.bot_status:
                self.bot_status[bot_id] = "idle"

            self.queue.append(bot_id)
            self.bot_status[bot_id] = "waiting"

            while True:
                if self.current_bot_id is None and self.queue and self.queue[0] == bot_id:
                    self.current_bot_id = bot_id
                    self.queue.popleft()
                    self.bot_status[bot_id] = "active"
                    return
                self.condition.wait()

    def release_turn(self, bot_id: str) -> None:
        with self.lock:
            if self.current_bot_id == bot_id:
                self.current_bot_id = None
                self.bot_status[bot_id] = "idle"
                self.condition.notify_all()

    def unregister_bot(self, bot_id: str) -> None:
        with self.lock:
            self.queue = deque([b for b in self.queue if b != bot_id])
            if self.current_bot_id == bot_id:
                self.current_bot_id = None
                self.condition.notify_all()
            self.bot_status.pop(bot_id, None)

    def get_queue_info(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "current_bot_id": self.current_bot_id,
                "queue": list(self.queue),
                "bot_status": dict(self.bot_status),
            }


# ----------------- BASE BOT ----------------- #

class BaseBot:
    def __init__(
        self,
        symbol: str,
        lev: int,
        percent: float,
        tp: Optional[float],
        sl: Optional[float],
        roi_trigger: Optional[float],
        ws_manager: WebSocketManager,
        api_key: str,
        api_secret: str,
        telegram_bot_token: Optional[str],
        telegram_chat_id: Optional[str],
        strategy_name: str,
        config_key: Optional[str] = None,
        bot_id: Optional[str] = None,
        coin_manager: Optional[CoinManager] = None,
        symbol_locks: Optional[Dict[str, threading.Lock]] = None,
        max_coins: int = 1,
        bot_coordinator: Optional[BotExecutionCoordinator] = None,
        event_bus: Optional[FrontendEventBus] = None,
    ):
        self.symbol = symbol
        self.lev = lev
        self.percent = percent
        self.tp = tp
        self.sl = sl
        self.roi_trigger = roi_trigger

        self.ws_manager = ws_manager
        self.api_key = api_key
        self.api_secret = api_secret
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.strategy_name = strategy_name
        self.config_key = config_key
        self.bot_id = bot_id or f"{strategy_name}_{int(time.time())}"

        self.event_bus = event_bus or GLOBAL_EVENT_BUS

        self.coin_manager = coin_manager or CoinManager()
        self.symbol_locks = symbol_locks or defaultdict(threading.Lock)
        self.max_coins = max_coins
        self.bot_coordinator = bot_coordinator

        self.symbol_data: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "position_open": False,
                "entry": 0.0,
                "qty": 0.0,
                "side": None,
                "last_price": 0.0,
                "last_update": 0.0,
                "average_down_count": 0,
                "last_average_down_time": 0.0,
                "status": "idle",
                "high_water_mark_roi": 0.0,
                "current_price": 0.0,
            }
        )
        self.active_symbols: Set[str] = set()

        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.status = "stopped"

    # ---- LOG ---- #

    def log(self, message: str, level: str = "info") -> None:
        if self.event_bus:
            self.event_bus.publish(
                {
                    "type": "bot_log",
                    "bot_id": self.bot_id,
                    "strategy": self.strategy_name,
                    "level": level,
                    "message": message,
                    "symbols": list(self.active_symbols),
                }
            )

        important_keywords = ["❌", "✅", "⛔", "💰", "📈", "📊", "🎯", "🛡️", "🔴", "🟢", "⚠️", "🚫"]
        log_text = f"[{self.bot_id}] {message}"

        if any(k in message for k in important_keywords):
            logger.warning(log_text)
        else:
            logger.info(log_text)

        if self.telegram_bot_token and self.telegram_chat_id:
            try:
                send_telegram(
                    f"<b>{self.bot_id}</b>: {message}",
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id,
                )
            except Exception as e:
                logger.error(f"Telegram log error: {e}")

    # ---- PRICE UPDATE ---- #

    def _handle_price_update(self, price: float, symbol: str) -> None:
        if symbol in self.symbol_data:
            self.symbol_data[symbol]["current_price"] = price

        if self.event_bus:
            self.event_bus.publish(
                {
                    "type": "price",
                    "symbol": symbol,
                    "price": price,
                    "bot_id": self.bot_id,
                }
            )

    def _subscribe_symbol(self, symbol: str) -> None:
        self.ws_manager.subscribe_price(symbol, self._handle_price_update)

    def _unsubscribe_symbol(self, symbol: str) -> None:
        self.ws_manager.unsubscribe_price(symbol, self._handle_price_update)

    # ---- VỊ THẾ ---- #

    def open_position(self, symbol: str, side: str) -> None:
        with self.symbol_locks[symbol]:
            data = self.symbol_data[symbol]
            if data["position_open"]:
                self.log(f"⛔ {symbol}: Đã có vị thế", "warning")
                return

            balance = get_balance(self.api_key, self.api_secret)
            if balance <= 0:
                self.log("❌ Không đủ số dư", "error")
                return

            price = get_current_price(symbol)
            if price <= 0:
                self.log(f"❌ Không lấy được giá {symbol}", "error")
                return

            trade_value = balance * (self.percent / 100.0) * self.lev
            qty = trade_value / price
            qty = round_quantity(symbol, qty)
            if qty <= 0:
                self.log(f"❌ Quantity {qty} quá nhỏ cho {symbol}", "error")
                return

            try:
                set_leverage(self.api_key, self.api_secret, symbol, self.lev)
            except Exception:
                self.log(f"⚠️ Không đặt được leverage cho {symbol}", "warning")

            side_map = {"LONG": ("BUY", "LONG"), "SHORT": ("SELL", "SHORT")}
            if side not in side_map:
                self.log(f"❌ side không hợp lệ: {side}", "error")
                return

            order_side, position_side = side_map[side]
            try:
                _futures_order(
                    side=order_side,
                    position_side=position_side,
                    symbol=symbol,
                    qty=qty,
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    reduce_only=False,
                )
                data["position_open"] = True
                data["entry"] = price
                data["qty"] = qty
                data["side"] = side
                data["status"] = "open"
                data["average_down_count"] = 0
                data["last_average_down_time"] = 0
                data["high_water_mark_roi"] = 0.0
                self.active_symbols.add(symbol)

                self._subscribe_symbol(symbol)

                self.log(
                    f"✅ Mở {side} {symbol} | Giá: {price:.4f} | QTY: {qty} | Vốn: {self.percent}% | Đòn bẩy: {self.lev}x"
                )
            except Exception as e:
                self.log(f"❌ Lỗi mở vị thế {side} {symbol}: {e}", "error")

    def close_position(self, symbol: str) -> None:
        with self.symbol_locks[symbol]:
            data = self.symbol_data[symbol]
            if not data["position_open"]:
                return

            side = data["side"]
            if side not in ("LONG", "SHORT"):
                self.log(f"⚠️ close_position: side không hợp lệ {side}", "warning")
                return

            qty = data["qty"]
            if qty <= 0:
                data["position_open"] = False
                data["status"] = "closed"
                self.active_symbols.discard(symbol)
                self._unsubscribe_symbol(symbol)
                return

            side_map = {"LONG": ("SELL", "LONG"), "SHORT": ("BUY", "SHORT")}
            order_side, position_side = side_map[side]

            try:
                current_price = get_current_price(symbol)
                entry = data["entry"] or current_price
                if entry > 0:
                    if side == "LONG":
                        roi = (current_price - entry) / entry * self.lev * 100
                    else:
                        roi = (entry - current_price) / entry * self.lev * 100
                else:
                    roi = 0.0
            except Exception:
                roi = 0.0

            try:
                _futures_order(
                    side=order_side,
                    position_side=position_side,
                    symbol=symbol,
                    qty=qty,
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    reduce_only=True,
                )
                data["position_open"] = False
                data["status"] = "closed"
                self.active_symbols.discard(symbol)
                self._unsubscribe_symbol(symbol)
                self.log(
                    f"💰 Đóng vị thế {side} {symbol} | QTY: {qty} | ROI xấp xỉ: {roi:.2f}%"
                )
            except Exception as e:
                self.log(f"❌ Lỗi đóng vị thế {side} {symbol}: {e}", "error")

    # ---- NHỒI LỆNH & TP/SL ---- #

    def check_averaging_down(self, symbol: str) -> None:
        with self.symbol_locks[symbol]:
            data = self.symbol_data[symbol]
            if (
                not data["position_open"]
                or not data["entry"]
                or data["average_down_count"] >= 7
            ):
                return

            current_time = time.time()
            if current_time - data["last_average_down_time"] < 60:
                return

            current_price = get_current_price(symbol)
            if current_price <= 0:
                return

            entry = data["entry"]
            side = data["side"]
            if side == "LONG":
                roi = (current_price - entry) / entry * self.lev * 100
            elif side == "SHORT":
                roi = (entry - current_price) / entry * self.lev * 100
            else:
                return

            if roi > -5:
                return

            balance = get_balance(self.api_key, self.api_secret)
            if balance <= 0:
                return

            trade_value = balance * (self.percent / 100.0) * self.lev * 0.5
            extra_qty = trade_value / current_price
            extra_qty = round_quantity(symbol, extra_qty)
            if extra_qty <= 0:
                return

            side_map = {"LONG": ("BUY", "LONG"), "SHORT": ("SELL", "SHORT")}
            order_side, position_side = side_map[side]

            try:
                _futures_order(
                    side=order_side,
                    position_side=position_side,
                    symbol=symbol,
                    qty=extra_qty,
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    reduce_only=False,
                )
                old_qty = data["qty"]
                old_entry = data["entry"]
                new_total_qty = old_qty + extra_qty
                new_entry = (old_entry * old_qty + current_price * extra_qty) / new_total_qty

                data["qty"] = new_total_qty
                data["entry"] = new_entry
                data["average_down_count"] += 1
                data["last_average_down_time"] = current_time

                self.log(
                    f"📉 Nhồi lệnh {side} {symbol}: thêm {extra_qty}, entry mới: {new_entry:.4f}, "
                    f"ROI hiện tại: {roi:.2f}%, lần nhồi: {data['average_down_count']}"
                )
            except Exception as e:
                self.log(f"❌ Lỗi nhồi lệnh {side} {symbol}: {e}", "error")

    def check_tp_sl_and_reverse(self, symbol: str) -> None:
        with self.symbol_locks[symbol]:
            data = self.symbol_data[symbol]
            if not data["position_open"] or not data["entry"]:
                return

            current_price = get_current_price(symbol)
            if current_price <= 0:
                return

            entry = data["entry"]
            side = data["side"]
            if side == "LONG":
                roi = (current_price - entry) / entry * self.lev * 100
            else:
                roi = (entry - current_price) / entry * self.lev * 100

            if roi > data["high_water_mark_roi"]:
                data["high_water_mark_roi"] = roi

            if self.sl is not None and roi <= -abs(self.sl):
                self.log(
                    f"🛑 {symbol}: ROI={roi:.2f}% <= SL={-abs(self.sl)}%, cắt lỗ",
                    "warning",
                )
                self.close_position(symbol)
                return

            if self.tp is not None and roi >= self.tp:
                self.log(
                    f"🎯 {symbol}: ROI={roi:.2f}% >= TP={self.tp}%, chốt lời",
                    "info",
                )
                self.close_position(symbol)

    # ---- LOOP ---- #

    def find_and_set_coin(self) -> None:
        raise NotImplementedError

    def run(self) -> None:
        self.running = True
        self.status = "running"
        self.log("🟢 Bot bắt đầu chạy")

        try:
            while self.running:
                for symbol in list(self.active_symbols):
                    self.check_tp_sl_and_reverse(symbol)
                    self.check_averaging_down(symbol)

                if len(self.active_symbols) < self.max_coins:
                    try:
                        self.find_and_set_coin()
                    except Exception as e:
                        self.log(
                            f"❌ Lỗi find_and_set_coin: {e}\n{traceback.format_exc()}",
                            "error",
                        )

                time.sleep(3)
        except Exception as e:
            self.status = "error"
            self.log(f"❌ Lỗi vòng lặp run: {e}\n{traceback.format_exc()}", "error")
        finally:
            self.running = False
            self.status = "stopped"
            self.log("🔴 Bot dừng chạy")

    def start(self) -> None:
        if self.running:
            self.log("⚠️ Bot đã chạy rồi", "warning")
            return
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        self.log("⏹ Dừng bot, đóng toàn bộ vị thế")
        try:
            for symbol in list(self.active_symbols):
                self.close_position(symbol)
            if self.coin_manager:
                self.coin_manager.release_all_for_bot(self.bot_id)
        finally:
            if self.bot_coordinator:
                self.bot_coordinator.unregister_bot(self.bot_id)
            self.status = "stopped"

    def get_summary(self) -> Dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "strategy": self.strategy_name,
            "status": self.status,
            "leverage": self.lev,
            "percent": self.percent,
            "tp": self.tp,
            "sl": self.sl,
            "roi_trigger": self.roi_trigger,
            "active_symbols": list(self.active_symbols),
        }


# ----------------- GLOBAL MARKET BOT ----------------- #

class GlobalMarketBot(BaseBot):
    def __init__(
        self,
        symbol: str,
        lev: int,
        percent: float,
        tp: Optional[float],
        sl: Optional[float],
        roi_trigger: Optional[float],
        ws_manager: WebSocketManager,
        api_key: str,
        api_secret: str,
        telegram_bot_token: Optional[str],
        telegram_chat_id: Optional[str],
        bot_id: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(
            symbol,
            lev,
            percent,
            tp,
            sl,
            roi_trigger,
            ws_manager,
            api_key,
            api_secret,
            telegram_bot_token,
            telegram_chat_id,
            "RSI-Volume-System-Queue",
            bot_id=bot_id,
            **kwargs,
        )

    def _simple_signal(self, symbol: str) -> Optional[str]:
        tickers = get_24h_tickers()
        t = tickers.get(symbol)
        if not t:
            return None
        try:
            change_percent = float(t.get("priceChangePercent", 0))
        except Exception:
            return None

        if change_percent > 3:
            return "LONG"
        elif change_percent < -3:
            return "SHORT"
        return None

    def find_and_set_coin(self) -> None:
        if self.bot_coordinator is None:
            self._find_coin_no_queue()
            return

        if len(self.active_symbols) >= self.max_coins:
            return

        bot_id = self.bot_id
        self.log("🔄 Yêu cầu quyền tìm coin (queue FIFO)")
        self.bot_coordinator.request_turn(bot_id)
        self.log("🟢 Đã được quyền tìm coin, bắt đầu quét thị trường")

        try:
            top_symbols = get_top_usdc_symbols_by_volume(limit=30)
            random.shuffle(top_symbols)

            for sym in top_symbols:
                if len(self.active_symbols) >= self.max_coins:
                    break
                if not self.coin_manager.is_symbol_free(sym):
                    continue

                signal = self._simple_signal(sym)
                if not signal:
                    continue

                if not self.coin_manager.acquire_symbol(bot_id, sym):
                    continue

                self.open_position(sym, signal)
                if sym in self.active_symbols:
                    self.log(
                        f"🎯 Bot chọn coin {sym} với tín hiệu {signal}. "
                        f"Coin đang chạy: {len(self.active_symbols)}/{self.max_coins}"
                    )
        finally:
            self.bot_coordinator.release_turn(bot_id)
            self.log("🔁 Đã trả quyền tìm coin cho bot tiếp theo")

    def _find_coin_no_queue(self) -> None:
        if len(self.active_symbols) >= self.max_coins:
            return

        top_symbols = get_top_usdc_symbols_by_volume(limit=30)
        random.shuffle(top_symbols)

        for sym in top_symbols:
            if len(self.active_symbols) >= self.max_coins:
                break
            if not self.coin_manager.is_symbol_free(sym):
                continue

            signal = self._simple_signal(sym)
            if not signal:
                continue

            if not self.coin_manager.acquire_symbol(self.bot_id, sym):
                continue

            self.open_position(sym, signal)
            if sym in self.active_symbols:
                self.log(
                    f"🎯 [NoQueue] Bot chọn coin {sym} với tín hiệu {signal}. "
                    f"Coin đang chạy: {len(self.active_symbols)}/{self.max_coins}"
                )


# ----------------- BOT MANAGER ----------------- #

class BotManager:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        event_bus: Optional[FrontendEventBus] = None,
    ):
        self.ws_manager = WebSocketManager()
        self.bots: Dict[str, BaseBot] = {}
        self.running = True
        self.start_time = time.time()
        self.user_states: Dict[str, Dict[str, Any]] = {}

        self.api_key = api_key
        self.api_secret = api_secret
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id

        self.event_bus = event_bus or GLOBAL_EVENT_BUS

        self.bot_coordinator = BotExecutionCoordinator()
        self.coin_manager = CoinManager()
        self.symbol_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)

        if api_key and api_secret:
            self._verify_api_connection()
            self.log("🟢 HỆ THỐNG BOT QUEUE ĐÃ KHỞI ĐỘNG - CƠ CHẾ FIFO")
        else:
            self.log("⚡ BotManager khởi động ở mode chưa cấu hình API")

    # ---- LOG ---- #

    def log(self, message: str, level: str = "info") -> None:
        event = {
            "type": "system_log",
            "level": level,
            "message": message,
            "bot_count": len(self.bots),
        }
        if self.event_bus:
            self.event_bus.publish(event)

        important_keywords = ["❌", "✅", "⛔", "💰", "📈", "📊", "🎯", "🛡️", "🔴", "🟢", "⚠️", "🚫"]
        log_text = f"[SYSTEM] {message}"

        if any(k in message for k in important_keywords):
            logger.warning(log_text)
        else:
            logger.info(log_text)

        if self.telegram_bot_token and self.telegram_chat_id:
            try:
                send_telegram(
                    f"<b>SYSTEM</b>: {message}",
                    chat_id=self.telegram_chat_id,
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id,
                )
            except Exception as e:
                logger.error(f"Telegram system log error: {e}")

    # ---- KẾT NỐI BINANCE ---- #

    def _verify_api_connection(self) -> None:
        try:
            balance = get_balance(self.api_key, self.api_secret)
            self.log(f"✅ Kết nối Binance thành công! Số dư: {balance} USDC")
        except Exception as e:
            self.log(f"❌ Lỗi kết nối Binance: {e}", "error")
            raise

    def set_config(
        self,
        api_key: str,
        api_secret: str,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        restart_bots: bool = True,
    ) -> None:
        self.log("⚙️ Nhận cấu hình mới từ backend (set_config)")

        self.api_key = api_key
        self.api_secret = api_secret
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id

        if restart_bots:
            self.log("🔄 Dừng toàn bộ bot để áp dụng cấu hình mới")
            self.stop_all()
            self.bots = {}
            try:
                self.ws_manager.stop()
            except Exception:
                pass
            self.ws_manager = WebSocketManager()

        self._verify_api_connection()

    # ---- QUẢN LÝ BOT ---- #

    def add_bot(
        self,
        strategy_name: str,
        lev: int,
        percent: float,
        tp: Optional[float],
        sl: Optional[float],
        roi_trigger: Optional[float],
        max_coins: int = 1,
    ) -> str:
        bot_id = f"DYNAMIC_{strategy_name}_{int(time.time())}_{len(self.bots)}"
        bot_class = GlobalMarketBot  # tạm thời 1 strategy
        bot = bot_class(
            symbol="AUTO",
            lev=lev,
            percent=percent,
            tp=tp,
            sl=sl,
            roi_trigger=roi_trigger,
            ws_manager=self.ws_manager,
            api_key=self.api_key,
            api_secret=self.api_secret,
            telegram_bot_token=self.telegram_bot_token,
            telegram_chat_id=self.telegram_chat_id,
            coin_manager=self.coin_manager,
            symbol_locks=self.symbol_locks,
            bot_coordinator=self.bot_coordinator,
            bot_id=bot_id,
            max_coins=max_coins,
            event_bus=self.event_bus,
        )
        self.bots[bot_id] = bot
        bot.start()
        self.log(
            f"🟢 Bot {strategy_name} khởi động | {max_coins} coin | Đòn bẩy: {lev}x | "
            f"Vốn: {percent}% | TP/SL: {tp}%/{sl}% | ROI Trigger: {roi_trigger}%"
        )
        return bot_id

    def stop_bot(self, bot_id: str) -> None:
        bot = self.bots.get(bot_id)
        if not bot:
            self.log(f"⚠️ stop_bot: không tìm thấy bot_id = {bot_id}", "warning")
            return
        bot.stop()
        self.coin_manager.release_all_for_bot(bot_id)
        self.bot_coordinator.unregister_bot(bot_id)
        self.log(f"⏹ Đã dừng bot {bot_id}")
        del self.bots[bot_id]

    def stop_all(self) -> None:
        for bot_id in list(self.bots.keys()):
            self.stop_bot(bot_id)

    # ---- STATE & CHART ---- #

    def get_state(self) -> Dict[str, Any]:
        bots_info: Dict[str, Any] = {}
        for bot_id, bot in self.bots.items():
            bot_data = {
                "bot_id": bot_id,
                "strategy": bot.strategy_name,
                "status": bot.status,
                "leverage": bot.lev,
                "percent": bot.percent,
                "tp": bot.tp,
                "sl": bot.sl,
                "roi_trigger": bot.roi_trigger,
                "active_symbols": list(bot.active_symbols),
                "symbol_data": {},
            }
            for sym, sd in bot.symbol_data.items():
                bot_data["symbol_data"][sym] = {
                    "status": sd.get("status"),
                    "side": sd.get("side"),
                    "qty": sd.get("qty"),
                    "entry": sd.get("entry"),
                    "current_price": sd.get("current_price"),
                    "position_open": sd.get("position_open"),
                    "high_water_mark_roi": sd.get("high_water_mark_roi"),
                }
            bots_info[bot_id] = bot_data

        queue_info = self.bot_coordinator.get_queue_info()
        balance = None
        try:
            if self.api_key and self.api_secret:
                balance = get_balance(self.api_key, self.api_secret)
        except Exception:
            balance = None

        return {
            "uptime_seconds": time.time() - self.start_time,
            "bot_count": len(self.bots),
            "balance": balance,
            "queue": queue_info,
            "ws_symbols": list(self.ws_manager.connections.keys()),
            "bots": bots_info,
        }

    def get_chart_data(self, symbol: str, limit: int = 60) -> Dict[str, Any]:
        """
        Dữ liệu cho biểu đồ nến: 20–60 nến 1m gần nhất.
        """
        klines = get_klines(symbol, interval="1m", limit=limit)
        return {
            "symbol": symbol,
            "interval": "1m",
            "candles": klines,
        }


def create_bot_manager(
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    telegram_bot_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    event_bus: Optional[FrontendEventBus] = None,
) -> BotManager:
    if event_bus is None:
        event_bus = GLOBAL_EVENT_BUS
    manager = BotManager(
        api_key=api_key,
        api_secret=api_secret,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        event_bus=event_bus,
    )
    return manager
