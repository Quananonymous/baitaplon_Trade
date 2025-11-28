# trading_bot_lib_complete.py
import json
import hmac
import hashlib
import time
import threading
import urllib.request
import urllib.parse
import numpy as np
import websocket
import logging
import requests
import os
import math
import traceback
import random
import queue
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import ssl

# ========== CONFIGURATION & CONSTANTS ==========
_BINANCE_LAST_REQUEST_TIME = 0
_BINANCE_RATE_LOCK = threading.Lock()
_BINANCE_MIN_INTERVAL = 0.1

_USDC_CACHE = {"pairs": [], "last_update": 0}
_USDC_CACHE_TTL = 300

_LEVERAGE_CACHE = {"data": {}, "last_update": 0}
_LEVERAGE_CACHE_TTL = 300

_SYMBOL_BLACKLIST = {'BTCUSDC', 'ETHUSDC'}

# ========== UTILITY FUNCTIONS ==========
def setup_logging():
    logging.basicConfig(
        level=logging.WARNING,
        format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
        handlers=[logging.StreamHandler(), logging.FileHandler('bot_errors.log')]
    )
    return logging.getLogger()

logger = setup_logging()

def escape_html(text):
    if not text: return text
    return (text.replace('&', '&amp;').replace('<', '&lt;')
                .replace('>', '&gt;').replace('"', '&quot;'))

def send_telegram(message, chat_id=None, reply_markup=None, bot_token=None, default_chat_id=None):
    if not bot_token or not (chat_id or default_chat_id):
        return
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    safe_message = escape_html(message)
    
    payload = {"chat_id": chat_id or default_chat_id, "text": safe_message, "parse_mode": "HTML"}
    if reply_markup: payload["reply_markup"] = json.dumps(reply_markup)
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code != 200:
            logger.error(f"Telegram error ({response.status_code}): {response.text}")
    except Exception as e:
        logger.error(f"Telegram connection error: {str(e)}")

