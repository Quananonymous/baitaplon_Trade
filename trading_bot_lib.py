# trading_bot_lib_complete.py - HỆ THỐNG RSI + KHỐI LƯỢNG HOÀN CHỈNH
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
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import time
import ssl

# ========== BYPASS SSL VERIFICATION ==========
ssl._create_default_https_context = ssl._create_unverified_context

def _last_closed_1m_quote_volume(symbol):
    data = binance_api_request(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": symbol, "interval": "1m", "limit": 2}
    )
    if not data or len(data) < 2:
        return None
    k = data[-2]               # nến 1m đã đóng gần nhất
    return float(k[7])         # quoteVolume (USDC)

# ========== CẤU HÌNH LOGGING ==========
def setup_logging():
    logger = logging.getLogger("trading_bot_lib")
    logger.setLevel(logging.INFO)  # <-- ĐỔI WARNING -> INFO

    if not logger.handlers:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)  # <-- Đổi luôn

        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
        )
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        fh = logging.FileHandler("trading_bot_errors.log", encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger


# ========== HÀM HỖ TRỢ TELEGRAM ==========
def escape_html(text: str) -> str:
    if not text:
        return text
    return (
        text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
    )

def send_telegram(message, chat_id=None, reply_markup=None,
                  bot_token=None, default_chat_id=None):
    """
    Gửi message Telegram (HTML mode).
    Format hàm giữ nguyên như bản gốc.
    """
    if not bot_token:
        logger.warning("Telegram Bot Token chưa được thiết lập")
        return False

    chat_id = chat_id or default_chat_id
    if not chat_id:
        logger.warning("Telegram Chat ID chưa được thiết lập")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    safe_message = escape_html(message)

    payload = {
        "chat_id": chat_id,
        "text": safe_message,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            return True
        else:
            logger.error(f"Lỗi Telegram ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logger.error(f"Lỗi kết nối Telegram: {str(e)}")
        return False

# ========== KEYBOARD / MENU TELEGRAM (FORMAT CŨ) ==========
def create_cancel_keyboard():
    return {
        "keyboard": [[{"text": "❌ Hủy bỏ"}]],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def create_strategy_keyboard():
    return {
        "keyboard": [
            [{"text": "📊 Hệ thống RSI + Khối lượng"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def create_exit_strategy_keyboard():
    return {
        "keyboard": [
            [{"text": "🎯 Chỉ TP/SL cố định"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def create_bot_mode_keyboard():
    return {
        "keyboard": [
            [{"text": "🤖 Bot Tĩnh - Coin cụ thể"}, {"text": "🔄 Bot Động - Tự tìm coin"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def get_all_usdc_pairs(limit=100):
    try:
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        data = binance_api_request(url)
        if not data:
            logger.warning("Không lấy được exchangeInfo, trả về danh sách rỗng")
            return []
        
        usdc_pairs = [
            symbol_info["symbol"] 
            for symbol_info in data.get("symbols", []) 
            if symbol_info["symbol"].endswith("USDC") 
            and symbol_info.get("status") == "TRADING"
        ]
        
        return usdc_pairs[:limit] if limit else usdc_pairs
    except Exception as e:
        logger.error(f"Lỗi get_all_usdc_pairs: {str(e)}")
        return []

def create_symbols_keyboard(strategy=None):
    try:
        symbols = get_all_usdc_pairs(limit=12)
        if not symbols:
            symbols = [
                "BTCUSDC", "ETHUSDC", "BNBUSDC", "ADAUSDC",
                "DOGEUSDC", "XRPUSDC", "DOTUSDC", "LINKUSDC"
            ]
    except:
        symbols = [
            "BTCUSDC", "ETHUSDC", "BNBUSDC", "ADAUSDC",
            "DOGEUSDC", "XRPUSDC", "DOTUSDC", "LINKUSDC"
        ]
    
    keyboard = []
    row = []
    for symbol in symbols:
        row.append({"text": symbol})
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([{"text": "❌ Hủy bỏ"}])
    
    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def create_main_menu():
    return {
        "keyboard": [
            [{"text": "📊 Danh sách Bot"}, {"text": "📊 Thống kê"}],
            [{"text": "➕ Thêm Bot"}, {"text": "⛔ Dừng Bot"}],
            [{"text": "💰 Số dư"}, {"text": "📈 Vị thế"}],
            [{"text": "⚙️ Cấu hình"}, {"text": "🎯 Chiến lược"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

def create_leverage_keyboard(strategy=None):
    leverages = ["3", "5", "10", "15", "20", "25", "50", "75", "100", "125"]
    keyboard = []
    row = []
    
    for lev in leverages:
        row.append({"text": f"{lev}x"})
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([{"text": "❌ Hủy bỏ"}])
    
    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def create_percent_keyboard():
    return {
        "keyboard": [
            [{"text": "1"}, {"text": "3"}, {"text": "5"}, {"text": "10"}],
            [{"text": "15"}, {"text": "20"}, {"text": "25"}, {"text": "50"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def create_tp_keyboard():
    return {
        "keyboard": [
            [{"text": "50"}, {"text": "100"}, {"text": "200"}],
            [{"text": "300"}, {"text": "500"}, {"text": "1000"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def create_sl_keyboard():
    return {
        "keyboard": [
            [{"text": "0"}, {"text": "50"}, {"text": "100"}],
            [{"text": "150"}, {"text": "200"}, {"text": "500"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def create_bot_count_keyboard():
    return {
        "keyboard": [
            [{"text": "1"}, {"text": "2"}, {"text": "3"}],
            [{"text": "5"}, {"text": "10"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def create_roi_trigger_keyboard():
    return {
        "keyboard": [
            [{"text": "30"}, {"text": "50"}, {"text": "100"}],
            [{"text": "150"}, {"text": "200"}, {"text": "300"}],
            [{"text": "❌ Tắt tính năng"}],
            [{"text": "❌ Hủy bỏ"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

# ========== HÀM HỖ TRỢ KÝ VÀ GỌI API BINANCE ==========
def sign(query_string, api_secret):
    try:
        return hmac.new(
            api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    except Exception as e:
        logger.error(f"Lỗi tạo chữ ký: {str(e)}")
        return ""

def binance_api_request(url, method='GET', params=None, headers=None):
    """
    Gọi API Binance có retry, giữ nguyên format cũ.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Thêm User-Agent để tránh bị chặn
            if headers is None:
                headers = {}
            
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
            
            # Tăng timeout và thêm retry logic
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 200:
                    return json.loads(response.read().decode())
                else:
                    error_content = response.read().decode()
                    logger.error(f"Lỗi API ({response.status}): {error_content}")
                    if response.status == 401:
                        return None
                    if response.status == 429:
                        time.sleep(2 ** attempt)
                    elif response.status >= 500:
                        time.sleep(1)
                    continue
        
        except urllib.error.HTTPError as e:
            if e.code == 451:
                logger.error("Lỗi 451: Bị chặn truy cập (có thể do vùng địa lý / IP).")
                return None
            else:
                logger.error(f"Lỗi HTTPError ({e.code}): {e.reason}")
                if e.code == 401:
                    return None
                if e.code == 429:
                    time.sleep(2 ** attempt)
                elif e.code >= 500:
                    time.sleep(1)
                continue
        
        except Exception as e:
            msg = str(e)
            if "Name or service not known" in msg:
                logger.error("❌ Không phân giải được tên miền Binance (DNS). Môi trường không có mạng hoặc bị chặn.")
                return None
            logger.error(f"Lỗi kết nối API (lần {attempt+1}): {msg}")
            time.sleep(1)
    
    logger.error(f"Không thể thực hiện API sau {max_retries} lần thử")
    return None

def get_top_volume_symbols(limit=100):
    """
    Lấy top symbol theo quoteVolume 1m đã đóng.
    """
    try:
        universe = get_all_usdc_pairs(limit=100) or []
        if not universe:
            logger.warning("Không có USDC pair nào trong universe")
            return []
        
        scored_symbols = []
        max_workers = 8
        
        def worker(symbol):
            try:
                qv = _last_closed_1m_quote_volume(symbol)
                return symbol, qv
            except:
                return symbol, None
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(worker, sym) for sym in universe]
            for future in as_completed(futures):
                symbol, qv = future.result()
                if qv is not None:
                    scored_symbols.append((symbol, qv))
                time.sleep(0.5)
        
        scored_symbols.sort(key=lambda x: x[1], reverse=True)
        top_symbols = [sym for sym, _ in scored_symbols[:limit]]
        return top_symbols
    except Exception as e:
        logger.error(f"Lỗi get_top_volume_symbols: {str(e)}")
        return []

def get_max_leverage(symbol, api_key, api_secret):
    """Lấy đòn bẩy tối đa cho một symbol (giống bản 93 – không gây 401)."""
    try:
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        data = binance_api_request(url)
        if not data:
            return 100

        for s in data.get("symbols", []):
            if s.get("symbol", "").upper() == symbol.upper():
                for f in s.get("filters", []):
                    if f.get("filterType") == "LEVERAGE":
                        if "maxLeverage" in f:
                            return int(f["maxLeverage"])
                break

        # Không tìm thấy -> trả mặc định 100, hành vi y hệt file 93
        return 100
    except Exception as e:
        logger.error(f"Lỗi lấy leverage tối đa {symbol}: {str(e)}")
        return 100


def get_step_size(symbol, api_key, api_secret):
    if not symbol:
        logger.error("Không thể lấy step size: symbol là None")
        return 0.001
    
    try:
        exchange_info = binance_api_request("https://fapi.binance.com/fapi/v1/exchangeInfo")
        if not exchange_info:
            logger.warning("Không lấy được exchangeInfo, dùng step size mặc định 0.001")
            return 0.001

        for symbol_info in exchange_info.get("symbols", []):
            if symbol_info["symbol"] == symbol:
                for f in symbol_info["filters"]:
                    if f["filterType"] == "LOT_SIZE" and "stepSize" in f:
                        return float(f.get("stepSize", 0.001))
        logger.warning(f"Không tìm được LOT_SIZE stepSize cho {symbol}, dùng 0.001")
        return 0.001
    except Exception as e:
        logger.error(f"Lỗi lấy step size {symbol}: {str(e)}")
        return 0.001

def set_leverage(symbol, leverage, api_key, api_secret):
    if not symbol:
        logger.error("Không thể set leverage: symbol là None")
        return False
    
    try:
        ts = int(time.time() * 1000)
        params = {
            "symbol": symbol,
            "leverage": leverage,
            "timestamp": ts
        }
        query_string = urllib.parse.urlencode(params)
        signature = sign(query_string, api_secret)
        url = f"https://fapi.binance.com/fapi/v1/leverage?{query_string}&signature={signature}"
        headers = {'X-MBX-APIKEY': api_key}
        
        response = binance_api_request(url, method='POST', headers=headers)
        if response and 'leverage' in response:
            return True
        logger.error(f"Lỗi set leverage {symbol}: {response}")
        return False
    except Exception as e:
        logger.error(f"Lỗi set leverage {symbol}: {str(e)}")
        return False

def get_balance(api_key, api_secret):
    """
    Lấy số dư USDC khả dụng (availableBalance).
    """
    try:
        ts = int(time.time() * 1000)
        params = {"timestamp": ts}
        query_string = urllib.parse.urlencode(params)
        signature = sign(query_string, api_secret)
        url = f"https://fapi.binance.com/fapi/v2/account?{query_string}&signature={signature}"
        headers = {'X-MBX-APIKEY': api_key}
        
        response = binance_api_request(url, method='GET', headers=headers)
        if not response:
            logger.error("Không thể lấy thông tin account")
            return None
        
        for asset in response.get("assets", []):
            if asset.get("asset") == "USDC":
                available_balance = float(asset.get("availableBalance", 0))
                total_balance = float(asset.get("walletBalance", 0))
                logger.info(f"Số dư USDC: avail={available_balance:.2f}, total={total_balance:.2f}")
                return available_balance
        
        logger.warning("Không tìm thấy USDC trong tài khoản")
        return 0
    except Exception as e:
        logger.error(f"Lỗi get_balance: {str(e)}")
        return None

def place_order(symbol, side, quantity, api_key, api_secret):
    if not symbol:
        logger.error("Không thể đặt lệnh: symbol là None")
        return None
    
    try:
        ts = int(time.time() * 1000)
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "timestamp": ts
        }
        query_string = urllib.parse.urlencode(params)
        signature = sign(query_string, api_secret)
        url = f"https://fapi.binance.com/fapi/v1/order?{query_string}&signature={signature}"
        headers = {'X-MBX-APIKEY': api_key}
        
        return binance_api_request(url, method='POST', headers=headers)
    except Exception as e:
        logger.error(f"Lỗi đặt lệnh: {str(e)}")
    return None

def cancel_all_orders(symbol, api_key, api_secret):
    if not symbol:
        logger.error("❌ Không thể hủy lệnh: symbol là None")
        return False
    try:
        ts = int(time.time() * 1000)
        params = {"symbol": symbol, "timestamp": ts}
        query_string = urllib.parse.urlencode(params)
        signature = sign(query_string, api_secret)
        url = f"https://fapi.binance.com/fapi/v1/allOpenOrders?{query_string}&signature={signature}"
        headers = {'X-MBX-APIKEY': api_key}
        
        _ = binance_api_request(url, method='DELETE', headers=headers)
        return True
    except Exception as e:
        logger.error(f"Lỗi hủy tất cả lệnh {symbol}: {str(e)}")
        return False

def get_current_price(symbol):
    if not symbol:
        logger.error("Không thể lấy giá hiện tại: symbol là None")
        return 0
    try:
        url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}"
        data = binance_api_request(url)
        if data and "price" in data:
            price = float(data["price"])
            if price > 0:
                return price
        logger.warning(f"Giá hiện tại cho {symbol} không hợp lệ hoặc không có")
        return 0
    except Exception as e:
        logger.error(f"Lỗi lấy giá hiện tại {symbol}: {str(e)}")
        return 0

def get_position_summary(api_key, api_secret):
    """
    Lấy danh sách vị thế đang mở (format cũ).
    """
    try:
        ts = int(time.time() * 1000)
        params = {"timestamp": ts}
        query_string = urllib.parse.urlencode(params)
        signature = sign(query_string, api_secret)
        url = f"https://fapi.binance.com/fapi/v2/positionRisk?{query_string}&signature={signature}"
        headers = {'X-MBX-APIKEY': api_key}
        
        positions = binance_api_request(url, headers=headers)
        if not positions:
            return []
        
        open_positions = []
        for pos in positions:
            amt = float(pos.get("positionAmt", 0))
            if amt != 0:
                open_positions.append(pos)
        return open_positions
    except Exception as e:
        logger.error(f"Lỗi get_position_summary: {str(e)}")
        return []

# ========== COIN MANAGER ==========
class CoinManager:
    """
    Quản lý tập coin đang được các bot sử dụng để tránh trùng lặp.
    """
    def __init__(self):
        self.active_coins = set()
        self._lock = threading.Lock()

    def register_coin(self, symbol):
        if not symbol:
            return
        with self._lock:
            self.active_coins.add(symbol)

    def unregister_coin(self, symbol):
        if not symbol:
            return
        with self._lock:
            self.active_coins.discard(symbol)

    def is_coin_active(self, symbol):
        if not symbol:
            return False
        with self._lock:
            return symbol in self.active_coins

    def get_active_coins(self):
        with self._lock:
            return list(self.active_coins)

# ========== SMART COIN FINDER (GIỮ FORMAT CŨ + LOGIC RSI MỚI) ==========
class SmartCoinFinder:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        
    def get_symbol_leverage(self, symbol):
        """Lấy đòn bẩy tối đa của symbol"""
        return get_max_leverage(symbol, self.api_key, self.api_secret)
    
    def calculate_rsi(self, prices, period=14):
        """Tính RSI từ danh sách giá"""
        if len(prices) < period + 1:
            return 50  # Giá trị trung bình nếu không đủ dữ liệu
            
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def get_rsi_signal(self, symbol, volume_threshold=9):
        """
        Logic RSI + khối lượng MỚI theo đúng 6 điều kiện bạn yêu cầu.
        """
        try:
            data = binance_api_request(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": symbol, "interval": "5m", "limit": 15}
            )
            if not data or len(data) < 15:
                return None
            
            prev_candle = data[-3]     # nến trước
            current_candle = data[-2]  # nến hiện tại
            latest_candle = data[-1]   # nến mới nhất (có thể chưa đóng)
            
            # Giá đóng cửa
            closes = [float(k[4]) for k in data]
            rsi_current = self.calculate_rsi(closes)
            
            prev_close = float(prev_candle[4])
            current_close = float(current_candle[4])
            latest_close = float(latest_candle[4]) if len(latest_candle) > 4 else current_close
            
            # Khối lượng
            prev_volume = float(prev_candle[5])
            current_volume = float(current_candle[5])
            
            # Xu hướng giá
            price_increase = current_close > prev_close
            price_decrease = current_close < prev_close
            
            # Xu hướng khối lượng
            volume_increase = current_volume > prev_volume * (1 + volume_threshold/100)
            volume_decrease = current_volume < prev_volume * (1 - volume_threshold/100)
            
            # 1) RSI > 80 + price increase + volume increase → SELL
            if rsi_current > 80 and price_increase and volume_increase:
                return "SELL"
            
            # 2) RSI < 20 + price decrease + volume decrease → SELL
            if rsi_current < 20 and price_decrease and volume_decrease:
                return "SELL"
            
            # 3) RSI > 80 + price increase + volume decrease → BUY
            if rsi_current > 80 and price_increase and volume_decrease:
                return "BUY"
            
            # 4) RSI < 20 + price decrease + volume increase → BUY
            if rsi_current < 20 and price_decrease and volume_increase:
                return "BUY"
            
            # 5) RSI > 20 + no price decrease + volume decrease → BUY
            if rsi_current > 20 and (not price_decrease) and volume_decrease:
                return "BUY"
            
            # 6) RSI < 80 + no price increase + volume increase → SELL
            if rsi_current < 80 and (not price_increase) and volume_increase:
                return "SELL"
            
            return None
        except Exception as e:
            logger.error(f"Lỗi phân tích RSI {symbol}: {str(e)}")
            return None
    
    def get_entry_signal(self, symbol):
        """Tín hiệu vào lệnh dùng RSI + volume"""
        return self.get_rsi_signal(symbol, volume_threshold=9)
    
    def get_exit_signal(self, symbol):
        """Tín hiệu thoát lệnh (có thể dùng threshold khác)"""
        return self.get_rsi_signal(symbol, volume_threshold=20)
    
    def has_existing_position(self, symbol):
        """Kiểm tra xem symbol đã có vị thế trên Binance chưa"""
        try:
            positions = get_position_summary(self.api_key, self.api_secret)
            if not positions:
                return False
            
            for pos in positions:
                if pos.get("symbol") == symbol:
                    amt = float(pos.get("positionAmt", 0))
                    if abs(amt) > 0:
                        logger.info(f"Đã có vị thế với {symbol}: {amt}")
                        return True
            return False
        except Exception as e:
            logger.error(f"Lỗi kiểm tra vị thế {symbol}: {str(e)}")
            return True
    
    def find_best_coin(self, target_direction, excluded_coins=None, required_leverage=10):
        """Tìm coin tốt nhất - format cũ, mỗi coin độc lập"""
        try:
            all_symbols = get_all_usdc_pairs(limit=50)
            if not all_symbols:
                return None
            
            valid_symbols = []
            
            for symbol in all_symbols:
                # Coin đã bị loại trừ?
                if excluded_coins and symbol in excluded_coins:
                    continue
                
                # Coin đã có vị thế trên Binance?
                if self.has_existing_position(symbol):
                    logger.info(f"🚫 Bỏ qua {symbol} - đã có vị thế trên Binance")
                    continue
                
                # Đòn bẩy tối đa không đủ?
                max_lev = self.get_symbol_leverage(symbol)
                if max_lev < required_leverage:
                    logger.info(f"🚫 Bỏ qua {symbol} - max lev {max_lev}x < required {required_leverage}x")
                    continue
                
                # Kiểm tra tín hiệu vào lệnh
                entry_signal = self.get_entry_signal(symbol)
                if entry_signal == target_direction:
                    valid_symbols.append(symbol)
            
            if not valid_symbols:
                logger.info("Không tìm được coin phù hợp theo tín hiệu và điều kiện")
                return None
            
            selected_symbol = random.choice(valid_symbols)
            max_lev = self.get_symbol_leverage(selected_symbol)
            
            # Kiểm tra lần cuối
            if self.has_existing_position(selected_symbol):
                logger.info(f"🚫 {selected_symbol} - Coin được chọn đã có vị thế, bỏ qua")
                return None
            
            logger.info(f"✅ Đã chọn coin: {selected_symbol} - Tín hiệu: {target_direction} - Đòn bẩy: {max_lev}x")
            return selected_symbol
        except Exception as e:
            logger.error(f"❌ Lỗi tìm coin: {str(e)}")
            return None

# ========== WEBSOCKET MANAGER ==========
class WebSocketManager:
    def __init__(self):
        self.connections = {}
        self.executor = ThreadPoolExecutor(max_workers=10)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def add_symbol(self, symbol, callback):
        if not symbol:
            return
        symbol = symbol.upper()
        with self._lock:
            if symbol not in self.connections:
                self._create_connection(symbol, callback)

    def _create_connection(self, symbol, callback):
        if self._stop_event.is_set():
            return
        
        stream = f"{symbol.lower()}@trade"
        url = f"wss://fstream.binance.com/ws/{stream}"

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if "p" in data:
                    price = float(data["p"])
                    self.executor.submit(callback, price)
            except Exception as e:
                logger.error(f"Lỗi xử lý message WebSocket {symbol}: {str(e)}")

        def on_error(ws, error):
            logger.error(f"Lỗi WebSocket {symbol}: {error}")
            if not self._stop_event.is_set():
                time.sleep(5)
                self._reconnect(symbol, callback)

        def on_close(ws, close_status_code, close_msg):
            logger.info(f"WebSocket đóng {symbol}: {close_status_code}, {close_msg}")
            if not self._stop_event.is_set() and symbol in self.connections:
                time.sleep(5)
                self._reconnect(symbol, callback)

        ws = websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        thread = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 20}, daemon=True)
        thread.start()
        
        self.connections[symbol] = {"ws": ws, "thread": thread, "callback": callback}
        logger.info(f"Đã start WebSocket cho {symbol}")

    def _reconnect(self, symbol, callback):
        logger.info(f"Reconnect WebSocket cho {symbol}")
        self.remove_symbol(symbol)
        self._create_connection(symbol, callback)

    def remove_symbol(self, symbol):
        if not symbol:
            return
        symbol = symbol.upper()
        with self._lock:
            conn = self.connections.get(symbol)
            if conn:
                try:
                    conn["ws"].close()
                except Exception as e:
                    logger.error(f"Lỗi đóng WebSocket {symbol}: {str(e)}")
                del self.connections[symbol]

    def stop(self):
        self._stop_event.set()
        for symbol in list(self.connections.keys()):
            self.remove_symbol(symbol)

# ========== BASE BOT (GIAO DỊCH NỐI TIẾP, FORMAT CŨ) ==========
class BaseBot:
    def __init__(
        self,
        symbol,
        leverage,
        position_percent,
        take_profit,
        stop_loss,
        roi_trigger,
        ws_manager,
        api_key,
        api_secret,
        telegram_bot_token,
        telegram_chat_id,
        strategy_name,
        config_key=None,
        bot_id=None,
        coin_manager=None,
        symbol_locks=None,
        max_coins=1
    ):
        # Cấu hình cơ bản
        self.symbol = symbol.upper() if symbol else None
        self.leverage = leverage
        self.position_percent = position_percent
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.roi_trigger = roi_trigger
        
        self.ws_manager = ws_manager
        self.api_key = api_key
        self.api_secret = api_secret
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.strategy_name = strategy_name
        self.config_key = config_key
        
        self.bot_id = bot_id or f"{strategy_name}_{int(time.time())}_{random.randint(1000, 9999)}"
        
        # Thông tin trạng thái toàn bot
        self.status = "searching"
        self._stop = False
        
        # Cài đặt nối tiếp & quản lý multi-coin
        self.max_coins = max_coins
        self.active_symbols = []
        self.symbol_data = {}
        
        self.current_processing_symbol = None
        self.last_trade_completion_time = 0
        self.trade_cooldown = 3
        
        # Thống kê toàn tài khoản
        self.last_global_position_check = 0
        self.last_error_log_time = 0
        self.global_position_check_interval = 10
        self.global_long_count = 0
        self.global_short_count = 0
        self.global_long_pnl = 0
        self.global_short_pnl = 0
        
        # Quản lý coin toàn hệ thống
        self.coin_manager = coin_manager or CoinManager()
        self.symbol_locks = symbol_locks or defaultdict(threading.Lock)
        self.smart_finder = SmartCoinFinder(api_key, api_secret)
        
        # Flag: sau khi đóng hết sẽ tìm coin mới
        self.find_new_bot_after_close = True
        self.bot_creation_time = time.time()
        
        # Lock quản lý symbol
        self.symbol_management_lock = threading.Lock()
        
        # Nếu có symbol ban đầu -> thêm ngay nếu chưa có vị thế
        if self.symbol and not self.smart_finder.has_existing_position(self.symbol):
            self._add_symbol(self.symbol)
        
        # Thread chính
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        
        roi_info = f" | ROI Trigger: {roi_trigger}%" if roi_trigger else " | ROI Trigger: Tắt"
        self.log(
            f"🟢 Bot {self.strategy_name} KHỞI ĐỘNG | "
            f"Max coins: {self.max_coins} | Lev: {self.leverage}x | "
            f"Vốn: {self.position_percent}% | TP/SL: {self.take_profit}%/{self.stop_loss}%{roi_info}"
        )

    # ========== LOG ==========
    def log(self, message):
        important_keywords = ['❌', '✅', '⛔', '💰', '📈', '📊', '🎯', '🛡️', '🔴', '🟢', '⚠️', '🚫']
        prefix = f"[{self.bot_id}]"
        
        if any(k in message for k in important_keywords):
            logger.warning(f"{prefix} {message}")
            if self.telegram_bot_token and self.telegram_chat_id:
                send_telegram(
                    f"<b>{self.bot_id}</b>: {message}",
                    chat_id=self.telegram_chat_id,
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
        else:
            logger.info(f"{prefix} {message}")

    # ========== VÒNG LẶP CHÍNH (NỐI TIẾP) ==========
    def _run(self):
        while not self._stop:
            try:
                now = time.time()
                
                # Check vị thế toàn tài khoản định kỳ
                if now - self.last_global_position_check > self.global_position_check_interval:
                    self.check_global_positions()
                    self.last_global_position_check = now
                
                # Cooldown giữa các lần xử lý
                if now - self.last_trade_completion_time < self.trade_cooldown:
                    time.sleep(0.5)
                    continue
                
                # Luôn cố gắng bổ sung coin mới nếu chưa đủ
                if len(self.active_symbols) < self.max_coins:
                    if self._find_and_add_new_coin():
                        self.last_trade_completion_time = time.time()
                        time.sleep(3)
                        continue
                
                if self.active_symbols:
                    # Chỉ xử lý chính 1 coin
                    symbol_to_process = self.active_symbols[0]
                    self.current_processing_symbol = symbol_to_process
                    
                    # Xử lý coin chính
                    self._process_single_symbol(symbol_to_process)
                    
                    # Check TP/SL + nhồi cho các coin còn lại
                    for sym in self.active_symbols:
                        if sym != symbol_to_process:
                            self._check_symbol_tp_sl(sym)
                            self._check_symbol_averaging_down(sym)
                    
                    self.last_trade_completion_time = time.time()
                    time.sleep(3)
                    
                    # Xoay vòng danh sách
                    if len(self.active_symbols) > 1:
                        self.active_symbols.append(self.active_symbols.pop(0))
                    
                    self.current_processing_symbol = None
                else:
                    # Không có coin -> nghỉ lâu hơn
                    time.sleep(5)
            except Exception as e:
                if time.time() - self.last_error_log_time > 10:
                    self.log(f"❌ Lỗi trong vòng lặp chính: {str(e)}")
                    self.last_error_log_time = time.time()
                time.sleep(1)

    # ========== TÌM COIN MỚI ==========
    def _find_and_add_new_coin(self):
        with self.symbol_management_lock:
            try:
                if len(self.active_symbols) >= self.max_coins:
                    return False
                
                active_coins = self.coin_manager.get_active_coins()
                
                target_direction = self.get_next_side_based_on_comprehensive_analysis()
                new_symbol = self.smart_finder.find_best_coin(
                    target_direction=target_direction,
                    excluded_coins=active_coins,
                    required_leverage=self.leverage
                )
                
                if not new_symbol:
                    return False
                
                if self.smart_finder.has_existing_position(new_symbol):
                    self.log(f"🚫 {new_symbol} - phát hiện có vị thế thật, bỏ qua")
                    return False
                
                if self._add_symbol(new_symbol):
                    self.log(f"✅ Thêm coin mới: {new_symbol} (tổng {len(self.active_symbols)})")
                    time.sleep(1)
                    
                    if self.smart_finder.has_existing_position(new_symbol):
                        self.log(f"🚫 {new_symbol} - có vị thế thật sau khi thêm, dừng theo dõi")
                        self.stop_symbol(new_symbol)
                        return False
                    return True
                return False
            except Exception as e:
                self.log(f"❌ Lỗi _find_and_add_new_coin: {str(e)}")
                return False

    def _add_symbol(self, symbol):
        with self.symbol_management_lock:
            if symbol in self.active_symbols:
                return False
            if len(self.active_symbols) >= self.max_coins:
                return False
            if self.smart_finder.has_existing_position(symbol):
                return False
            
            self.symbol_data[symbol] = {
                "status": "waiting",
                "side": "",
                "quantity": 0,
                "entry_price": 0,
                "current_price": 0,
                "position_open": False,
                "last_trade_time": 0,
                "last_close_time": 0,
                "entry_base_price": 0,
                "average_down_count": 0,
                "last_average_down_time": 0,
                "high_water_mark_roi": 0,
                "roi_check_activated": False,
                "close_attempted": False,
                "last_close_attempt_time": 0,
                "last_position_check": 0,
            }
            
            self.active_symbols.append(symbol)
            self.coin_manager.register_coin(symbol)
            self.ws_manager.add_symbol(symbol, lambda price, sym=symbol: self._handle_price_update(sym, price))
            
            self._check_symbol_position(symbol)
            if self.symbol_data[symbol]["position_open"]:
                self.stop_symbol(symbol)
                return False
            return True

    def _handle_price_update(self, symbol, price):
        if symbol in self.symbol_data:
            self.symbol_data[symbol]["current_price"] = price

    # ========== QUẢN LÝ VỊ THẾ THEO SYMBOL ==========
    def _check_symbol_position(self, symbol):
        try:
            positions = get_position_summary(self.api_key, self.api_secret)
            if not positions:
                self._reset_symbol_position(symbol)
                return
            
            found = False
            for pos in positions:
                if pos.get("symbol") == symbol:
                    amt = float(pos.get("positionAmt", 0))
                    if abs(amt) > 0:
                        found = True
                        data = self.symbol_data[symbol]
                        data["position_open"] = True
                        data["status"] = "open"
                        data["side"] = "BUY" if amt > 0 else "SELL"
                        data["quantity"] = amt
                        data["entry_price"] = float(pos.get("entryPrice", 0))
                        
                        current_price = get_current_price(symbol)
                        if current_price > 0 and self.roi_trigger:
                            if data["side"] == "BUY":
                                profit = (current_price - data["entry_price"]) * abs(data["quantity"])
                            else:
                                profit = (data["entry_price"] - current_price) * abs(data["quantity"])
                            invested = data["entry_price"] * abs(data["quantity"]) / self.leverage
                            if invested > 0:
                                roi = profit / invested * 100
                                if roi >= self.roi_trigger:
                                    data["roi_check_activated"] = True
                        break
                    else:
                        found = True
                        self._reset_symbol_position(symbol)
                        break
            if not found:
                self._reset_symbol_position(symbol)
        except Exception as e:
            self.log(f"❌ Lỗi _check_symbol_position {symbol}: {str(e)}")

    def _reset_symbol_position(self, symbol):
        if symbol in self.symbol_data:
            data = self.symbol_data[symbol]
            data["position_open"] = False
            data["status"] = "waiting"
            data["side"] = ""
            data["quantity"] = 0
            data["entry_price"] = 0
            data["close_attempted"] = False
            data["last_close_attempt_time"] = 0
            data["entry_base_price"] = 0
            data["average_down_count"] = 0
            data["high_water_mark_roi"] = 0
            data["roi_check_activated"] = False

    # ========== XỬ LÝ 1 SYMBOL ==========
    def _process_single_symbol(self, symbol):
        try:
            data = self.symbol_data[symbol]
            now = time.time()
            
            if now - data.get("last_position_check", 0) > 30:
                self._check_symbol_position(symbol)
                data["last_position_check"] = now
            
            if self.smart_finder.has_existing_position(symbol) and not data["position_open"]:
                self.log(f"⚠️ {symbol} - phát hiện có vị thế thật, dừng theo dõi")
                self.stop_symbol(symbol)
                return False
            
            if data["position_open"]:
                if self._check_smart_exit_condition(symbol):
                    return True
                self._check_symbol_tp_sl(symbol)
                self._check_symbol_averaging_down(symbol)
            else:
                if (now - data["last_trade_time"] > 60 
                    and now - data["last_close_time"] > 3600):
                    
                    target_side = self.get_next_side_based_on_comprehensive_analysis()
                    entry_signal = self.smart_finder.get_entry_signal(symbol)
                    
                    if entry_signal == target_side:
                        if self.smart_finder.has_existing_position(symbol):
                            self.log(f"🚫 {symbol} - đã có vị thế thật, bỏ qua")
                            self.stop_symbol(symbol)
                            return False
                        
                        if self._open_symbol_position(symbol, target_side):
                            data["last_trade_time"] = now
                            return True
            return False
        except Exception as e:
            self.log(f"❌ Lỗi _process_single_symbol {symbol}: {str(e)}")
            return False

    # ========== MỞ / ĐÓNG VỊ THẾ ==========
    def _open_symbol_position(self, symbol, side):
        try:
            if self.smart_finder.has_existing_position(symbol):
                self.log(f"⚠️ {symbol} đã có vị thế, bỏ qua")
                self.stop_symbol(symbol)
                return False
            
            self._check_symbol_position(symbol)
            if self.symbol_data[symbol]["position_open"]:
                return False
            
            current_leverage = self.smart_finder.get_symbol_leverage(symbol)
            if current_leverage < self.leverage:
                self.log(f"❌ {symbol} leverage không đủ: {current_leverage}x < {self.leverage}x")
                self.stop_symbol(symbol)
                return False
            
            if not set_leverage(symbol, self.leverage, self.api_key, self.api_secret):
                self.log(f"❌ {symbol} không set được leverage")
                self.stop_symbol(symbol)
                return False
            
            balance = get_balance(self.api_key, self.api_secret)
            if not balance or balance <= 0:
                self.log(f"❌ {symbol} không đủ số dư")
                return False
            
            current_price = get_current_price(symbol)
            if current_price <= 0:
                self.log(f"❌ {symbol} lỗi giá")
                self.stop_symbol(symbol)
                return False
            
            step_size = get_step_size(symbol, self.api_key, self.api_secret)
            usd_amount = balance * (self.position_percent / 100)
            quantity = (usd_amount * self.leverage) / current_price
            
            if step_size > 0:
                quantity = math.floor(quantity / step_size) * step_size
                quantity = round(quantity, 8)
            if quantity <= 0 or quantity < step_size:
                self.log(f"❌ {symbol} khối lượng không hợp lệ")
                self.stop_symbol(symbol)
                return False
            
            cancel_all_orders(symbol, self.api_key, self.api_secret)
            time.sleep(0.2)
            
            result = place_order(symbol, side, quantity, self.api_key, self.api_secret)
            if result and "orderId" in result:
                executed_qty = float(result.get("executedQty", 0))
                avg_price = float(result.get("avgPrice", current_price))
                if executed_qty >= 0:
                    time.sleep(1)
                    self._check_symbol_position(symbol)
                    if not self.symbol_data[symbol]["position_open"]:
                        self.log(f"❌ {symbol} lệnh khớp nhưng không tạo vị thế")
                        self.stop_symbol(symbol)
                        return False
                    
                    data = self.symbol_data[symbol]
                    data["entry_price"] = avg_price
                    data["entry_base_price"] = avg_price
                    data["average_down_count"] = 0
                    data["side"] = side
                    data["quantity"] = executed_qty if side == "BUY" else -executed_qty
                    data["position_open"] = True
                    data["status"] = "open"
                    data["high_water_mark_roi"] = 0
                    data["roi_check_activated"] = False
                    
                    msg = (
                        f"✅ <b>MỞ VỊ THẾ {symbol}</b>\n"
                        f"🤖 Bot: {self.bot_id}\n"
                        f"📌 Hướng: {side}\n"
                        f"🏷️ Giá vào: {avg_price:.4f}\n"
                        f"📊 Khối lượng: {executed_qty:.4f}\n"
                        f"💰 Đòn bẩy: {self.leverage}x\n"
                        f"🎯 TP: {self.take_profit}% | 🛡️ SL: {self.stop_loss}%"
                    )
                    if self.roi_trigger:
                        msg += f" | ROI Trigger: {self.roi_trigger}%"
                    self.log(msg)
                    return True
                else:
                    self.log(f"❌ {symbol} lệnh không khớp")
                    self.stop_symbol(symbol)
                    return False
            else:
                err_msg = result.get("msg", "Unknown") if result else "No response"
                self.log(f"❌ {symbol} lỗi đặt lệnh: {err_msg}")
                self.stop_symbol(symbol)
                return False
        except Exception as e:
            self.log(f"❌ {symbol} lỗi _open_symbol_position: {str(e)}")
            self.stop_symbol(symbol)
            return False

    def _close_symbol_position(self, symbol, reason=""):
        try:
            self._check_symbol_position(symbol)
            data = self.symbol_data[symbol]
            if not data["position_open"] or abs(data["quantity"]) <= 0:
                return True
            
            now = time.time()
            if data["close_attempted"] and now - data["last_close_attempt_time"] < 30:
                return False
            
            data["close_attempted"] = True
            data["last_close_attempt_time"] = now
            
            close_side = "SELL" if data["side"] == "BUY" else "BUY"
            close_qty = abs(data["quantity"])
            
            cancel_all_orders(symbol, self.api_key, self.api_secret)
            time.sleep(0.5)
            
            result = place_order(symbol, close_side, close_qty, self.api_key, self.api_secret)
            if result and "orderId" in result:
                current_price = get_current_price(symbol)
                pnl = 0
                if data["entry_price"] > 0:
                    if data["side"] == "BUY":
                        pnl = (current_price - data["entry_price"]) * abs(data["quantity"])
                    else:
                        pnl = (data["entry_price"] - current_price) * abs(data["quantity"])
                
                msg = (
                    f"⛔ <b>ĐÓNG VỊ THẾ {symbol}</b>\n"
                    f"🤖 Bot: {self.bot_id}\n"
                    f"📌 Lý do: {reason}\n"
                    f"🏷️ Giá ra: {current_price:.4f}\n"
                    f"📊 Khối lượng: {close_qty:.4f}\n"
                    f"💰 PnL: {pnl:.2f} USDC\n"
                    f"📈 Số lần nhồi: {data['average_down_count']}"
                )
                self.log(msg)
                data["last_close_time"] = time.time()
                self._reset_symbol_position(symbol)
                return True
            else:
                err_msg = result.get("msg", "Unknown") if result else "No response"
                self.log(f"❌ {symbol} lỗi đóng lệnh: {err_msg}")
                data["close_attempted"] = False
                return False
        except Exception as e:
            self.log(f"❌ {symbol} lỗi _close_symbol_position: {str(e)}")
            self.symbol_data[symbol]["close_attempted"] = False
            return False

    # ========== TP/SL + ROI TRIGGER ==========
    def _check_smart_exit_condition(self, symbol):
        try:
            data = self.symbol_data[symbol]
            if not data["position_open"] or not data["roi_check_activated"]:
                return False
            
            current_price = get_current_price(symbol)
            if current_price <= 0:
                return False
            
            if data["side"] == "BUY":
                profit = (current_price - data["entry_price"]) * abs(data["quantity"])
            else:
                profit = (data["entry_price"] - current_price) * abs(data["quantity"])
            
            invested = data["entry_price"] * abs(data["quantity"]) / self.leverage
            if invested <= 0:
                return False
            
            roi = profit / invested * 100
            
            if roi >= self.roi_trigger:
                exit_signal = self.smart_finder.get_exit_signal(symbol)
                if exit_signal:
                    reason = f"🎯 ROI {self.roi_trigger}% + tín hiệu exit (ROI: {roi:.2f}%)"
                    self._close_symbol_position(symbol, reason)
                    return True
            return False
        except Exception as e:
            self.log(f"❌ {symbol} lỗi _check_smart_exit_condition: {str(e)}")
            return False

    def _check_symbol_tp_sl(self, symbol):
        data = self.symbol_data[symbol]
        if (
            not data["position_open"]
            or data["entry_price"] <= 0
            or data["close_attempted"]
        ):
            return False
        
        current_price = get_current_price(symbol)
        if current_price <= 0:
            return False
        
        if data["side"] == "BUY":
            profit = (current_price - data["entry_price"]) * abs(data["quantity"])
        else:
            profit = (data["entry_price"] - current_price) * abs(data["quantity"])
        
        invested = data["entry_price"] * abs(data["quantity"]) / self.leverage
        if invested <= 0:
            return False
        
        roi = profit / invested * 100
        
        if roi > data["high_water_mark_roi"]:
            data["high_water_mark_roi"] = roi
        
        if (
            self.roi_trigger is not None
            and data["high_water_mark_roi"] >= self.roi_trigger
            and not data["roi_check_activated"]
        ):
            data["roi_check_activated"] = True
        
        closed = False
        if self.take_profit is not None and roi >= self.take_profit:
            self._close_symbol_position(symbol, f"✅ Đạt TP {self.take_profit}% (ROI: {roi:.2f}%)")
            closed = True
        elif self.stop_loss is not None and self.stop_loss > 0 and roi <= -self.stop_loss:
            self._close_symbol_position(symbol, f"❌ Đạt SL {self.stop_loss}% (ROI: {roi:.2f}%)")
            closed = True
        
        return closed

    # ========== NHỒI LỆNH FIBONACCI ==========
    def _check_symbol_averaging_down(self, symbol):
        data = self.symbol_data[symbol]
        if (
            not data["position_open"]
            or not data["entry_base_price"]
            or data["average_down_count"] >= 7
        ):
            return False
        try:
            now = time.time()
            if now - data["last_average_down_time"] < 60:
                return False
            
            current_price = get_current_price(symbol)
            if current_price <= 0:
                return False
            
            if data["side"] == "BUY":
                profit = (current_price - data["entry_base_price"]) * abs(data["quantity"])
            else:
                profit = (data["entry_base_price"] - current_price) * abs(data["quantity"])
            
            invested = data["entry_base_price"] * abs(data["quantity"]) / self.leverage
            if invested <= 0:
                return False
            
            roi = profit / invested * 100
            if roi >= 0:
                return False
            
            roi_negative = abs(roi)
            fib_levels = [200, 300, 500, 800, 1300, 2100, 3400]
            
            if data["average_down_count"] < len(fib_levels):
                target = fib_levels[data["average_down_count"]]
                if roi_negative >= target:
                    if self._execute_symbol_average_down(symbol):
                        data["last_average_down_time"] = now
                        data["average_down_count"] += 1
                        self.log(f"📈 {symbol} nhồi Fibonacci mốc {target}% lỗ")
                        return True
            return False
        except Exception as e:
            self.log(f"❌ {symbol} lỗi _check_symbol_averaging_down: {str(e)}")
            return False

    def _execute_symbol_average_down(self, symbol):
        try:
            data = self.symbol_data[symbol]
            balance = get_balance(self.api_key, self.api_secret)
            if not balance or balance <= 0:
                return False
            
            current_price = get_current_price(symbol)
            if current_price <= 0:
                return False
            
            add_percent = self.position_percent * (data["average_down_count"] + 1)
            usd_amount = balance * (add_percent / 100)
            quantity = (usd_amount * self.leverage) / current_price
            
            step_size = get_step_size(symbol, self.api_key, self.api_secret)
            if step_size > 0:
                quantity = math.floor(quantity / step_size) * step_size
                quantity = round(quantity, 8)
            if quantity < step_size:
                return False
            
            result = place_order(symbol, data["side"], quantity, self.api_key, self.api_secret)
            if result and "orderId" in result:
                executed_qty = float(result.get("executedQty", 0))
                avg_price = float(result.get("avgPrice", current_price))
                if executed_qty >= 0:
                    total_qty = abs(data["quantity"]) + executed_qty
                    new_entry = (
                        abs(data["quantity"]) * data["entry_price"]
                        + executed_qty * avg_price
                    ) / total_qty
                    data["entry_price"] = new_entry
                    data["quantity"] = total_qty if data["side"] == "BUY" else -total_qty
                    
                    msg = (
                        f"📈 <b>NHỒI LỆNH {symbol}</b>\n"
                        f"🔢 Lần nhồi: {data['average_down_count'] + 1}\n"
                        f"📊 Thêm: {executed_qty:.4f}\n"
                        f"🏷️ Giá nhồi: {avg_price:.4f}\n"
                        f"📈 Entry mới: {new_entry:.4f}\n"
                        f"💰 Tổng khối lượng: {total_qty:.4f}"
                    )
                    self.log(msg)
                    return True
            return False
        except Exception as e:
            self.log(f"❌ {symbol} lỗi _execute_symbol_average_down: {str(e)}")
            return False

    # ========== DỪNG SYMBOL / BOT ==========
    def stop_symbol(self, symbol):
        with self.symbol_management_lock:
            if symbol not in self.active_symbols:
                return False
            
            self.log(f"⛔ Dừng coin {symbol}...")
            
            if self.current_processing_symbol == symbol:
                timeout = time.time() + 10
                while self.current_processing_symbol == symbol and time.time() < timeout:
                    time.sleep(0.5)
            
            if self.symbol_data[symbol]["position_open"]:
                self._close_symbol_position(symbol, "Dừng coin theo lệnh")
            
            self.ws_manager.remove_symbol(symbol)
            self.coin_manager.unregister_coin(symbol)
            
            if symbol in self.symbol_data:
                del self.symbol_data[symbol]
            if symbol in self.active_symbols:
                self.active_symbols.remove(symbol)
            
            self.log(f"✅ Đã dừng {symbol} | Còn lại {len(self.active_symbols)}/{self.max_coins}")
            
            if len(self.active_symbols) < self.max_coins:
                self.log(f"🔄 Tự tìm coin mới thay {symbol}...")
                threading.Thread(target=self._delayed_find_new_coin, daemon=True).start()
            return True

    def _delayed_find_new_coin(self):
        time.sleep(2)
        self._find_and_add_new_coin()

    def stop_all_symbols(self):
        self.log("⛔ Dừng tất cả coin...")
        to_stop = self.active_symbols.copy()
        stopped = 0
        for sym in to_stop:
            if self.stop_symbol(sym):
                stopped += 1
                time.sleep(1)
        self.log(f"✅ Đã dừng {stopped} coin, bot vẫn chạy (có thể thêm coin mới)")
        return stopped

    def stop(self):
        self._stop = True
        stopped = self.stop_all_symbols()
        self.log(f"🔴 Bot dừng - đã dừng {stopped} coin")

    # ========== PHÂN TÍCH TOÀN TÀI KHOẢN ==========
    def check_global_positions(self):
        try:
            positions = get_position_summary(self.api_key, self.api_secret)
            if not positions:
                self.global_long_count = 0
                self.global_short_count = 0
                self.global_long_pnl = 0
                self.global_short_pnl = 0
                return
            
            long_count = 0
            short_count = 0
            long_pnl = 0
            short_pnl = 0
            
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                upnl = float(pos.get("unRealizedProfit", 0))
                if amt > 0:
                    long_count += 1
                    long_pnl += upnl
                elif amt < 0:
                    short_count += 1
                    short_pnl += upnl
            
            self.global_long_count = long_count
            self.global_short_count = short_count
            self.global_long_pnl = long_pnl
            self.global_short_pnl = short_pnl
        except Exception as e:
            if time.time() - self.last_error_log_time > 30:
                self.log(f"❌ Lỗi kiểm tra vị thế toàn tài khoản: {str(e)}")
                self.last_error_log_time = time.time()

    def get_next_side_based_on_comprehensive_analysis(self):
        self.check_global_positions()
        long_pnl = self.global_long_pnl
        short_pnl = self.global_short_pnl
        
        if long_pnl > short_pnl:
            return "BUY"
        elif short_pnl > long_pnl:
            return "SELL"
        else:
            return random.choice(["BUY", "SELL"])

# ========== GLOBAL MARKET BOT ==========
class GlobalMarketBot(BaseBot):
    def __init__(
        self,
        symbol,
        leverage,
        position_percent,
        take_profit,
        stop_loss,
        roi_trigger,
        ws_manager,
        api_key,
        api_secret,
        telegram_bot_token,
        telegram_chat_id,
        bot_id=None,
        **kwargs
    ):
        super().__init__(
            symbol,
            leverage,
            position_percent,
            take_profit,
            stop_loss,
            roi_trigger,
            ws_manager,
            api_key,
            api_secret,
            telegram_bot_token,
            telegram_chat_id,
            "Hệ-thống-RSI-Khối-lượng",
            bot_id=bot_id,
            **kwargs
        )

# ========== GLOBAL INSTANCES ==========
coin_manager = CoinManager()
# ========== BOT MANAGER (FORMAT CŨ + HỖ TRỢ HỆ RSI + KHỐI LƯỢNG) ==========
class BotManager:
    def __init__(self, api_key=None, api_secret=None, telegram_bot_token=None, telegram_chat_id=None):
        self.ws_manager = WebSocketManager()
        self.bots = {}              # {bot_id: bot_instance}
        self.running = True
        self.start_time = time.time()
        self.user_states = {}       # {chat_id: {...}}

        self.api_key = api_key
        self.api_secret = api_secret
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = str(telegram_chat_id) if telegram_chat_id else None

        # tài nguyên dùng chung cho tất cả bot
        self.coin_manager = CoinManager()
        self.symbol_locks = defaultdict(threading.Lock)

        # Thread lắng nghe Telegram (long-polling)
        self.telegram_thread = None
        if self.telegram_bot_token and self.telegram_chat_id:
            self.telegram_thread = threading.Thread(target=self._telegram_listener, daemon=True)
            self.telegram_thread.start()
            # gửi menu chính khi khởi động
            self.send_main_menu(self.telegram_chat_id)
        else:
            self.log("⚡ BotManager khởi động ở chế độ không dùng Telegram")

    # ----- LOG -----
    def log(self, message):
        prefix = "[BotManager]"
        if any(k in message for k in ['❌', '✅', '⛔', '💰', '📈', '📊', '🎯', '🛡️', '🔴', '🟢', '⚠️', '🚫']):
            logger.warning(f"{prefix} {message}")
            # gửi admin nếu có
            if self.telegram_bot_token and self.telegram_chat_id:
                send_telegram(
                    message,
                    chat_id=self.telegram_chat_id,
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
        else:
            logger.info(f"{prefix} {message}")

    # ----- KIỂM TRA KẾT NỐI BINANCE -----
    def _verify_api_connection(self):
        """Kiểm tra kết nối API Binance trước khi tạo bot"""
        try:
            balance = get_balance(self.api_key, self.api_secret)
            if balance is None:
                self.log("❌ LỖI: Không thể kết nối Binance API. Kiểm tra:")
                self.log("   • API Key / Secret")
                self.log("   • IP/VPS/Railway có bị chặn Binance không")
                return False
            self.log(f"✅ Kết nối Binance OK – Số dư USDC: {balance:.2f}")
            return True
        except Exception as e:
            self.log(f"❌ Lỗi khi kiểm tra API: {str(e)}")
            return False

    # ----- TELEGRAM MENU -----
    def send_main_menu(self, chat_id):
        send_telegram(
            "📋 <b>MENU CHÍNH</b>\n"
            "Chọn chức năng bên dưới:",
            chat_id,
            create_main_menu(),
            bot_token=self.telegram_bot_token,
            default_chat_id=self.telegram_chat_id
        )

    # ----- LẮNG NGHE TELEGRAM (LONG-POLLING) -----
    def _telegram_listener(self):
        last_update_id = 0
        self.log("▶ Đang lắng nghe Telegram...")

        while self.running and self.telegram_bot_token:
            try:
                url = (
                    f"https://api.telegram.org/bot{self.telegram_bot_token}"
                    f"/getUpdates?offset={last_update_id+1}&timeout=30"
                )
                response = requests.get(url, timeout=35)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("ok"):
                        for update in data.get("result", []):
                            update_id = update.get("update_id", 0)
                            message = update.get("message") or update.get("edited_message") or {}
                            chat = message.get("chat", {})
                            chat_id = str(chat.get("id"))
                            text = (message.get("text") or "").strip()

                            # chỉ nhận tin từ chat_id đã cấu hình
                            if self.telegram_chat_id and chat_id != self.telegram_chat_id:
                                continue

                            if update_id > last_update_id:
                                last_update_id = update_id

                            if text:
                                self._handle_telegram_message(chat_id, text)
                    else:
                        logger.error(f"Lỗi getUpdates: {data}")
                        time.sleep(5)
                elif response.status_code == 409:
                    logger.error("❌ Lỗi 409: có instance khác đang dùng cùng bot token")
                    time.sleep(10)
                else:
                    logger.error(f"Lỗi HTTP {response.status_code}: {response.text}")
                    time.sleep(5)
            except requests.exceptions.Timeout:
                continue
            except Exception as e:
                logger.error(f"Lỗi Telegram listener: {str(e)}")
                time.sleep(10)

    # ----- XỬ LÝ TIN NHẮN TELEGRAM (FORMAT CŨ) -----
    def _handle_telegram_message(self, chat_id, text):
        user_state = self.user_states.get(chat_id, {})
        current_step = user_state.get("step")

        # Cho phép hủy ở mọi bước
        if text == "❌ Hủy bỏ":
            self.user_states[chat_id] = {}
            send_telegram(
                "❌ Đã hủy thao tác.",
                chat_id,
                create_main_menu(),
                bot_token=self.telegram_bot_token,
                default_chat_id=self.telegram_chat_id
            )
            return

        # Nếu đang trong flow tạo bot -> xử lý theo step
        if current_step:
            self._handle_create_bot_steps(chat_id, text, user_state, current_step)
            self.user_states[chat_id] = user_state
            return

        # Không ở step nào -> xử lý lệnh / menu chính
        if text in ["/start", "🏠 Menu", "menu", "Menu"]:
            self.send_main_menu(chat_id)
        elif text == "📊 Danh sách Bot":
            self._show_bot_list(chat_id)
        elif text == "📊 Thống kê":
            self._show_system_stats(chat_id)
        elif text == "➕ Thêm Bot":
            self._start_bot_creation(chat_id)
        elif text == "⛔ Dừng Bot":
            self._start_stop_all_bots(chat_id)
        elif text == "💰 Số dư":
            self._show_balance(chat_id)
        elif text == "📈 Vị thế":
            self._show_positions(chat_id)
        elif text == "⚙️ Cấu hình":
            self._show_config_info(chat_id)
        elif text == "🎯 Chiến lược":
            self._show_strategy_info(chat_id)
        elif text == "/stop":
            self.stop_all()
            send_telegram(
                "🔴 Đã dừng tất cả bot. Hệ thống vẫn chạy, có thể thêm bot mới.",
                chat_id,
                create_main_menu(),
                bot_token=self.telegram_bot_token,
                default_chat_id=self.telegram_chat_id
            )
        else:
            # tin nhắn không khớp menu -> hiện lại menu
            send_telegram(
                "⚠️ Không hiểu lệnh. Vui lòng dùng menu dưới.",
                chat_id,
                create_main_menu(),
                bot_token=self.telegram_bot_token,
                default_chat_id=self.telegram_chat_id
            )

    # ----- FLOW TẠO BOT (CÁC STEP waiting_*) -----
    def _start_bot_creation(self, chat_id):
        state = {
            "step": "waiting_bot_count",
            "bot_count": None,
            "bot_mode": None,
            "symbols": None,
            "leverage": None,
            "percent": None,
            "tp": None,
            "sl": None,
            "roi_trigger": None,
            "strategy_type": "RSI-Khoi-luong"
        }
        self.user_states[chat_id] = state
        send_telegram(
            "➕ <b>THÊM BOT MỚI</b>\n\n"
            "Chọn <b>số lượng bot</b> (1 bot sẽ quản lý nhiều coin):",
            chat_id,
            create_bot_count_keyboard(),
            bot_token=self.telegram_bot_token,
            default_chat_id=self.telegram_chat_id
        )

    def _handle_create_bot_steps(self, chat_id, text, user_state, current_step):
        # 1) số lượng bot
        if current_step == "waiting_bot_count":
            try:
                bot_count = int(text)
                if bot_count <= 0 or bot_count > 10:
                    send_telegram(
                        "⚠️ Số lượng bot phải từ 1 đến 10. Vui lòng chọn lại:",
                        chat_id,
                        create_bot_count_keyboard(),
                        bot_token=self.telegram_bot_token,
                        default_chat_id=self.telegram_chat_id
                    )
                    return
                user_state["bot_count"] = bot_count
                user_state["step"] = "waiting_bot_mode"
                send_telegram(
                    f"🤖 Số lượng bot: <b>{bot_count}</b>\n\n"
                    f"Chọn <b>chế độ bot</b>:",
                    chat_id,
                    create_bot_mode_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            except ValueError:
                send_telegram(
                    "⚠️ Vui lòng nhập số nguyên hợp lệ cho số lượng bot:",
                    chat_id,
                    create_bot_count_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            return

        # 2) chọn mode: tĩnh / động
        if current_step == "waiting_bot_mode":
            if text == "🤖 Bot Tĩnh - Coin cụ thể":
                user_state["bot_mode"] = "static"
                user_state["step"] = "waiting_symbol"
                send_telegram(
                    "🎯 <b>ĐÃ CHỌN: BOT TĨNH</b>\n\n"
                    "🤖 Bot sẽ giao dịch COIN CỤ THỂ.\n"
                    "Bạn có thể chọn coin trên bàn phím hoặc nhập ví dụ: <code>BTCUSDC</code>\n\n"
                    "Chọn coin:",
                    chat_id,
                    create_symbols_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            elif text == "🔄 Bot Động - Tự tìm coin":
                user_state["bot_mode"] = "dynamic"
                user_state["symbols"] = None
                user_state["step"] = "waiting_leverage"
                balance = get_balance(self.api_key, self.api_secret)
                balance_info = f"\n💰 Số dư hiện có: {balance:.2f} USDC" if balance else ""
                send_telegram(
                    "🔄 <b>ĐÃ CHỌN: BOT ĐỘNG</b>\n\n"
                    "🤖 Bot sẽ TỰ ĐỘNG tìm coin tốt nhất theo hệ RSI + Khối lượng.\n"
                    f"{balance_info}\n\n"
                    "Chọn đòn bẩy (x):",
                    chat_id,
                    create_leverage_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            else:
                send_telegram(
                    "⚠️ Vui lòng chọn chế độ bot bằng nút bên dưới:",
                    chat_id,
                    create_bot_mode_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            return

        # 3) chọn symbol (cho bot tĩnh)
        if current_step == "waiting_symbol":
            # cho phép nhập nhiều symbol cách nhau bởi dấu phẩy
            symbols = [s.strip().upper() for s in text.replace(" ", "").split(",") if s.strip()]
            if not symbols:
                send_telegram(
                    "⚠️ Vui lòng nhập / chọn ít nhất 1 symbol hợp lệ (ví dụ: BTCUSDC hoặc BTCUSDC,ETHUSDC)",
                    chat_id,
                    create_symbols_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
                return
            user_state["symbols"] = symbols
            user_state["step"] = "waiting_leverage"

            balance = get_balance(self.api_key, self.api_secret)
            balance_info = f"\n💰 Số dư hiện có: {balance:.2f} USDC" if balance else ""
            send_telegram(
                "✅ Coin đã chọn: " + ", ".join(symbols) + f"{balance_info}\n\n"
                "Chọn đòn bẩy (x):",
                chat_id,
                create_leverage_keyboard(),
                bot_token=self.telegram_bot_token,
                default_chat_id=self.telegram_chat_id
            )
            return

        # 4) đòn bẩy
        if current_step == "waiting_leverage":
            try:
                txt = text.replace("x", "").replace("X", "").strip()
                lev = int(txt)
                if lev <= 0 or lev > 125:
                    send_telegram(
                        "⚠️ Đòn bẩy phải từ 1x đến 125x. Vui lòng chọn lại:",
                        chat_id,
                        create_leverage_keyboard(),
                        bot_token=self.telegram_bot_token,
                        default_chat_id=self.telegram_chat_id
                    )
                    return
                user_state["leverage"] = lev
                user_state["step"] = "waiting_percent"

                balance = get_balance(self.api_key, self.api_secret)
                balance_info = f"\n💰 Số dư hiện có: {balance:.2f} USDC" if balance else ""

                send_telegram(
                    f"💰 Đòn bẩy: <b>{lev}x</b>{balance_info}\n\n"
                    f"Chọn <b>% số dư</b> cho mỗi lệnh:",
                    chat_id,
                    create_percent_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            except ValueError:
                send_telegram(
                    "⚠️ Vui lòng nhập số hợp lệ cho đòn bẩy:",
                    chat_id,
                    create_leverage_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            return

        # 5) % số dư
        if current_step == "waiting_percent":
            try:
                percent = float(text.replace("%", "").strip())
                if percent <= 0 or percent > 100:
                    send_telegram(
                        "⚠️ % số dư phải từ 0.1 đến 100. Vui lòng chọn lại:",
                        chat_id,
                        create_percent_keyboard(),
                        bot_token=self.telegram_bot_token,
                        default_chat_id=self.telegram_chat_id
                    )
                    return
                user_state["percent"] = percent
                user_state["step"] = "waiting_tp"

                balance = get_balance(self.api_key, self.api_secret)
                actual_amount = balance * (percent / 100) if balance else 0

                send_telegram(
                    f"📊 % Số dư: <b>{percent}%</b>\n"
                    f"💵 Số tiền mỗi lệnh (ước tính): <b>{actual_amount:.2f} USDC</b>\n\n"
                    f"Chọn Take Profit (%):",
                    chat_id,
                    create_tp_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            except ValueError:
                send_telegram(
                    "⚠️ Vui lòng nhập số hợp lệ cho % số dư:",
                    chat_id,
                    create_percent_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            return

        # 6) Take Profit
        if current_step == "waiting_tp":
            try:
                tp = float(text.replace("%", "").strip())
                if tp <= 0:
                    send_telegram(
                        "⚠️ Take Profit phải lớn hơn 0. Vui lòng chọn lại:",
                        chat_id,
                        create_tp_keyboard(),
                        bot_token=self.telegram_bot_token,
                        default_chat_id=self.telegram_chat_id
                    )
                    return
                user_state["tp"] = tp
                user_state["step"] = "waiting_sl"

                send_telegram(
                    f"🎯 Take Profit: <b>{tp}%</b>\n\n"
                    f"Chọn Stop Loss (%):",
                    chat_id,
                    create_sl_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            except ValueError:
                send_telegram(
                    "⚠️ Vui lòng nhập số hợp lệ cho Take Profit:",
                    chat_id,
                    create_tp_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            return

        # 7) Stop Loss
        if current_step == "waiting_sl":
            try:
                sl = float(text.replace("%", "").strip())
                if sl < 0:
                    send_telegram(
                        "⚠️ Stop Loss không được âm. Vui lòng chọn lại:",
                        chat_id,
                        create_sl_keyboard(),
                        bot_token=self.telegram_bot_token,
                        default_chat_id=self.telegram_chat_id
                    )
                    return
                user_state["sl"] = sl
                user_state["step"] = "waiting_roi_trigger"

                send_telegram(
                    f"🛡️ Stop Loss: <b>{sl}%</b>\n\n"
                    f"Chọn ROI Trigger (tự động ưu tiên đóng khi ROI đã đạt mức này)\n"
                    f"Hoặc chọn \"❌ Tắt tính năng\" để bỏ qua:",
                    chat_id,
                    create_roi_trigger_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            except ValueError:
                send_telegram(
                    "⚠️ Vui lòng nhập số hợp lệ cho Stop Loss:",
                    chat_id,
                    create_sl_keyboard(),
                    bot_token=self.telegram_bot_token,
                    default_chat_id=self.telegram_chat_id
                )
            return

        # 8) ROI Trigger
        if current_step == "waiting_roi_trigger":
            if text == "❌ Tắt tính năng":
                user_state["roi_trigger"] = None
            else:
                try:
                    roi_trigger = float(text.replace("%", "").strip())
                    if roi_trigger <= 0:
                        send_telegram(
                            "⚠️ ROI Trigger phải > 0 hoặc chọn \"❌ Tắt tính năng\".",
                            chat_id,
                            create_roi_trigger_keyboard(),
                            bot_token=self.telegram_bot_token,
                            default_chat_id=self.telegram_chat_id
                        )
                        return
                    user_state["roi_trigger"] = roi_trigger
                except ValueError:
                    send_telegram(
                        "⚠️ Vui lòng nhập số hợp lệ cho ROI Trigger:",
                        chat_id,
                        create_roi_trigger_keyboard(),
                        bot_token=self.telegram_bot_token,
                        default_chat_id=self.telegram_chat_id
                    )
                    return

            # ĐÃ ĐỦ THÔNG TIN -> TẠO BOT
            user_state["step"] = None
            self._create_bots_from_state(chat_id, user_state)
            # reset state
            self.user_states[chat_id] = {}

    # ----- TẠO BOT TỪ STATE -----
    def _create_bots_from_state(self, chat_id, state):
        bot_count = state["bot_count"]
        bot_mode = state["bot_mode"]
        symbols = state["symbols"]
        lev = state["leverage"]
        percent = state["percent"]
        tp = state["tp"]
        sl = state["sl"]
        roi_trigger = state["roi_trigger"]
        strategy_type = state.get("strategy_type", "RSI-Khoi-luong")

        if not self._verify_api_connection():
            send_telegram(
                "❌ KHÔNG THỂ KẾT NỐI BINANCE – KHÔNG TẠO ĐƯỢC BOT.",
                chat_id,
                create_main_menu(),
                bot_token=self.telegram_bot_token,
                default_chat_id=self.telegram_chat_id
            )
            return

        # Với kiến trúc hiện tại, mỗi Bot là 1 GlobalMarketBot nối tiếp nhiều coin
        # → bot_count = số coin tối đa mỗi bot quản lý (max_coins)
        # symbol: nếu static thì dùng symbol đầu tiên, dynamic thì None
        symbol_for_bot = None
        if bot_mode == "static" and symbols:
            symbol_for_bot = symbols[0]

        # gọi add_bot chuẩn format cũ
        success = self.add_bot(
            symbol_for_bot,
            lev,
            percent,
            tp,
            sl,
            roi_trigger,
            strategy_type,
            bot_count=bot_count,
            bot_mode=bot_mode,
        )

        if success:
            send_telegram(
                "✅ ĐÃ TẠO BOT THÀNH CÔNG.\n"
                "Dùng mục <b>📊 Danh sách Bot</b> để xem chi tiết.",
                chat_id,
                create_main_menu(),
                bot_token=self.telegram_bot_token,
                default_chat_id=self.telegram_chat_id
            )
        else:
            send_telegram(
                "❌ TẠO BOT THẤT BẠI. Xem log để biết chi tiết.",
                chat_id,
                create_main_menu(),
                bot_token=self.telegram_bot_token,
                default_chat_id=self.telegram_chat_id
            )

    # ----- TẠO BOT (FORMAT CŨ) -----
    def add_bot(self, symbol, lev, percent, tp, sl, roi_trigger, strategy_type, bot_count=1, **kwargs):
        """
        Tạo 1 bot giao dịch nối tiếp (GlobalMarketBot) – format tên hàm cũ.
        symbol: coin khởi tạo (static) hoặc None nếu dynamic
        lev: đòn bẩy
        percent: % số dư
        tp, sl: TP/SL (%)
        roi_trigger: ROI trigger (có thể None)
        strategy_type: chuỗi tên chiến lược (ví dụ: 'RSI-Khoi-luong')
        bot_count: số coin tối đa bot quản lý (max_coins)
        kwargs: bot_mode='static' hoặc 'dynamic'
        """
        if sl == 0:
            sl = None

        if not self.api_key or not self.api_secret:
            self.log("❌ Chưa thiết lập API Key / Secret trong BotManager")
            return False

        if not self._verify_api_connection():
            self.log("❌ KHÔNG THỂ KẾT NỐI BINANCE - KHÔNG TẠO ĐƯỢC BOT")
            return False

        bot_mode = kwargs.get("bot_mode", "static")
        try:
            # Tạo bot_id
            if bot_mode == "static" and symbol:
                bot_id = f"STATIC_{strategy_type}_{int(time.time())}"
            else:
                bot_id = f"DYNAMIC_{strategy_type}_{int(time.time())}"

            if bot_id in self.bots:
                self.log(f"⚠️ Bot {bot_id} đã tồn tại, bỏ qua.")
                return False

            # Tạo instance GlobalMarketBot
            bot = GlobalMarketBot(
                symbol=symbol,
                leverage=lev,
                position_percent=percent,
                take_profit=tp,
                stop_loss=sl,
                roi_trigger=roi_trigger,
                ws_manager=self.ws_manager,
                api_key=self.api_key,
                api_secret=self.api_secret,
                telegram_bot_token=self.telegram_bot_token,
                telegram_chat_id=self.telegram_chat_id,
                coin_manager=self.coin_manager,
                symbol_locks=self.symbol_locks,
                bot_id=bot_id,
                max_coins=bot_count
            )

            # liên kết ngược
            bot._bot_manager = self
            self.bots[bot_id] = bot

            roi_info = f"{roi_trigger}%" if roi_trigger else "Tắt"
            msg = (
                "✅ <b>ĐÃ TẠO BOT MỚI</b>\n"
                f"🆔 Bot ID: <code>{bot_id}</code>\n"
                f"🔧 Chế độ: {bot_mode}\n"
                f"💰 Đòn bẩy: {lev}x\n"
                f"📈 % Số dư: {percent}%\n"
                f"🎯 TP: {tp}%\n"
                f"🛡️ SL: {sl if sl is not None else 'Tắt'}%\n"
                f"🎯 ROI Trigger: {roi_info}\n"
                f"🔢 Số coin tối đa: {bot_count}\n"
            )
            if bot_mode == "static" and symbol:
                msg += f"🔗 Coin khởi tạo: {symbol}\n"
            else:
                msg += "🔗 Coin: Tự động tìm theo hệ RSI + Khối lượng\n"

            msg += "\n🔄 <b>CƠ CHẾ NỐI TIẾP</b> đã kích hoạt – bot xử lý từng coin một."
            self.log(msg)
            return True

        except Exception as e:
            self.log(f"❌ Lỗi tạo bot: {str(e)}")
            return False

    # ----- QUẢN LÝ DỪNG BOT / COIN -----
    def stop_bot_symbol(self, bot_id, symbol):
        """Dừng 1 coin cụ thể trong 1 bot"""
        bot = self.bots.get(bot_id)
        if bot and hasattr(bot, "stop_symbol"):
            ok = bot.stop_symbol(symbol)
            if ok:
                self.log(f"⛔ Đã dừng coin {symbol} trong bot {bot_id}")
            return ok
        return False

    def stop_all_bot_symbols(self, bot_id):
        """Dừng tất cả coin trong 1 bot (bot vẫn sống – có thể thêm coin mới)"""
        bot = self.bots.get(bot_id)
        if bot and hasattr(bot, "stop_all_symbols"):
            count = bot.stop_all_symbols()
            self.log(f"⛔ Đã dừng {count} coin trong bot {bot_id}")
            return count
        return 0

    def stop_bot(self, bot_id):
        """Dừng toàn bộ bot (đóng tất cả vị thế & xóa bot)"""
        bot = self.bots.get(bot_id)
        if bot:
            bot.stop()
            del self.bots[bot_id]
            self.log(f"🔴 Đã dừng bot {bot_id}")
            return True
        return False

    def stop_all(self):
        """Dừng tất cả bot (đóng tất cả vị thế và xóa khỏi danh sách)"""
        self.log("🔴 Đang dừng tất cả bot...")
        for bot_id in list(self.bots.keys()):
            self.stop_bot(bot_id)
        self.log("🔴 Đã dừng tất cả bot – hệ thống vẫn chạy, có thể thêm bot mới")

    def _start_stop_all_bots(self, chat_id):
        """Xử lý nút '⛔ Dừng Bot' trong menu – tạm dừng toàn bộ"""
        self.stop_all()
        send_telegram(
            "🔴 Đã dừng toàn bộ bot.\n"
            "Bạn có thể tạo bot mới bằng nút ➕ Thêm Bot.",
            chat_id,
            create_main_menu(),
            bot_token=self.telegram_bot_token,
            default_chat_id=self.telegram_chat_id
        )

    # ----- HIỂN THỊ THÔNG TIN TRÊN TELEGRAM -----
    def _show_bot_list(self, chat_id):
        if not self.bots:
            send_telegram(
                "📊 Hiện tại <b>chưa có bot nào</b> đang chạy.",
                chat_id,
                create_main_menu(),
                bot_token=self.telegram_bot_token,
                default_chat_id=self.telegram_chat_id
            )
            return

        lines = ["📊 <b>DANH SÁCH BOT</b>\n"]
        for bot_id, bot in self.bots.items():
            symbols = getattr(bot, "active_symbols", [])
            status = getattr(bot, "status", "unknown")
            lev = getattr(bot, "leverage", "?")
            pct = getattr(bot, "position_percent", "?")
            lines.append(
                f"🆔 <code>{bot_id}</code>\n"
                f"   • Trạng thái: {status}\n"
                f"   • Đòn bẩy: {lev}x | % Số dư: {pct}%\n"
                f"   • Coin đang quản lý: {', '.join(symbols) if symbols else 'Chưa có (đang tìm)'}\n"
            )
        send_telegram(
            "\n".join(lines),
            chat_id,
            create_main_menu(),
            bot_token=self.telegram_bot_token,
            default_chat_id=self.telegram_chat_id
        )

    def _show_balance(self, chat_id):
        balance = get_balance(self.api_key, self.api_secret)
        if balance is None:
            msg = "❌ Không lấy được số dư. Kiểm tra kết nối Binance / API Key."
        else:
            msg = f"💰 <b>SỐ DƯ USDC</b>\n\n"
            msg += f"📦 Số dư khả dụng: <b>{balance:.4f} USDC</b>"
        send_telegram(
            msg,
            chat_id,
            create_main_menu(),
            bot_token=self.telegram_bot_token,
            default_chat_id=self.telegram_chat_id
        )

    def _show_positions(self, chat_id):
        positions = get_position_summary(self.api_key, self.api_secret)
        if not positions:
            msg = "📈 Hiện tại <b>không có vị thế nào</b> đang mở."
        else:
            lines = ["📈 <b>DANH SÁCH VỊ THẾ</b>\n"]
            for pos in positions:
                symbol = pos.get("symbol")
                amt = float(pos.get("positionAmt", 0))
                entry = float(pos.get("entryPrice", 0))
                upnl = float(pos.get("unRealizedProfit", 0))
                lev = int(float(pos.get("leverage", 0)))
                side = "LONG" if amt > 0 else "SHORT"
                lines.append(
                    f"🔗 {symbol} | {side}\n"
                    f"   • Kích thước: {abs(amt):.4f}\n"
                    f"   • Entry: {entry:.4f}\n"
                    f"   • Leverage: {lev}x\n"
                    f"   • PnL chưa thực: {upnl:.4f} USDC\n"
                )
            msg = "\n".join(lines)
        send_telegram(
            msg,
            chat_id,
            create_main_menu(),
            bot_token=self.telegram_bot_token,
            default_chat_id=self.telegram_chat_id
        )

    def _show_system_stats(self, chat_id):
        uptime = time.time() - self.start_time
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        seconds = int(uptime % 60)
        msg = (
            "📊 <b>THỐNG KÊ HỆ THỐNG</b>\n\n"
            f"🕒 Uptime: {hours}h {minutes}m {seconds}s\n"
            f"🤖 Số bot đang chạy: {len(self.bots)}\n"
        )
        send_telegram(
            msg,
            chat_id,
            create_main_menu(),
            bot_token=self.telegram_bot_token,
            default_chat_id=self.telegram_chat_id
        )

    def _show_config_info(self, chat_id):
        msg = (
            "⚙️ <b>CẤU HÌNH HIỆN TẠI</b>\n\n"
            f"• Có API Key: {'✅' if self.api_key else '❌'}\n"
            f"• Có Secret: {'✅' if self.api_secret else '❌'}\n"
            f"• Telegram Bot Token: {'✅' if self.telegram_bot_token else '❌'}\n"
            f"• Chat ID: {self.telegram_chat_id or '❌ Chưa thiết lập'}\n"
        )
        send_telegram(
            msg,
            chat_id,
            create_main_menu(),
            bot_token=self.telegram_bot_token,
            default_chat_id=self.telegram_chat_id
        )

    def _show_strategy_info(self, chat_id):
        msg = (
            "🎯 <b>CHIẾN LƯỢC: HỆ THỐNG RSI + KHỐI LƯỢNG</b>\n\n"
            "• Tín hiệu vào/ra dựa trên RSI + xu hướng giá + thay đổi khối lượng.\n"
            "• Nhồi lệnh Fibonacci khi lỗ sâu.\n"
            "• Có TP/SL và ROI Trigger (tự ưu tiên đóng khi đã đạt ROI cao).\n"
            "• Bot chạy theo cơ chế nối tiếp: xử lý từng coin một.\n"
        )
        send_telegram(
            msg,
            chat_id,
            create_main_menu(),
            bot_token=self.telegram_bot_token,
            default_chat_id=self.telegram_chat_id
        )

# ========== HÀM KHỞI ĐỘNG HỆ THỐNG (GIỮ NGUYÊN TÊN CŨ) ==========
def start_trading_system(api_key, api_secret, telegram_bot_token=None, telegram_chat_id=None):
    """
    Khởi động hệ thống giao dịch hoàn chỉnh.
    Trả về instance BotManager để main.py dùng nếu cần.
    """
    try:
        logger.info("🚀 Đang khởi động Hệ thống RSI + Khối lượng...")
        bot_manager = BotManager(
            api_key=api_key,
            api_secret=api_secret,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id
        )
        logger.info("✅ Hệ thống đã khởi động thành công!")
        return bot_manager
    except Exception as e:
        logger.error(f"❌ Lỗi khởi động hệ thống: {str(e)}")
        return None