# ========== KEYBOARD CREATION FUNCTIONS ==========
def create_main_menu():
    return {
        "keyboard": [
            [{"text": "📊 Danh sách Bot"}, {"text": "📊 Thống kê"}],
            [{"text": "➕ Thêm Bot"}, {"text": "⛔ Dừng Bot"}],
            [{"text": "⛔ Quản lý Coin"}, {"text": "📈 Vị thế"}],
            [{"text": "💰 Số dư"}, {"text": "⚙️ Cấu hình"}],
            [{"text": "🎯 Chiến lược"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

def create_cancel_keyboard():
    return {"keyboard": [[{"text": "❌ Hủy bỏ"}]], "resize_keyboard": True, "one_time_keyboard": True}

def create_bot_count_keyboard():
    return {
        "keyboard": [[{"text": "1"}, {"text": "2"}, {"text": "3"}], [{"text": "5"}, {"text": "10"}], [{"text": "❌ Hủy bỏ"}]],
        "resize_keyboard": True, "one_time_keyboard": True
    }

def create_bot_mode_keyboard():
    return {
        "keyboard": [
            [{"text": "🤖 Bot Tĩnh - Coin cụ thể"}, {"text": "🔄 Bot Động - Tự tìm coin"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True, "one_time_keyboard": True
    }

def create_symbols_keyboard():
    try:
        symbols = get_all_usdc_pairs(limit=12) or ["BNBUSDC", "ADAUSDC", "DOGEUSDC", "XRPUSDC", "DOTUSDC", "LINKUSDC", "SOLUSDC", "MATICUSDC"]
    except:
        symbols = ["BNBUSDC", "ADAUSDC", "DOGEUSDC", "XRPUSDC", "DOTUSDC", "LINKUSDC", "SOLUSDC", "MATICUSDC"]
    
    keyboard = []
    row = []
    for symbol in symbols:
        row.append({"text": symbol})
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    keyboard.append([{"text": "❌ Hủy bỏ"}])
    
    return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": True}

def create_leverage_keyboard():
    leverages = ["3", "5", "10", "15", "20", "25", "50", "75", "100"]
    keyboard = []
    row = []
    for lev in leverages:
        row.append({"text": f"{lev}x"})
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    keyboard.append([{"text": "❌ Hủy bỏ"}])
    return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": True}

def create_percent_keyboard():
    return {
        "keyboard": [
            [{"text": "1"}, {"text": "3"}, {"text": "5"}, {"text": "10"}],
            [{"text": "15"}, {"text": "20"}, {"text": "25"}, {"text": "50"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True, "one_time_keyboard": True
    }

def create_tp_keyboard():
    return {
        "keyboard": [
            [{"text": "50"}, {"text": "100"}, {"text": "200"}],
            [{"text": "300"}, {"text": "500"}, {"text": "1000"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True, "one_time_keyboard": True
    }

def create_sl_keyboard():
    return {
        "keyboard": [
            [{"text": "0"}, {"text": "50"}, {"text": "100"}],
            [{"text": "150"}, {"text": "200"}, {"text": "500"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True, "one_time_keyboard": True
    }

def create_roi_trigger_keyboard():
    return {
        "keyboard": [
            [{"text": "30"}, {"text": "50"}, {"text": "100"}],
            [{"text": "150"}, {"text": "200"}, {"text": "300"}],
            [{"text": "❌ Tắt tính năng"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True, "one_time_keyboard": True
    }

# ========== BINANCE API FUNCTIONS ==========
def _wait_for_rate_limit():
    global _BINANCE_LAST_REQUEST_TIME
    with _BINANCE_RATE_LOCK:
        now = time.time()
        delta = now - _BINANCE_LAST_REQUEST_TIME
        if delta < _BINANCE_MIN_INTERVAL:
            time.sleep(_BINANCE_MIN_INTERVAL - delta)
        _BINANCE_LAST_REQUEST_TIME = time.time()

def sign(query, api_secret):
    try:
        return hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    except Exception as e:
        logger.error(f"Sign error: {str(e)}")
        return ""

def binance_api_request(url, method='GET', params=None, headers=None):
    max_retries = 2
    base_url = url

    for attempt in range(max_retries):
        try:
            _wait_for_rate_limit()
            url = base_url

            if headers is None: headers = {}
            if 'User-Agent' not in headers:
                headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

            if method.upper() == 'GET':
                if params:
                    query = urllib.parse.urlencode(params)
                    url = f"{url}?{query}"
                req = urllib.request.Request(url, headers=headers)
            else:
                data = urllib.parse.urlencode(params).encode() if params else None
                req = urllib.request.Request(url, data=data, headers=headers, method=method)

            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status == 200:
                    return json.loads(response.read().decode())
                else:
                    error_content = response.read().decode()
                    logger.error(f"API error ({response.status}): {error_content}")
                    if response.status == 401: return None
                    if response.status == 429:
                        sleep_time = 2 ** attempt
                        logger.warning(f"⚠️ 429 Too Many Requests, sleeping {sleep_time}s")
                        time.sleep(sleep_time)
                    elif response.status >= 500: time.sleep(0.5)
                    continue

        except urllib.error.HTTPError as e:
            if e.code == 451:
                logger.error("❌ Error 451: Access blocked - Check VPN/proxy")
                return None
            else: logger.error(f"HTTP error ({e.code}): {e.reason}")

            if e.code == 401: return None
            if e.code == 429:
                sleep_time = 2 ** attempt
                logger.warning(f"⚠️ HTTP 429 Too Many Requests, sleeping {sleep_time}s")
                time.sleep(sleep_time)
            elif e.code >= 500: time.sleep(0.5)
            continue

        except Exception as e:
            logger.error(f"API connection error (attempt {attempt + 1}): {str(e)}")
            time.sleep(0.5)

    logger.error(f"Failed API request after {max_retries} attempts")
    return None

def get_all_usdc_pairs(limit=100):
    global _USDC_CACHE
    try:
        now = time.time()
        if _USDC_CACHE["pairs"] and (now - _USDC_CACHE["last_update"] < _USDC_CACHE_TTL):
            return _USDC_CACHE["pairs"][:limit]

        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        data = binance_api_request(url)
        if not data: return []

        usdc_pairs = []
        for symbol_info in data.get('symbols', []):
            symbol = symbol_info.get('symbol', '')
            if (symbol.endswith('USDC') and symbol_info.get('status') == 'TRADING' 
                and symbol not in _SYMBOL_BLACKLIST):
                usdc_pairs.append(symbol)

        _USDC_CACHE["pairs"] = usdc_pairs
        _USDC_CACHE["last_update"] = now
        logger.info(f"✅ Retrieved {len(usdc_pairs)} USDC coins (excluding BTC/ETH)")
        return usdc_pairs[:limit]

    except Exception as e:
        logger.error(f"❌ Error getting coin list: {str(e)}")
        return []

def get_max_leverage(symbol, api_key, api_secret):
    global _LEVERAGE_CACHE
    try:
        symbol = symbol.upper()
        current_time = time.time()
        
        if (symbol in _LEVERAGE_CACHE["data"] and 
            current_time - _LEVERAGE_CACHE["last_update"] < _LEVERAGE_CACHE_TTL):
            return _LEVERAGE_CACHE["data"][symbol]
        
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        data = binance_api_request(url)
        if not data: return 100
        
        for s in data['symbols']:
            if s['symbol'] == symbol:
                for f in s['filters']:
                    if f['filterType'] == 'LEVERAGE' and 'maxLeverage' in f:
                        leverage = int(f['maxLeverage'])
                        _LEVERAGE_CACHE["data"][symbol] = leverage
                        _LEVERAGE_CACHE["last_update"] = current_time
                        return leverage
        return 100
    except Exception as e:
        logger.error(f"Leverage error {symbol}: {str(e)}")
        return 100

def get_step_size(symbol, api_key, api_secret):
    if not symbol: return 0.001
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    try:
        data = binance_api_request(url)
        if not data: return 0.001
        for s in data['symbols']:
            if s['symbol'] == symbol.upper():
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        return float(f['stepSize'])
    except Exception as e:
        logger.error(f"Step size error: {str(e)}")
    return 0.001

def set_leverage(symbol, lev, api_key, api_secret):
    if not symbol: return False
    try:
        ts = int(time.time() * 1000)
        params = {"symbol": symbol.upper(), "leverage": lev, "timestamp": ts}
        query = urllib.parse.urlencode(params)
        sig = sign(query, api_secret)
        url = f"https://fapi.binance.com/fapi/v1/leverage?{query}&signature={sig}"
        headers = {'X-MBX-APIKEY': api_key}
        
        response = binance_api_request(url, method='POST', headers=headers)
        return bool(response and 'leverage' in response)
    except Exception as e:
        logger.error(f"Leverage setting error: {str(e)}")
        return False

def get_balance(api_key, api_secret):
    try:
        ts = int(time.time() * 1000)
        params = {"timestamp": ts}
        query = urllib.parse.urlencode(params)
        sig = sign(query, api_secret)
        url = f"https://fapi.binance.com/fapi/v2/account?{query}&signature={sig}"
        headers = {'X-MBX-APIKEY': api_key}
        
        data = binance_api_request(url, headers=headers)
        if not data: return None
            
        for asset in data['assets']:
            if asset['asset'] == 'USDC':
                available_balance = float(asset['availableBalance'])
                logger.info(f"💰 Balance - Available: {available_balance:.2f} USDC")
                return available_balance
        return 0
    except Exception as e:
        logger.error(f"Balance error: {str(e)}")
        return None

def place_order(symbol, side, qty, api_key, api_secret):
    if not symbol: return None
    try:
        ts = int(time.time() * 1000)
        params = {
            "symbol": symbol.upper(),
            "side": side,
            "type": "MARKET",
            "quantity": qty,
            "timestamp": ts
        }
        query = urllib.parse.urlencode(params)
        sig = sign(query, api_secret)
        url = f"https://fapi.binance.com/fapi/v1/order?{query}&signature={sig}"
        headers = {'X-MBX-APIKEY': api_key}
        
        return binance_api_request(url, method='POST', headers=headers)
    except Exception as e:
        logger.error(f"Order error: {str(e)}")
        return None

def cancel_all_orders(symbol, api_key, api_secret):
    if not symbol: return False
    try:
        ts = int(time.time() * 1000)
        params = {"symbol": symbol.upper(), "timestamp": ts}
        query = urllib.parse.urlencode(params)
        sig = sign(query, api_secret)
        url = f"https://fapi.binance.com/fapi/v1/allOpenOrders?{query}&signature={sig}"
        headers = {'X-MBX-APIKEY': api_key}
        
        binance_api_request(url, method='DELETE', headers=headers)
        return True
    except Exception as e:
        logger.error(f"Cancel orders error: {str(e)}")
        return False

def get_current_price(symbol):
    if not symbol: return 0
    try:
        url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol.upper()}"
        data = binance_api_request(url)
        if data and 'price' in data:
            price = float(data['price'])
            return price if price > 0 else 0
        return 0
    except Exception as e:
        logger.error(f"Price error {symbol}: {str(e)}")
        return 0

def get_positions(symbol=None, api_key=None, api_secret=None):
    try:
        ts = int(time.time() * 1000)
        params = {"timestamp": ts}
        if symbol: params["symbol"] = symbol.upper()
        query = urllib.parse.urlencode(params)
        sig = sign(query, api_secret)
        url = f"https://fapi.binance.com/fapi/v2/positionRisk?{query}&signature={sig}"
        headers = {'X-MBX-APIKEY': api_key}
        
        positions = binance_api_request(url, headers=headers)
        if not positions: return []
        if symbol:
            for pos in positions:
                if pos['symbol'] == symbol.upper():
                    return [pos]
        return positions
    except Exception as e:
        logger.error(f"Positions error: {str(e)}")
        return []

# ========== CORE MANAGEMENT CLASSES ==========
class CoinManager:
    def __init__(self):
        self.active_coins = set()
        self._lock = threading.Lock()
    
    def register_coin(self, symbol):
        if not symbol: return
        with self._lock: self.active_coins.add(symbol.upper())
    
    def unregister_coin(self, symbol):
        if not symbol: return
        with self._lock: self.active_coins.discard(symbol.upper())
    
    def is_coin_active(self, symbol):
        if not symbol: return False
        with self._lock: return symbol.upper() in self.active_coins
    
    def get_active_coins(self):
        with self._lock: return list(self.active_coins)

class BotExecutionCoordinator:
    def __init__(self):
        self._lock = threading.Lock()
        self._bot_queue = queue.Queue()
        self._current_finding_bot = None
        self._found_coins = set()
        self._bots_with_coins = set()
    
    def request_coin_search(self, bot_id):
        with self._lock:
            if bot_id in self._bots_with_coins:
                return False
                
            # ✅ SỬA: Cho phép bot đang được chỉ định (_current_finding_bot) được quyền scan
            if self._current_finding_bot is None or self._current_finding_bot == bot_id:
                self._current_finding_bot = bot_id
                return True
            else:
                # Chỉ xếp hàng nếu chưa ở trong queue
                if bot_id not in list(self._bot_queue.queue):
                    self._bot_queue.put(bot_id)
                return False
    
    def finish_coin_search(self, bot_id, found_symbol=None, has_coin_now=False):
        with self._lock:
            if self._current_finding_bot == bot_id:
                self._current_finding_bot = None
                if found_symbol: self._found_coins.add(found_symbol)
                if has_coin_now: self._bots_with_coins.add(bot_id)
                
                if not self._bot_queue.empty():
                    next_bot = self._bot_queue.get()
                    self._current_finding_bot = next_bot
                    return next_bot
            return None
    
    def bot_has_coin(self, bot_id):
        with self._lock:
            self._bots_with_coins.add(bot_id)
            new_queue = queue.Queue()
            while not self._bot_queue.empty():
                bot_in_queue = self._bot_queue.get()
                if bot_in_queue != bot_id: new_queue.put(bot_in_queue)
            self._bot_queue = new_queue
    
    def bot_lost_coin(self, bot_id):
        with self._lock:
            if bot_id in self._bots_with_coins:
                self._bots_with_coins.remove(bot_id)
    
    def is_coin_available(self, symbol):
        with self._lock: return symbol not in self._found_coins

    def bot_processing_coin(self, bot_id):
        """Đánh dấu bot đang xử lý coin (chưa vào lệnh)"""
        with self._lock:
            self._bots_with_coins.add(bot_id)
            # Xóa bot khỏi queue nếu có
            new_queue = queue.Queue()
            while not self._bot_queue.empty():
                bot_in_queue = self._bot_queue.get()
                if bot_in_queue != bot_id:
                    new_queue.put(bot_in_queue)
            self._bot_queue = new_queue
    
    def get_queue_info(self):
        with self._lock:
            return {
                'current_finding': self._current_finding_bot,
                'queue_size': self._bot_queue.qsize(),
                'queue_bots': list(self._bot_queue.queue),
                'bots_with_coins': list(self._bots_with_coins),
                'found_coins_count': len(self._found_coins)
            }
    
    def get_queue_position(self, bot_id):
        with self._lock:
            if self._current_finding_bot == bot_id: return 0
            else:
                queue_list = list(self._bot_queue.queue)
                return queue_list.index(bot_id) + 1 if bot_id in queue_list else -1

class SmartCoinFinder:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.last_scan_time = 0
        self.scan_cooldown = 10
        self.analysis_cache = {}
        self.cache_ttl = 30
    
    def get_symbol_leverage(self, symbol):
        return get_max_leverage(symbol, self.api_key, self.api_secret)
    
    def calculate_rsi(self, prices, period=14):
        if len(prices) < period + 1: return 50
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gains = np.mean(gains[:period])
        avg_losses = np.mean(losses[:period])
        if avg_losses == 0: return 100
            
        rs = avg_gains / avg_losses
        return 100 - (100 / (1 + rs))
    
    def get_rsi_signal(self, symbol, volume_threshold=20):
        try:
            current_time = time.time()
            cache_key = f"{symbol}_{volume_threshold}"
            
            if (cache_key in self.analysis_cache and 
                current_time - self.analysis_cache[cache_key]['timestamp'] < self.cache_ttl):
                return self.analysis_cache[cache_key]['signal']
            
            data = binance_api_request(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": symbol, "interval": "5m", "limit": 15}
            )
            if not data or len(data) < 15: return None
            
            prev_prev_candle, prev_candle, current_candle = data[-4], data[-3], data[-2]
            
            prev_prev_close, prev_close, current_close = float(prev_prev_candle[4]), float(prev_candle[4]), float(current_candle[4])
            prev_prev_volume, prev_volume, current_volume = float(prev_prev_candle[5]), float(prev_candle[5]), float(current_candle[5])
            
            closes = [float(k[4]) for k in data]
            rsi_current = self.calculate_rsi(closes)
            
            price_change_prev = prev_close - prev_prev_close
            price_change_current = current_close - prev_close
            
            volume_change_prev = (prev_volume - prev_prev_volume) / prev_prev_volume * 100
            volume_change_current = (current_volume - prev_volume) / prev_volume * 100
            
            price_increasing = price_change_current > 0
            price_decreasing = price_change_current < 0
            price_not_increasing = price_change_current <= 0
            price_not_decreasing = price_change_current >= 0
            
            volume_increasing = volume_change_current > volume_threshold
            volume_decreasing = volume_change_current < -volume_threshold
            
            # RSI signal conditions
            if rsi_current > 80 and price_increasing and volume_increasing:
                result = "SELL"
            elif rsi_current < 20 and price_decreasing and volume_decreasing:
                result = "SELL"
            elif rsi_current > 80 and price_increasing and volume_decreasing:
                result = "BUY"
            elif rsi_current < 20 and price_decreasing and volume_increasing:
                result = "BUY"
            elif rsi_current > 20 and price_not_decreasing and volume_decreasing:
                result = "BUY"
            elif rsi_current < 80 and price_not_increasing and volume_increasing:
                result = "SELL"
            else:
                result = None
            
            self.analysis_cache[cache_key] = {'signal': result, 'timestamp': current_time}
            return result
            
        except Exception as e:
            logger.error(f"RSI analysis error {symbol}: {str(e)}")
            return None
    
    def get_entry_signal(self, symbol):
        return self.get_rsi_signal(symbol, volume_threshold=20)
    
    def get_exit_signal(self, symbol):
        return self.get_rsi_signal(symbol, volume_threshold=40)
    
    def has_existing_position(self, symbol):
        try:
            positions = get_positions(symbol, self.api_key, self.api_secret)
            if positions:
                for pos in positions:
                    if abs(float(pos.get('positionAmt', 0))) > 0:
                        logger.info(f"⚠️ Position detected on {symbol}")
                        return True
            return False
        except Exception as e:
            logger.error(f"Position check error {symbol}: {str(e)}")
            return True

    def find_best_coin_any_signal(self, excluded_coins=None, required_leverage=10):
        try:
            now = time.time()
            if now - self.last_scan_time < self.scan_cooldown: return None
            self.last_scan_time = now

            all_symbols = get_all_usdc_pairs(limit=20)
            if not all_symbols: return None

            valid_symbols = []
            for symbol in all_symbols:
                if excluded_coins and symbol in excluded_coins: continue
                if self.has_existing_position(symbol): continue

                max_lev = self.get_symbol_leverage(symbol)
                if max_lev < required_leverage: continue

                time.sleep(0.05)
                entry_signal = self.get_entry_signal(symbol)
                if entry_signal in ["BUY", "SELL"]:
                    valid_symbols.append((symbol, entry_signal))
                    logger.info(f"✅ Found signal coin: {symbol} - {entry_signal}")

            if not valid_symbols: return None
            selected_symbol, _ = random.choice(valid_symbols)

            if self.has_existing_position(selected_symbol): return None
            logger.info(f"🎯 Selected coin: {selected_symbol}")
            return selected_symbol

        except Exception as e:
            logger.error(f"❌ Coin finding error: {str(e)}")
            return None

class WebSocketManager:
    def __init__(self):
        self.connections = {}
        self.executor = ThreadPoolExecutor(max_workers=20)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.price_cache = {}
        self.last_price_update = {}
        
    def add_symbol(self, symbol, callback):
        if not symbol: return
        symbol = symbol.upper()
        with self._lock:
            if symbol not in self.connections:
                self._create_connection(symbol, callback)
                
    def _create_connection(self, symbol, callback):
        if self._stop_event.is_set(): return
        
        streams = [f"{symbol.lower()}@trade"]
        url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"
        
        def on_message(ws, message):
            try:
                data = json.loads(message)
                if 'data' in data:
                    symbol = data['data']['s']
                    price = float(data['data']['p'])
                    current_time = time.time()
                    
                    if (symbol in self.last_price_update and 
                        current_time - self.last_price_update[symbol] < 0.1):
                        return
                    
                    self.last_price_update[symbol] = current_time
                    self.price_cache[symbol] = price
                    self.executor.submit(callback, price)
            except Exception as e:
                logger.error(f"WebSocket message error {symbol}: {str(e)}")
                
        def on_error(ws, error):
            logger.error(f"WebSocket error {symbol}: {str(error)}")
            if not self._stop_event.is_set():
                time.sleep(5)
                self._reconnect(symbol, callback)
            
        def on_close(ws, close_status_code, close_msg):
            logger.info(f"WebSocket closed {symbol}: {close_status_code} - {close_msg}")
            if not self._stop_event.is_set() and symbol in self.connections:
                time.sleep(5)
                self._reconnect(symbol, callback)
                
        ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close)
        thread = threading.Thread(target=ws.run_forever, daemon=True)
        thread.start()
        
        self.connections[symbol] = {'ws': ws, 'thread': thread, 'callback': callback}
        logger.info(f"🔗 WebSocket started for {symbol}")
        
    def _reconnect(self, symbol, callback):
        logger.info(f"Reconnecting WebSocket for {symbol}")
        self.remove_symbol(symbol)
        self._create_connection(symbol, callback)
        
    def remove_symbol(self, symbol):
        if not symbol: return
        symbol = symbol.upper()
        with self._lock:
            if symbol in self.connections:
                try: self.connections[symbol]['ws'].close()
                except Exception as e: logger.error(f"WebSocket close error {symbol}: {str(e)}")
                del self.connections[symbol]
                logger.info(f"WebSocket removed for {symbol}")
                
    def stop(self):
        self._stop_event.set()
        for symbol in list(self.connections.keys()):
            self.remove_symbol(symbol)

# ========== BOT CORE CLASSES ==========
class BaseBot:
    def __init__(self, symbol, lev, percent, tp, sl, roi_trigger, ws_manager, api_key, api_secret,
                 telegram_bot_token, telegram_chat_id, strategy_name, config_key=None, bot_id=None,
                 coin_manager=None, symbol_locks=None, max_coins=1, bot_coordinator=None):

        self.max_coins = 1
        self.active_symbols = []
        self.symbol_data = {}
        self.symbol = symbol.upper() if symbol else None
        
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
        self.bot_id = bot_id or f"{strategy_name}_{int(time.time())}_{random.randint(1000, 9999)}"

        self.status = "searching" if not symbol else "waiting"
        self._stop = False

        self.current_processing_symbol = None
        self.last_trade_completion_time = 0
        self.trade_cooldown = 30

        self.last_global_position_check = 0
        self.last_error_log_time = 0
        self.global_position_check_interval = 30

        self.global_long_count = 0
        self.global_short_count = 0
        self.global_long_pnl = 0
        self.global_short_pnl = 0

        self.coin_manager = coin_manager or CoinManager()
        self.symbol_locks = symbol_locks
        self.coin_finder = SmartCoinFinder(api_key, api_secret)

        self.find_new_bot_after_close = True
        self.bot_creation_time = time.time()

        self.execution_lock = threading.Lock()
        self.last_execution_time = 0
        self.execution_cooldown = 1

        self.bot_coordinator = bot_coordinator or BotExecutionCoordinator()

        if symbol and not self.coin_finder.has_existing_position(symbol):
            self._add_symbol(symbol)
        
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

        roi_info = f" | 🎯 ROI Trigger: {roi_trigger}%" if roi_trigger else " | 🎯 ROI Trigger: Tắt"
        self.log(f"🟢 Bot {strategy_name} started | 1 coin | Leverage: {lev}x | Capital: {percent}% | TP/SL: {tp}%/{sl}%{roi_info}")

    def _run(self):
        """Vòng lặp chính - CHỈ CHUYỂN QUYỀN KHI ĐÃ VÀO LỆNH THÀNH CÔNG"""
        while not self._stop:
            try:
                current_time = time.time()
                
                # KIỂM TRA VỊ THẾ TOÀN TÀI KHOẢN ĐỊNH KỲ
                if current_time - self.last_global_position_check > 30:
                    self.check_global_positions()
                    self.last_global_position_check = current_time
                
                # NẾU BOT KHÔNG CÓ COIN NÀO - YÊU CẦU TÌM COIN
                if not self.active_symbols:
                    search_permission = self.bot_coordinator.request_coin_search(self.bot_id)
                    
                    if search_permission:
                        # Bot này được quyền tìm coin
                        queue_info = self.bot_coordinator.get_queue_info()
                        self.log(f"🔍 Đang tìm coin (vị trí: 1/{queue_info['queue_size'] + 1})...")
                        
                        # TÌM COIN
                        found_coin = self._find_and_add_new_coin()
                        
                        if found_coin:
                            # 🎯 QUAN TRỌNG: CHỈ ĐÁNH DẤU LÀ ĐANG XỬ LÝ, CHƯA CHUYỂN QUYỀN
                            self.bot_coordinator.bot_has_coin(self.bot_id)  # Đánh dấu bot đang xử lý coin
                            self.log(f"✅ Đã tìm thấy coin: {found_coin}, đang chờ vào lệnh...")
                            # 🚫 KHÔNG gọi finish_coin_search ở đây - chờ vào lệnh thành công
                        else:
                            # Tìm thất bại - giải phóng quyền tìm ngay
                            self.bot_coordinator.finish_coin_search(self.bot_id)
                            self.log(f"❌ Không tìm thấy coin phù hợp")
                    else:
                        # Chờ trong queue
                        queue_pos = self.bot_coordinator.get_queue_position(self.bot_id)
                        if queue_pos > 0:
                            queue_info = self.bot_coordinator.get_queue_info()
                            current_finder = queue_info['current_finding']
                            self.log(f"⏳ Đang chờ tìm coin (vị trí: {queue_pos}/{queue_info['queue_size'] + 1}) - Bot đang tìm: {current_finder}")
                        time.sleep(2)
                
                # XỬ LÝ COIN HIỆN TẠI (nếu có) - QUAN TRỌNG: KIỂM TRA VÀO LỆNH THÀNH CÔNG
                for symbol in self.active_symbols.copy():
                    position_opened = self._process_single_symbol(symbol)
                    
                    # 🎯 NẾU VỪA VÀO LỆNH THÀNH CÔNG, CHUYỂN QUYỀN CHO BOT TIẾP THEO
                    if position_opened:
                        self.log(f"🎯 Đã vào lệnh thành công {symbol}, chuyển quyền tìm coin...")
                        next_bot = self.bot_coordinator.finish_coin_search(self.bot_id)
                        if next_bot:
                            self.log(f"🔄 Đã chuyển quyền tìm coin cho bot: {next_bot}")
                        break  # Chỉ xử lý một coin một lúc
                
                time.sleep(1)
                
            except Exception as e:
                if time.time() - self.last_error_log_time > 10:
                    self.log(f"❌ Lỗi hệ thống: {str(e)}")
                    self.last_error_log_time = time.time()
                time.sleep(5)
    def _process_single_symbol(self, symbol):
        """Xử lý một symbol duy nhất - TRẢ VỀ True NẾU VỪA VÀO LỆNH THÀNH CÔNG"""
        try:
            symbol_info = self.symbol_data[symbol]
            current_time = time.time()
            
            # Kiểm tra vị thế định kỳ
            if current_time - symbol_info.get('last_position_check', 0) > 30:
                self._check_symbol_position(symbol)
                symbol_info['last_position_check'] = current_time
            
            # Xử lý theo trạng thái
            if symbol_info['position_open']:
                # Kiểm tra đóng lệnh
                if self._check_smart_exit_condition(symbol):
                    return False
                
                # Kiểm tra TP/SL truyền thống
                self._check_symbol_tp_sl(symbol)
                return False
            else:
                # Tìm cơ hội vào lệnh
                if (current_time - symbol_info['last_trade_time'] > 30 and 
                    current_time - symbol_info['last_close_time'] > 30):
                    
                    entry_signal = self.coin_finder.get_entry_signal(symbol)
                    
                    if entry_signal:
                        target_side = self.get_next_side_based_on_comprehensive_analysis()
                        
                        if entry_signal == target_side:
                            if not self.coin_finder.has_existing_position(symbol):
                                if self._open_symbol_position(symbol, target_side):
                                    symbol_info['last_trade_time'] = current_time
                                    return True  # 🎯 TRẢ VỀ True KHI VÀO LỆNH THÀNH CÔNG
                return False
                
        except Exception as e:
            self.log(f"❌ Lỗi xử lý {symbol}: {str(e)}")
            return False

    def _check_smart_exit_condition(self, symbol):
        try:
            if not self.symbol_data[symbol]['position_open'] or not self.symbol_data[symbol]['roi_check_activated']:
                return False
            
            current_price = self.get_current_price(symbol)
            if current_price <= 0: return False
            
            if self.symbol_data[symbol]['side'] == "BUY":
                profit = (current_price - self.symbol_data[symbol]['entry']) * abs(self.symbol_data[symbol]['qty'])
            else:
                profit = (self.symbol_data[symbol]['entry'] - current_price) * abs(self.symbol_data[symbol]['qty'])
                
            invested = self.symbol_data[symbol]['entry'] * abs(self.symbol_data[symbol]['qty']) / self.lev
            if invested <= 0: return False
                
            current_roi = (profit / invested) * 100
            
            if current_roi >= self.roi_trigger:
                exit_signal = self.coin_finder.get_exit_signal(symbol)
                if exit_signal:
                    reason = f"🎯 Reached ROI {self.roi_trigger}% + Exit signal (ROI: {current_roi:.2f}%)"
                    self._close_symbol_position(symbol, reason)
                    return True
            return False
            
        except Exception as e:
            self.log(f"❌ Smart exit check error {symbol}: {str(e)}")
            return False

    def _find_and_add_new_coin(self):
        """Tìm và thêm coin mới - TRẢ VỀ TÊN COIN HOẶC NONE"""
        try:
            active_coins = self.coin_manager.get_active_coins()
            
            new_symbol = self.coin_finder.find_best_coin_any_signal(
                excluded_coins=active_coins,
                required_leverage=self.lev
            )
            
            if new_symbol and self.bot_coordinator.is_coin_available(new_symbol):
                if self.coin_finder.has_existing_position(new_symbol):
                    return None  # ❌ Không thể thêm do có vị thế tồn tại
                    
                success = self._add_symbol(new_symbol)
                if success:
                    # Kiểm tra ngay lập tức
                    time.sleep(1)
                    if self.coin_finder.has_existing_position(new_symbol):
                        self.log(f"🚫 {new_symbol} - PHÁT HIỆN CÓ VỊ THẾ SAU KHI THÊM, DỪNG THEO DÕI NGAY")
                        self.stop_symbol(new_symbol)
                        return None  # ❌ Phát hiện vị thế sau khi thêm
                        
                    return new_symbol  # ✅ TRẢ VỀ TÊN COIN KHI THÀNH CÔNG
            
            return None  # ❌ Không tìm thấy coin phù hợp
                
        except Exception as e:
            self.log(f"❌ Lỗi tìm coin mới: {str(e)}")
            return None
            
    def _add_symbol(self, symbol):
        if symbol in self.active_symbols or len(self.active_symbols) >= self.max_coins:
            return False
        if self.coin_finder.has_existing_position(symbol): return False
        
        self.symbol_data[symbol] = {
            'status': 'waiting', 'side': '', 'qty': 0, 'entry': 0, 'current_price': 0,
            'position_open': False, 'last_trade_time': 0, 'last_close_time': 0,
            'entry_base': 0, 'average_down_count': 0, 'last_average_down_time': 0,
            'high_water_mark_roi': 0, 'roi_check_activated': False,
            'close_attempted': False, 'last_close_attempt': 0, 'last_position_check': 0
        }
        
        self.active_symbols.append(symbol)
        self.coin_manager.register_coin(symbol)
        self.ws_manager.add_symbol(symbol, lambda price, sym=symbol: self._handle_price_update(price, sym))
        
        self._check_symbol_position(symbol)
        if self.symbol_data[symbol]['position_open']:
            self.stop_symbol(symbol)
            return False
        return True

    def _handle_price_update(self, price, symbol):
        if symbol in self.symbol_data:
            self.symbol_data[symbol]['current_price'] = price

    def get_current_price(self, symbol):
        if (symbol in self.ws_manager.price_cache and 
            time.time() - self.ws_manager.last_price_update.get(symbol, 0) < 5):
            return self.ws_manager.price_cache[symbol]
        return get_current_price(symbol)

    def _check_symbol_position(self, symbol):
        try:
            positions = get_positions(symbol, self.api_key, self.api_secret)
            if not positions:
                self._reset_symbol_position(symbol)
                return
            
            position_found = False
            for pos in positions:
                if pos['symbol'] == symbol:
                    position_amt = float(pos.get('positionAmt', 0))
                    if abs(position_amt) > 0:
                        position_found = True
                        self.symbol_data[symbol]['position_open'] = True
                        self.symbol_data[symbol]['status'] = "open"
                        self.symbol_data[symbol]['side'] = "BUY" if position_amt > 0 else "SELL"
                        self.symbol_data[symbol]['qty'] = position_amt
                        self.symbol_data[symbol]['entry'] = float(pos.get('entryPrice', 0))
                        
                        current_price = self.get_current_price(symbol)
                        if current_price > 0:
                            if self.symbol_data[symbol]['side'] == "BUY":
                                profit = (current_price - self.symbol_data[symbol]['entry']) * abs(self.symbol_data[symbol]['qty'])
                            else:
                                profit = (self.symbol_data[symbol]['entry'] - current_price) * abs(self.symbol_data[symbol]['qty'])
                                
                            invested = self.symbol_data[symbol]['entry'] * abs(self.symbol_data[symbol]['qty']) / self.lev
                            if invested > 0:
                                current_roi = (profit / invested) * 100
                                if current_roi >= self.roi_trigger:
                                    self.symbol_data[symbol]['roi_check_activated'] = True
                        break
                    else:
                        position_found = True
                        self._reset_symbol_position(symbol)
                        break
            
            if not position_found:
                self._reset_symbol_position(symbol)
                
        except Exception as e:
            self.log(f"❌ Position check error {symbol}: {str(e)}")

    def _reset_symbol_position(self, symbol):
        if symbol in self.symbol_data:
            self.symbol_data[symbol].update({
                'position_open': False, 'status': "waiting", 'side': "", 'qty': 0, 'entry': 0,
                'close_attempted': False, 'last_close_attempt': 0, 'entry_base': 0,
                'average_down_count': 0, 'high_water_mark_roi': 0, 'roi_check_activated': False
            })

    def _open_symbol_position(self, symbol, side):
        try:
            if self.coin_finder.has_existing_position(symbol):
                self.log(f"⚠️ {symbol} - POSITION EXISTS ON BINANCE, SKIPPING")
                self.stop_symbol(symbol)
                return False

            self._check_symbol_position(symbol)
            if self.symbol_data[symbol]['position_open']: return False

            current_leverage = self.coin_finder.get_symbol_leverage(symbol)
            if current_leverage < self.lev:
                self.log(f"❌ {symbol} - Insufficient leverage: {current_leverage}x < {self.lev}x")
                self.stop_symbol(symbol)
                return False

            if not set_leverage(symbol, self.lev, self.api_key, self.api_secret):
                self.log(f"❌ {symbol} - Cannot set leverage")
                self.stop_symbol(symbol)
                return False

            balance = get_balance(self.api_key, self.api_secret)
            if balance is None or balance <= 0:
                self.log(f"❌ {symbol} - Insufficient balance")
                return False

            current_price = self.get_current_price(symbol)
            if current_price <= 0:
                self.log(f"❌ {symbol} - Price error")
                self.stop_symbol(symbol)
                return False

            step_size = get_step_size(symbol, self.api_key, self.api_secret)
            usd_amount = balance * (self.percent / 100)
            qty = (usd_amount * self.lev) / current_price
            if step_size > 0:
                qty = math.floor(qty / step_size) * step_size
                qty = round(qty, 8)

            if qty <= 0 or qty < step_size:
                self.log(f"❌ {symbol} - Invalid quantity")
                self.stop_symbol(symbol)
                return False

            cancel_all_orders(symbol, self.api_key, self.api_secret)
            time.sleep(1)

            result = place_order(symbol, side, qty, self.api_key, self.api_secret)
            if result and 'orderId' in result:
                executed_qty = float(result.get('executedQty', 0))
                avg_price = float(result.get('avgPrice', current_price))

                if executed_qty >= 0:
                    time.sleep(1)
                    self._check_symbol_position(symbol)
                    
                    if not self.symbol_data[symbol]['position_open']:
                        self.log(f"❌ {symbol} - Order filled but no position created")
                        self.stop_symbol(symbol)
                        return False
                    
                    self.symbol_data[symbol].update({
                        'entry': avg_price, 'entry_base': avg_price, 'average_down_count': 0,
                        'side': side, 'qty': executed_qty if side == "BUY" else -executed_qty,
                        'position_open': True, 'status': "open", 'high_water_mark_roi': 0,
                        'roi_check_activated': False
                    })

                    self.bot_coordinator.bot_has_coin(self.bot_id)

                    message = (f"✅ <b>OPENED POSITION {symbol}</b>\n"
                              f"🤖 Bot: {self.bot_id}\n📌 Direction: {side}\n"
                              f"🏷️ Entry: {avg_price:.4f}\n📊 Quantity: {executed_qty:.4f}\n"
                              f"💰 Leverage: {self.lev}x\n🎯 TP: {self.tp}% | 🛡️ SL: {self.sl}%")
                    if self.roi_trigger: message += f" | 🎯 ROI Trigger: {self.roi_trigger}%"
                    
                    self.log(message)
                    return True
                else:
                    self.log(f"❌ {symbol} - Order not filled")
                    self.stop_symbol(symbol)
                    return False
            else:
                error_msg = result.get('msg', 'Unknown error') if result else 'No response'
                self.log(f"❌ {symbol} - Order error: {error_msg}")
                self.stop_symbol(symbol)
                return False

        except Exception as e:
            self.log(f"❌ {symbol} - Open position error: {str(e)}")
            self.stop_symbol(symbol)
            return False

    def _close_symbol_position(self, symbol, reason=""):
        try:
            self._check_symbol_position(symbol)
            if not self.symbol_data[symbol]['position_open'] or abs(self.symbol_data[symbol]['qty']) <= 0:
                return True

            current_time = time.time()
            if (self.symbol_data[symbol]['close_attempted'] and 
                current_time - self.symbol_data[symbol]['last_close_attempt'] < 30):
                return False
            
            self.symbol_data[symbol]['close_attempted'] = True
            self.symbol_data[symbol]['last_close_attempt'] = current_time

            close_side = "SELL" if self.symbol_data[symbol]['side'] == "BUY" else "BUY"
            close_qty = abs(self.symbol_data[symbol]['qty'])
            
            cancel_all_orders(symbol, self.api_key, self.api_secret)
            time.sleep(1)
            
            result = place_order(symbol, close_side, close_qty, self.api_key, self.api_secret)
            if result and 'orderId' in result:
                current_price = self.get_current_price(symbol)
                pnl = 0
                if self.symbol_data[symbol]['entry'] > 0:
                    if self.symbol_data[symbol]['side'] == "BUY":
                        pnl = (current_price - self.symbol_data[symbol]['entry']) * abs(self.symbol_data[symbol]['qty'])
                    else:
                        pnl = (self.symbol_data[symbol]['entry'] - current_price) * abs(self.symbol_data[symbol]['qty'])
                
                message = (f"⛔ <b>CLOSED POSITION {symbol}</b>\n"
                          f"🤖 Bot: {self.bot_id}\n📌 Reason: {reason}\n"
                          f"🏷️ Exit: {current_price:.4f}\n📊 Quantity: {close_qty:.4f}\n"
                          f"💰 PnL: {pnl:.2f} USDC\n"
                          f"📈 Average downs: {self.symbol_data[symbol]['average_down_count']}")
                self.log(message)
                
                self.symbol_data[symbol]['last_close_time'] = time.time()
                self._reset_symbol_position(symbol)
                self.bot_coordinator.bot_lost_coin(self.bot_id)
                return True
            else:
                error_msg = result.get('msg', 'Unknown error') if result else 'No response'
                self.log(f"❌ {symbol} - Close order error: {error_msg}")
                self.symbol_data[symbol]['close_attempted'] = False
                return False
                
        except Exception as e:
            self.log(f"❌ {symbol} - Close position error: {str(e)}")
            self.symbol_data[symbol]['close_attempted'] = False
            return False

    def _check_symbol_tp_sl(self, symbol):
        if (not self.symbol_data[symbol]['position_open'] or 
            self.symbol_data[symbol]['entry'] <= 0 or 
            self.symbol_data[symbol]['close_attempted']):
            return

        current_price = self.get_current_price(symbol)
        if current_price <= 0: return

        if self.symbol_data[symbol]['side'] == "BUY":
            profit = (current_price - self.symbol_data[symbol]['entry']) * abs(self.symbol_data[symbol]['qty'])
        else:
            profit = (self.symbol_data[symbol]['entry'] - current_price) * abs(self.symbol_data[symbol]['qty'])
            
        invested = self.symbol_data[symbol]['entry'] * abs(self.symbol_data[symbol]['qty']) / self.lev
        if invested <= 0: return
            
        roi = (profit / invested) * 100

        if roi > self.symbol_data[symbol]['high_water_mark_roi']:
            self.symbol_data[symbol]['high_water_mark_roi'] = roi

        if (self.roi_trigger is not None and 
            self.symbol_data[symbol]['high_water_mark_roi'] >= self.roi_trigger and 
            not self.symbol_data[symbol]['roi_check_activated']):
            self.symbol_data[symbol]['roi_check_activated'] = True

        if self.tp is not None and roi >= self.tp:
            self._close_symbol_position(symbol, f"✅ Reached TP {self.tp}% (ROI: {roi:.2f}%)")
        elif self.sl is not None and self.sl > 0 and roi <= -self.sl:
            self._close_symbol_position(symbol, f"❌ Reached SL {self.sl}% (ROI: {roi:.2f}%)")

    def stop_symbol(self, symbol):
        if symbol not in self.active_symbols: return False
        
        self.log(f"⛔ Stopping coin {symbol}...")
        
        if self.current_processing_symbol == symbol:
            timeout = time.time() + 10
            while self.current_processing_symbol == symbol and time.time() < timeout:
                time.sleep(1)
        
        if self.symbol_data[symbol]['position_open']:
            self._close_symbol_position(symbol, "Stop coin by command")
        
        self.ws_manager.remove_symbol(symbol)
        self.coin_manager.unregister_coin(symbol)
        
        if symbol in self.symbol_data: del self.symbol_data[symbol]
        if symbol in self.active_symbols: self.active_symbols.remove(symbol)
        
        self.bot_coordinator.bot_lost_coin(self.bot_id)
        self.log(f"✅ Stopped coin {symbol}")
        return True

    def stop_all_symbols(self):
        self.log("⛔ Stopping all coins...")
        symbols_to_stop = self.active_symbols.copy()
        stopped_count = 0
        
        for symbol in symbols_to_stop:
            if self.stop_symbol(symbol):
                stopped_count += 1
                time.sleep(1)
        
        self.log(f"✅ Stopped {stopped_count} coins, bot still running")
        return stopped_count

    def stop(self):
        self._stop = True
        stopped_count = self.stop_all_symbols()
        self.log(f"🔴 Bot stopped - Stopped {stopped_count} coins")

    def check_global_positions(self):
        try:
            positions = get_positions(api_key=self.api_key, api_secret=self.api_secret)
            if not positions:
                self.global_long_count = 0
                self.global_short_count = 0
                self.global_long_pnl = 0
                self.global_short_pnl = 0
                return
            
            long_count, short_count = 0, 0
            long_pnl_total, short_pnl_total = 0, 0
            
            for pos in positions:
                position_amt = float(pos.get('positionAmt', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                
                if position_amt > 0:
                    long_count += 1
                    long_pnl_total += unrealized_pnl
                elif position_amt < 0:
                    short_count += 1
                    short_pnl_total += unrealized_pnl
            
            self.global_long_count = long_count
            self.global_short_count = short_count
            self.global_long_pnl = long_pnl_total
            self.global_short_pnl = short_pnl_total
            
        except Exception as e:
            if time.time() - self.last_error_log_time > 30:
                self.log(f"❌ Global positions error: {str(e)}")
                self.last_error_log_time = time.time()

    def get_next_side_based_on_comprehensive_analysis(self):
        self.check_global_positions()
        long_pnl, short_pnl = self.global_long_pnl, self.global_short_pnl
        
        if long_pnl > short_pnl: return "BUY"
        elif short_pnl > long_pnl: return "SELL"
        else: return random.choice(["BUY", "SELL"])

    def log(self, message):
        important_keywords = ['❌', '✅', '⛔', '💰', '📈', '📊', '🎯', '🛡️', '🔴', '🟢', '⚠️', '🚫']
        if any(keyword in message for keyword in important_keywords):
            logger.warning(f"[{self.bot_id}] {message}")
            if self.telegram_bot_token and self.telegram_chat_id:
                send_telegram(f"<b>{self.bot_id}</b>: {message}", 
                             bot_token=self.telegram_bot_token, 
                             default_chat_id=self.telegram_chat_id)

class GlobalMarketBot(BaseBot):
    def __init__(self, symbol, lev, percent, tp, sl, roi_trigger, ws_manager,
                 api_key, api_secret, telegram_bot_token, telegram_chat_id, bot_id=None, **kwargs):
        super().__init__(symbol, lev, percent, tp, sl, roi_trigger, ws_manager,
                         api_key, api_secret, telegram_bot_token, telegram_chat_id,
                         "RSI-Volume-System-Queue", bot_id=bot_id, **kwargs)

class BotManager:
    def __init__(self, api_key=None, api_secret=None, telegram_bot_token=None, telegram_chat_id=None):
        self.ws_manager = WebSocketManager()
        self.bots = {}
        self.running = True
        self.start_time = time.time()
        self.user_states = {}

        self.api_key = api_key
        self.api_secret = api_secret
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id

        self.bot_coordinator = BotExecutionCoordinator()
        self.coin_manager = CoinManager()
        self.symbol_locks = defaultdict(threading.Lock)

        if api_key and api_secret:
            self._verify_api_connection()
            self.log("🟢 QUEUE BOT SYSTEM STARTED - FIFO MECHANISM")

            self.telegram_thread = threading.Thread(target=self._telegram_listener, daemon=True)
            self.telegram_thread.start()

            if self.telegram_chat_id:
                self.send_main_menu(self.telegram_chat_id)
        else:
            self.log("⚡ BotManager started in no-config mode")

    def _verify_api_connection(self):
        try:
            balance = get_balance(self.api_key, self.api_secret)
            if balance is None:
                self.log("❌ ERROR: Cannot connect to Binance API. Check:")
                self.log("   - API Key and Secret")
                self.log("   - IP blocking (error 451), try VPN")
                self.log("   - Internet connection")
                return False
            else:
                self.log(f"✅ Binance connection successful! Balance: {balance:.2f} USDC")
                return True
        except Exception as e:
            self.log(f"❌ Connection check error: {str(e)}")
            return False

    def get_position_summary(self):
        try:
            all_positions = get_positions(api_key=self.api_key, api_secret=self.api_secret)
            
            total_long_count, total_short_count = 0, 0
            total_long_pnl, total_short_pnl, total_unrealized_pnl = 0, 0, 0
            
            for pos in all_positions:
                position_amt = float(pos.get('positionAmt', 0))
                if position_amt != 0:
                    unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                    total_unrealized_pnl += unrealized_pnl
                    
                    if position_amt > 0:
                        total_long_count += 1
                        total_long_pnl += unrealized_pnl
                    else:
                        total_short_count += 1
                        total_short_pnl += unrealized_pnl
        
            bot_details = []
            total_bots_with_coins, trading_bots = 0, 0
            
            for bot_id, bot in self.bots.items():
                has_coin = len(bot.active_symbols) > 0 if hasattr(bot, 'active_symbols') else False
                is_trading = False
                
                if has_coin and hasattr(bot, 'symbol_data'):
                    for symbol, data in bot.symbol_data.items():
                        if data.get('position_open', False):
                            is_trading = True
                            break
                
                if has_coin: total_bots_with_coins += 1
                if is_trading: trading_bots += 1
                
                bot_details.append({
                    'bot_id': bot_id, 'has_coin': has_coin, 'is_trading': is_trading,
                    'symbols': bot.active_symbols if hasattr(bot, 'active_symbols') else [],
                    'symbol_data': bot.symbol_data if hasattr(bot, 'symbol_data') else {},
                    'status': bot.status, 'leverage': bot.lev, 'percent': bot.percent
                })
            
            summary = "📊 **DETAILED STATISTICS - QUEUE SYSTEM**\n\n"
            
            balance = get_balance(self.api_key, self.api_secret)
            if balance is not None:
                summary += f"💰 **BALANCE**: {balance:.2f} USDC\n"
                summary += f"📈 **Total PnL**: {total_unrealized_pnl:.2f} USDC\n\n"
            else:
                summary += f"💰 **BALANCE**: ❌ Connection error\n\n"
            
            summary += f"🤖 **SYSTEM BOTS**: {len(self.bots)} bots | {total_bots_with_coins} bots with coins | {trading_bots} bots trading\n\n"
            
            summary += f"📈 **PnL AND VOLUME ANALYSIS**:\n"
            summary += f"   📊 Count: LONG={total_long_count} | SHORT={total_short_count}\n"
            summary += f"   💰 PnL: LONG={total_long_pnl:.2f} USDC | SHORT={total_short_pnl:.2f} USDC\n"
            summary += f"   ⚖️ Difference: {abs(total_long_pnl - total_short_pnl):.2f} USDC\n\n"
            
            queue_info = self.bot_coordinator.get_queue_info()
            summary += f"🎪 **QUEUE INFORMATION (FIFO)**\n"
            summary += f"• Bot finding coin: {queue_info['current_finding'] or 'None'}\n"
            summary += f"• Bots in queue: {queue_info['queue_size']}\n"
            summary += f"• Bots with coins: {len(queue_info['bots_with_coins'])}\n"
            summary += f"• Distributed coins: {queue_info['found_coins_count']}\n\n"
            
            if queue_info['queue_bots']:
                summary += f"📋 **BOTS IN QUEUE**:\n"
                for i, bot_id in enumerate(queue_info['queue_bots']):
                    summary += f"  {i+1}. {bot_id}\n"
                summary += "\n"
            
            if bot_details:
                summary += "📋 **BOT DETAILS**:\n"
                for bot in bot_details:
                    status_emoji = "🟢" if bot['is_trading'] else "🟡" if bot['has_coin'] else "🔴"
                    summary += f"{status_emoji} **{bot['bot_id']}**\n"
                    summary += f"   💰 Leverage: {bot['leverage']}x | Capital: {bot['percent']}%\n"
                    
                    if bot['symbols']:
                        for symbol in bot['symbols']:
                            symbol_info = bot['symbol_data'].get(symbol, {})
                            status = "🟢 Trading" if symbol_info.get('position_open') else "🟡 Waiting signal"
                            side = symbol_info.get('side', '')
                            qty = symbol_info.get('qty', 0)
                            
                            summary += f"   🔗 {symbol} | {status}"
                            if side: summary += f" | {side} {abs(qty):.4f}"
                            summary += "\n"
                    else:
                        summary += f"   🔍 Finding coin...\n"
                    summary += "\n"
            
            return summary
                    
        except Exception as e:
            return f"❌ Statistics error: {str(e)}"

    def log(self, message):
        important_keywords = ['❌', '✅', '⛔', '💰', '📈', '📊', '🎯', '🛡️', '🔴', '🟢', '⚠️', '🚫']
        if any(keyword in message for keyword in important_keywords):
            logger.warning(f"[SYSTEM] {message}")
            if self.telegram_bot_token and self.telegram_chat_id:
                send_telegram(f"<b>SYSTEM</b>: {message}", 
                             chat_id=self.telegram_chat_id,
                             bot_token=self.telegram_bot_token, 
                             default_chat_id=self.telegram_chat_id)

    def send_main_menu(self, chat_id):
        welcome = (
            "🤖 <b>FUTURES TRADING BOT - QUEUE SYSTEM (FIFO)</b>\n\n"
            "🎯 <b>NEW OPERATING MECHANISM:</b>\n"
            "• Uses Queue (FIFO) - First In First Out\n"
            "• First bot in queue finds coin first\n"
            "• Bot enters position → next bot in queue finds IMMEDIATELY\n"
            "• Bots with coins CANNOT enter queue\n"
            "• Bots closing positions can re-enter queue\n\n"
            
            "⚡ <b>PERFORMANCE OPTIMIZATION:</b>\n"
            "• 5-minute leverage cache\n"
            "• Real-time WebSocket for all bots\n"
            "• 80% reduction in API calls with cache\n"
            "• Load distribution via multiple threads\n\n"
            
            "🚫 <b>AUTO EXCLUSION:</b>\n"
            "• BTCUSDC - Too volatile\n"
            "• ETHUSDC - Large volume\n"
            "• Coins with existing positions\n"
            "• Coins with insufficient leverage"
        )
        send_telegram(welcome, chat_id=chat_id, reply_markup=create_main_menu(),
                     bot_token=self.telegram_bot_token, 
                     default_chat_id=self.telegram_chat_id)

    def add_bot(self, symbol, lev, percent, tp, sl, roi_trigger, strategy_type, bot_count=1, **kwargs):
        if sl == 0: sl = None
            
        if not self.api_key or not self.api_secret:
            self.log("❌ API Key not set in BotManager")
            return False
        
        if not self._verify_api_connection():
            self.log("❌ CANNOT CONNECT TO BINANCE - CANNOT CREATE BOT")
            return False
        
        bot_mode = kwargs.get('bot_mode', 'static')
        created_count = 0
        
        try:
            for i in range(bot_count):
                if bot_mode == 'static' and symbol:
                    bot_id = f"STATIC_{strategy_type}_{int(time.time())}_{i}"
                else:
                    bot_id = f"DYNAMIC_{strategy_type}_{int(time.time())}_{i}"
                
                if bot_id in self.bots: continue
                
                bot_class = GlobalMarketBot
                
                bot = bot_class(
                    symbol, lev, percent, tp, sl, roi_trigger, self.ws_manager,
                    self.api_key, self.api_secret, self.telegram_bot_token, self.telegram_chat_id,
                    coin_manager=self.coin_manager, symbol_locks=self.symbol_locks,
                    bot_coordinator=self.bot_coordinator, bot_id=bot_id, max_coins=1
                )
                
                bot._bot_manager = self
                self.bots[bot_id] = bot
                created_count += 1
                
        except Exception as e:
            self.log(f"❌ Bot creation error: {str(e)}")
            return False
        
        if created_count > 0:
            roi_info = f" | 🎯 ROI Trigger: {roi_trigger}%" if roi_trigger else " | 🎯 ROI Trigger: Off"
            
            success_msg = (f"✅ <b>CREATED {created_count} QUEUE BOTS</b>\n\n"
                          f"🎯 Strategy: {strategy_type}\n💰 Leverage: {lev}x\n"
                          f"📈 % Balance: {percent}%\n🎯 TP: {tp}%\n"
                          f"🛡️ SL: {sl if sl is not None else 'Off'}%{roi_info}\n"
                          f"🔧 Mode: {bot_mode}\n🔢 Bot count: {created_count}\n")
            
            if bot_mode == 'static' and symbol:
                success_msg += f"🔗 Initial coin: {symbol}\n"
            else:
                success_msg += f"🔗 Coin: Auto search\n"
            
            success_msg += (f"\n🔄 <b>QUEUE SYSTEM ACTIVATED</b>\n"
                          f"• First bot in queue finds coin first\n"
                          f"• Bot enters position → next bot finds IMMEDIATELY\n"
                          f"• Bots with coins cannot enter queue\n"
                          f"• Bots closing positions can re-enter queue\n\n"
                          f"⚡ <b>EACH BOT RUNS IN SEPARATE THREAD</b>")
            
            self.log(success_msg)
            return True
        else:
            self.log("❌ Cannot create bot")
            return False

    def stop_coin(self, symbol):
        stopped_count = 0
        symbol = symbol.upper()
        
        for bot_id, bot in self.bots.items():
            if hasattr(bot, 'stop_symbol') and symbol in bot.active_symbols:
                if bot.stop_symbol(symbol): stopped_count += 1
                    
        if stopped_count > 0:
            self.log(f"✅ Stopped coin {symbol} in {stopped_count} bots")
            return True
        else:
            self.log(f"❌ Coin {symbol} not found in any bot")
            return False

    def get_coin_management_keyboard(self):
        all_coins = set()
        for bot in self.bots.values():
            if hasattr(bot, 'active_symbols'):
                all_coins.update(bot.active_symbols)
        
        if not all_coins: return None
            
        keyboard = []
        row = []
        for coin in sorted(list(all_coins))[:12]:
            row.append({"text": f"⛔ Coin: {coin}"})
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row: keyboard.append(row)
        
        keyboard.append([{"text": "⛔ STOP ALL COINS"}])
        keyboard.append([{"text": "❌ Hủy bỏ"}])
        
        return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": True}

    def stop_bot_symbol(self, bot_id, symbol):
        bot = self.bots.get(bot_id)
        if bot and hasattr(bot, 'stop_symbol'):
            success = bot.stop_symbol(symbol)
            if success: self.log(f"⛔ Stopped coin {symbol} in bot {bot_id}")
            return success
        return False

    def stop_all_bot_symbols(self, bot_id):
        bot = self.bots.get(bot_id)
        if bot and hasattr(bot, 'stop_all_symbols'):
            stopped_count = bot.stop_all_symbols()
            self.log(f"⛔ Stopped {stopped_count} coins in bot {bot_id}")
            return stopped_count
        return 0

    def stop_all_coins(self):
        self.log("⛔ Stopping all coins in all bots...")
        total_stopped = 0
        for bot_id, bot in self.bots.items():
            if hasattr(bot, 'stop_all_symbols'):
                stopped_count = bot.stop_all_symbols()
                total_stopped += stopped_count
                self.log(f"⛔ Stopped {stopped_count} coins in bot {bot_id}")
        
        self.log(f"✅ Stopped total {total_stopped} coins, system still running")
        return total_stopped

    def stop_bot(self, bot_id):
        bot = self.bots.get(bot_id)
        if bot:
            bot.stop()
            del self.bots[bot_id]
            self.log(f"🔴 Stopped bot {bot_id}")
            return True
        return False

    def stop_all(self):
        self.log("🔴 Stopping all bots...")
        for bot_id in list(self.bots.keys()):
            self.stop_bot(bot_id)
        self.log("🔴 Stopped all bots, system still running")

    def _telegram_listener(self):
        last_update_id = 0
        
        while self.running and self.telegram_bot_token:
            try:
                url = f"https://api.telegram.org/bot{self.telegram_bot_token}/getUpdates?offset={last_update_id+1}&timeout=5"
                response = requests.get(url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('ok'):
                        for update in data['result']:
                            update_id = update['update_id']
                            message = update.get('message', {})
                            chat_id = str(message.get('chat', {}).get('id'))
                            text = message.get('text', '').strip()
                            
                            if chat_id != self.telegram_chat_id: continue
                            
                            if update_id > last_update_id:
                                last_update_id = update_id
                                self._handle_telegram_message(chat_id, text)
                
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Telegram listener error: {str(e)}")
                time.sleep(1)

    def _handle_telegram_message(self, chat_id, text):
        user_state = self.user_states.get(chat_id, {})
        current_step = user_state.get('step')
        
        # Handle bot creation steps
        if current_step == 'waiting_bot_count':
            if text == '❌ Hủy bỏ':
                self.user_states[chat_id] = {}
                send_telegram("❌ Cancelled adding bot", chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                try:
                    bot_count = int(text)
                    if bot_count <= 0 or bot_count > 10:
                        send_telegram("⚠️ Bot count must be 1-10. Please choose:",
                                    chat_id=chat_id, reply_markup=create_bot_count_keyboard(),
                                    bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                        return
    
                    user_state['bot_count'] = bot_count
                    user_state['step'] = 'waiting_bot_mode'
                    
                    send_telegram(f"🤖 Bot count: {bot_count}\n\nChoose bot mode:",
                                chat_id=chat_id, reply_markup=create_bot_mode_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                except ValueError:
                    send_telegram("⚠️ Please enter valid number for bot count:",
                                chat_id=chat_id, reply_markup=create_bot_count_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
    
        elif current_step == 'waiting_bot_mode':
            if text == '❌ Hủy bỏ':
                self.user_states[chat_id] = {}
                send_telegram("❌ Cancelled adding bot", chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            elif text in ["🤖 Bot Tĩnh - Coin cụ thể", "🔄 Bot Động - Tự tìm coin"]:
                if text == "🤖 Bot Tĩnh - Coin cụ thể":
                    user_state['bot_mode'] = 'static'
                    user_state['step'] = 'waiting_symbol'
                    send_telegram("🎯 <b>SELECTED: STATIC BOT</b>\n\nBot will trade FIXED coin\nChoose coin:",
                                chat_id=chat_id, reply_markup=create_symbols_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                else:
                    user_state['bot_mode'] = 'dynamic'
                    user_state['step'] = 'waiting_leverage'
                    send_telegram("🎯 <b>SELECTED: DYNAMIC BOT</b>\n\nSystem will manage coins automatically\nChoose leverage:",
                                chat_id=chat_id, reply_markup=create_leverage_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
    
        elif current_step == 'waiting_symbol':
            if text == '❌ Hủy bỏ':
                self.user_states[chat_id] = {}
                send_telegram("❌ Cancelled adding bot", chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                user_state['symbol'] = text
                user_state['step'] = 'waiting_leverage'
                send_telegram(f"🔗 Coin: {text}\n\nChoose leverage:",
                            chat_id=chat_id, reply_markup=create_leverage_keyboard(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
    
        elif current_step == 'waiting_leverage':
            if text == '❌ Hủy bỏ':
                self.user_states[chat_id] = {}
                send_telegram("❌ Cancelled adding bot", chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                lev_text = text[:-1] if text.endswith('x') else text
                try:
                    leverage = int(lev_text)
                    if leverage <= 0 or leverage > 100:
                        send_telegram("⚠️ Leverage must be 1-100. Please choose:",
                                    chat_id=chat_id, reply_markup=create_leverage_keyboard(),
                                    bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                        return
    
                    user_state['leverage'] = leverage
                    user_state['step'] = 'waiting_percent'
                    
                    balance = get_balance(self.api_key, self.api_secret)
                    balance_info = f"\n💰 Current balance: {balance:.2f} USDT" if balance else ""
                    
                    send_telegram(f"💰 Leverage: {leverage}x{balance_info}\n\nChoose % balance per order:",
                                chat_id=chat_id, reply_markup=create_percent_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                except ValueError:
                    send_telegram("⚠️ Please enter valid number for leverage:",
                                chat_id=chat_id, reply_markup=create_leverage_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
    
        elif current_step == 'waiting_percent':
            if text == '❌ Hủy bỏ':
                self.user_states[chat_id] = {}
                send_telegram("❌ Cancelled adding bot", chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                try:
                    percent = float(text)
                    if percent <= 0 or percent > 100:
                        send_telegram("⚠️ % balance must be 0.1-100. Please choose:",
                                    chat_id=chat_id, reply_markup=create_percent_keyboard(),
                                    bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                        return
    
                    user_state['percent'] = percent
                    user_state['step'] = 'waiting_tp'
                    
                    balance = get_balance(self.api_key, self.api_secret)
                    actual_amount = balance * (percent / 100) if balance else 0
                    
                    send_telegram(f"📊 % Balance: {percent}%\n💵 Amount per order: ~{actual_amount:.2f} USDT\n\nChoose Take Profit (%):",
                                chat_id=chat_id, reply_markup=create_tp_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                except ValueError:
                    send_telegram("⚠️ Please enter valid number for % balance:",
                                chat_id=chat_id, reply_markup=create_percent_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
    
        elif current_step == 'waiting_tp':
            if text == '❌ Hủy bỏ':
                self.user_states[chat_id] = {}
                send_telegram("❌ Cancelled adding bot", chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                try:
                    tp = float(text)
                    if tp <= 0:
                        send_telegram("⚠️ Take Profit must be >0. Please choose:",
                                    chat_id=chat_id, reply_markup=create_tp_keyboard(),
                                    bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                        return
    
                    user_state['tp'] = tp
                    user_state['step'] = 'waiting_sl'
                    
                    send_telegram(f"🎯 Take Profit: {tp}%\n\nChoose Stop Loss (%):",
                                chat_id=chat_id, reply_markup=create_sl_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                except ValueError:
                    send_telegram("⚠️ Please enter valid number for Take Profit:",
                                chat_id=chat_id, reply_markup=create_tp_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
    
        elif current_step == 'waiting_sl':
            if text == '❌ Hủy bỏ':
                self.user_states[chat_id] = {}
                send_telegram("❌ Cancelled adding bot", chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                try:
                    sl = float(text)
                    if sl < 0:
                        send_telegram("⚠️ Stop Loss must be >=0. Please choose:",
                                    chat_id=chat_id, reply_markup=create_sl_keyboard(),
                                    bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                        return
    
                    user_state['sl'] = sl
                    user_state['step'] = 'waiting_roi_trigger'
                    
                    send_telegram(f"🛡️ Stop Loss: {sl}%\n\n🎯 <b>CHOOSE ROI THRESHOLD FOR SMART EXIT</b>\n\nChoose ROI trigger (%):",
                                chat_id=chat_id, reply_markup=create_roi_trigger_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                except ValueError:
                    send_telegram("⚠️ Please enter valid number for Stop Loss:",
                                chat_id=chat_id, reply_markup=create_sl_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
    
        elif current_step == 'waiting_roi_trigger':
            if text == '❌ Hủy bỏ':
                self.user_states[chat_id] = {}
                send_telegram("❌ Cancelled adding bot", chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            elif text == '❌ Tắt tính năng':
                user_state['roi_trigger'] = None
                self._finish_bot_creation(chat_id, user_state)
            else:
                try:
                    roi_trigger = float(text)
                    if roi_trigger <= 0:
                        send_telegram("⚠️ ROI Trigger must be >0. Please choose:",
                                    chat_id=chat_id, reply_markup=create_roi_trigger_keyboard(),
                                    bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                        return
    
                    user_state['roi_trigger'] = roi_trigger
                    self._finish_bot_creation(chat_id, user_state)
                    
                except ValueError:
                    send_telegram("⚠️ Please enter valid number for ROI Trigger:",
                                chat_id=chat_id, reply_markup=create_roi_trigger_keyboard(),
                                bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
    
        # Handle coin management
        elif text == "⛔ Quản lý Coin":
            keyboard = self.get_coin_management_keyboard()
            if not keyboard:
                send_telegram("📭 No coins being managed", chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                send_telegram("⛔ <b>COIN MANAGEMENT</b>\n\nChoose coin to stop:",
                            chat_id=chat_id, reply_markup=keyboard,
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text.startswith("⛔ Coin: "):
            symbol = text.replace("⛔ Coin: ", "").strip()
            if self.stop_coin(symbol):
                send_telegram(f"✅ Stopped coin {symbol}", chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                send_telegram(f"❌ Cannot stop coin {symbol}", chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "⛔ DỪNG TẤT CẢ COIN":
            stopped_count = self.stop_all_coins()
            send_telegram(f"✅ Stopped {stopped_count} coins, system still running", chat_id=chat_id,
                         bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text.startswith("⛔ Bot: "):
            bot_id = text.replace("⛔ Bot: ", "").strip()
            if self.stop_bot(bot_id):
                send_telegram(f"✅ Stopped bot {bot_id}", chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                send_telegram(f"❌ Bot {bot_id} not found", chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "⛔ DỪNG TẤT CẢ BOT":
            stopped_count = len(self.bots)
            self.stop_all()
            send_telegram(f"✅ Stopped {stopped_count} bots, system still running", chat_id=chat_id,
                         bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "➕ Thêm Bot":
            self.user_states[chat_id] = {'step': 'waiting_bot_count'}
            balance = get_balance(self.api_key, self.api_secret)
            if balance is None:
                send_telegram("❌ <b>BINANCE CONNECTION ERROR</b>\nCheck API Key and network!", chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                return
            
            send_telegram(f"🎯 <b>CHOOSE BOT COUNT</b>\n\n💰 Current balance: <b>{balance:.2f} USDT</b>\n\nChoose bot count (each bot manages 1 coin):",
                         chat_id=chat_id, reply_markup=create_bot_count_keyboard(),
                         bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "📊 Danh sách Bot":
            summary = self.get_position_summary()
            send_telegram(summary, chat_id=chat_id,
                         bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "⛔ Dừng Bot":
            if not self.bots:
                send_telegram("🤖 No bots running", chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                message = "⛔ <b>CHOOSE BOT TO STOP</b>\n\n"
                bot_keyboard = []
                
                for bot_id, bot in self.bots.items():
                    bot_keyboard.append([{"text": f"⛔ Bot: {bot_id}"}])
                
                keyboard = []
                if bot_keyboard: keyboard.extend(bot_keyboard)
                keyboard.append([{"text": "⛔ DỪNG TẤT CẢ BOT"}])
                keyboard.append([{"text": "❌ Hủy bỏ"}])
                
                send_telegram(message, chat_id=chat_id, 
                            reply_markup={"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": True},
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "📊 Thống kê":
            summary = self.get_position_summary()
            send_telegram(summary, chat_id=chat_id,
                         bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "💰 Số dư":
            try:
                balance = get_balance(self.api_key, self.api_secret)
                if balance is None:
                    send_telegram("❌ <b>BINANCE CONNECTION ERROR</b>\nCheck API Key and network!", chat_id=chat_id,
                                 bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                else:
                    send_telegram(f"💰 <b>AVAILABLE BALANCE</b>: {balance:.2f} USDT", chat_id=chat_id,
                                 bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            except Exception as e:
                send_telegram(f"⚠️ Balance error: {str(e)}", chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "📈 Vị thế":
            try:
                positions = get_positions(api_key=self.api_key, api_secret=self.api_secret)
                if not positions:
                    send_telegram("📭 No open positions", chat_id=chat_id,
                                 bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
                    return
                
                message = "📈 <b>OPEN POSITIONS</b>\n\n"
                for pos in positions:
                    position_amt = float(pos.get('positionAmt', 0))
                    if position_amt != 0:
                        symbol = pos.get('symbol', 'UNKNOWN')
                        entry = float(pos.get('entryPrice', 0))
                        side = "LONG" if position_amt > 0 else "SHORT"
                        pnl = float(pos.get('unRealizedProfit', 0))
                        
                        message += (f"🔹 {symbol} | {side}\n"
                                  f"📊 Quantity: {abs(position_amt):.4f}\n"
                                  f"🏷️ Entry: {entry:.4f}\n"
                                  f"💰 PnL: {pnl:.2f} USDT\n\n")
                
                send_telegram(message, chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            except Exception as e:
                send_telegram(f"⚠️ Positions error: {str(e)}", chat_id=chat_id,
                             bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "🎯 Chiến lược":
            strategy_info = (
                "🎯 <b>ADVANCED RSI + VOLUME SYSTEM</b>\n\n"
                "📈 <b>6 ENTRY CONDITIONS:</b>\n"
                "1. RSI > 80 + price up + volume up → SELL\n"
                "2. RSI < 20 + price down + volume down → SELL\n"  
                "3. RSI > 80 + price up + volume down → BUY\n"
                "4. RSI < 20 + price down + volume up → BUY\n"
                "5. RSI > 20 + price not down + volume down → BUY\n"
                "6. RSI < 80 + price not up + volume up → SELL\n\n"
                
                "🎯 <b>EXIT CONDITIONS:</b>\n"
                "• SAME AS entry conditions\n"
                "• But volume change 40% (instead of 20%)\n"
                "• AND must reach user-set ROI trigger\n"
                "• Only take profit, no reverse entry\n\n"
                
                "🔄 <b>TRUE SEQUENTIAL COORDINATION:</b>\n"
                "• Fixed sequential queue\n"
                "• Only 1 bot executes at a time\n"
                "• Executed bot moves to end of queue\n"
                "• 1s wait between bots\n\n"
                
                "🚫 <b>POSITION CHECKING:</b>\n"
                "• Auto detect coins with positions\n"
                "• No entry on coins with positions\n"
                "• Auto switch to other coins"
            )
            send_telegram(strategy_info, chat_id=chat_id,
                         bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text == "⚙️ Cấu hình":
            balance = get_balance(self.api_key, self.api_secret)
            api_status = "✅ Connected" if balance is not None else "❌ Connection error"
            
            total_bots_with_coins, trading_bots = 0, 0
            for bot in self.bots.values():
                if hasattr(bot, 'active_symbols'):
                    if len(bot.active_symbols) > 0: total_bots_with_coins += 1
                    for symbol, data in bot.symbol_data.items():
                        if data.get('position_open', False): trading_bots += 1
            
            config_info = (f"⚙️ <b>RSI + VOLUME SYSTEM CONFIG</b>\n\n"
                          f"🔑 Binance API: {api_status}\n🤖 Total bots: {len(self.bots)}\n"
                          f"📊 Bots with coins: {total_bots_with_coins}\n"
                          f"🟢 Trading bots: {trading_bots}\n"
                          f"🌐 WebSocket: {len(self.ws_manager.connections)} connections\n"
                          f"🔄 Cooldown: 1s\n📋 Queue: {self.bot_coordinator.get_queue_info()['queue_size']} bots\n\n"
                          f"🔄 <b>TRUE SEQUENTIAL MECHANISM ACTIVE</b>\n"
                          f"🎯 <b>6 RSI CONDITIONS ACTIVE</b>")
            send_telegram(config_info, chat_id=chat_id,
                         bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
        
        elif text:
            self.send_main_menu(chat_id)

    def _finish_bot_creation(self, chat_id, user_state):
        try:
            bot_mode = user_state.get('bot_mode', 'static')
            leverage = user_state.get('leverage')
            percent = user_state.get('percent')
            tp = user_state.get('tp')
            sl = user_state.get('sl')
            roi_trigger = user_state.get('roi_trigger')
            symbol = user_state.get('symbol')
            bot_count = user_state.get('bot_count', 1)
            
            success = self.add_bot(
                symbol=symbol, lev=leverage, percent=percent, tp=tp, sl=sl,
                roi_trigger=roi_trigger, strategy_type="RSI-Volume-System",
                bot_mode=bot_mode, bot_count=bot_count
            )
            
            if success:
                roi_info = f" | 🎯 ROI Trigger: {roi_trigger}%" if roi_trigger else ""
                
                success_msg = (f"✅ <b>BOT CREATED SUCCESSFULLY</b>\n\n"
                              f"🤖 Strategy: RSI + Volume System\n🔧 Mode: {bot_mode}\n"
                              f"🔢 Bot count: {bot_count}\n💰 Leverage: {leverage}x\n"
                              f"📊 % Balance: {percent}%\n🎯 TP: {tp}%\n"
                              f"🛡️ SL: {sl}%{roi_info}")
                if bot_mode == 'static' and symbol: success_msg += f"\n🔗 Coin: {symbol}"
                
                success_msg += (f"\n\n🔄 <b>QUEUE SYSTEM ACTIVATED</b>\n"
                              f"• First bot in queue finds coin first\n"
                              f"• Bot enters position → next bot finds IMMEDIATELY\n"
                              f"• Bots with coins cannot enter queue\n"
                              f"• Bots closing positions can re-enter queue\n\n"
                              f"⚡ <b>EACH BOT RUNS IN SEPARATE THREAD</b>")
                
                send_telegram(success_msg, chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            else:
                send_telegram("❌ Error creating bot. Please try again.",
                            chat_id=chat_id, reply_markup=create_main_menu(),
                            bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            
            self.user_states[chat_id] = {}
            
        except Exception as e:
            send_telegram(f"❌ Bot creation error: {str(e)}", chat_id=chat_id, reply_markup=create_main_menu(),
                        bot_token=self.telegram_bot_token, default_chat_id=self.telegram_chat_id)
            self.user_states[chat_id] = {}

# Bypass SSL verification
ssl._create_default_https_context = ssl._create_unverified_context
