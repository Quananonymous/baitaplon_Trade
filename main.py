# backend/main.py
import traceback
import asyncio
import random
import time
import os
import secrets
import requests
import trading_bot_lib
from typing import Dict, Optional

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Header,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    ForeignKey,
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship

# ==================== CẤU HÌNH DATABASE ====================
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ==================== MODEL DATABASE ====================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    password = Column(String(255))
    api_key = Column(String(255), nullable=True)
    api_secret = Column(String(255), nullable=True)

    configs = relationship("BotConfig", back_populates="user")


class BotConfig(Base):
    __tablename__ = "bot_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    bot_mode = Column(String(50), default="static")
    symbol = Column(String(50), nullable=True)
    lev = Column(Integer, default=10)
    percent = Column(Float, default=5.0)
    tp = Column(Float, default=10.0)
    sl = Column(Float, default=20.0)
    roi_trigger = Column(Float, nullable=True)
    bot_count = Column(Integer, default=1)

    user = relationship("User", back_populates="configs")


Base.metadata.create_all(bind=engine)


# ==================== IMPORT BOT MANAGER THẬT ====================
try:
    from trading_bot_lib import BotManager, get_balance
except Exception as e:
    print("⚠ Lỗi import trading_bot_lib:", e)

    class BotManager:
        def __init__(self, *args, **kwargs):
            print("⚠ BOT MANAGER FAKE — UI vẫn chạy OK, KHÔNG giao dịch thật")

        def add_bot(self, **kwargs):
            print("📌 add_bot FAKE:", kwargs)
            return True

        def stop_all(self):
            print("🔴 stop_all FAKE")

        def stop_all_coins(self):
            print("🔴 stop_all_coins FAKE")

        def stop_bot(self, bot_id):
            print("🔴 stop_bot FAKE:", bot_id)
            return True

    def get_balance(api_key, api_secret):
        return 0.0


# ==================== FASTAPI APP ====================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # có thể giới hạn lại nếu muốn
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== DEPENDENCY DB ====================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==================== AUTH GIẢN ĐƠN ====================
TOKEN_STORE: Dict[str, int] = {}  # token -> user_id


class RegisterReq(BaseModel):
    username: str
    password: str


class LoginReq(BaseModel):
    username: str
    password: str


class SetupAccountReq(BaseModel):
    api_key: str
    api_secret: str


class BotConfigReq(BaseModel):
    bot_mode: str = Field(default="static")  # static / dynamic
    symbol: Optional[str] = None
    lev: int = 10
    percent: float = 5.0
    tp: float = 10.0
    sl: float = 20.0
    roi_trigger: Optional[float] = None
    bot_count: int = 1


# (giữ để tương thích nếu sau này dùng API khác)
class AddBotReq(BaseModel):
    bot_mode: str = Field(default="static")  # static / dynamic
    symbol: Optional[str] = None
    lev: int = 10
    percent: float = 5
    tp: float = 50
    sl: float = 0
    roi_trigger: float = 0
    bot_count: int = 1


class StopBotReq(BaseModel):
    bot_id: str


# ==================== BOT MANAGER STORE ====================
BOT_MANAGERS: Dict[int, BotManager] = {}


def restore_bots(user: User, bm: BotManager, db: Session):
    """Khôi phục bot từ DB vào RAM (nếu cần). Hiện tại mình chỉ dùng cấu hình + start thủ công."""
    configs = db.query(BotConfig).filter(BotConfig.user_id == user.id).all()
    for cfg in configs:
        # tuỳ bạn có muốn auto-start lại hay không; tạm thời không auto-start để an toàn
        pass


def get_bm(user: User, db: Session) -> BotManager:
    bm = BOT_MANAGERS.get(user.id)
    if bm is None:
        api_key = user.api_key
        api_secret = user.api_secret
        if not api_key or not api_secret:
            # Chưa lưu API → báo lỗi rõ ràng
            raise HTTPException(
                status_code=400,
                detail="Chưa thiết lập API Key/Secret cho tài khoản này. Vào mục 'API Binance' để lưu."
            )

        try:
            # Khởi tạo BotManager thật (từ trading_bot_lib)
            bm = BotManager(api_key=api_key, api_secret=api_secret)
        except Exception as e:
            # Bắt mọi lỗi trong __init__ (sai key, lỗi lib, v.v.)
            print("❌ Lỗi khởi tạo BotManager:", e)
            traceback.print_exc()
            raise HTTPException(
                status_code=400,
                detail=f"Lỗi khởi tạo BotManager: {e}"
            )

        BOT_MANAGERS[user.id] = bm
        restore_bots(user, bm, db)
    return bm



# ==================== AUTH API ====================
@app.post("/api/register")
def register(payload: RegisterReq, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(400, "Username đã tồn tại")

    user = User(username=payload.username, password=payload.password)
    db.add(user)
    db.commit()
    db.refresh(user)

    token = secrets.token_hex(16)
    TOKEN_STORE[token] = user.id

    return {"token": token, "username": user.username}


@app.post("/api/login")
def login(payload: LoginReq, db: Session = Depends(get_db)):
    user = (
        db.query(User)
        .filter(User.username == payload.username, User.password == payload.password)
        .first()
    )
    if not user:
        raise HTTPException(401, "Sai username hoặc password")

    token = secrets.token_hex(16)
    TOKEN_STORE[token] = user.id
    return {"token": token, "username": user.username}


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    x_auth_token: Optional[str] = Header(default=None),
) -> User:
    token = x_auth_token or request.headers.get("X-Auth-Token")
    if not token:
        raise HTTPException(401, "Thiếu token")

    uid = TOKEN_STORE.get(token)
    if not uid:
        raise HTTPException(401, "Token không hợp lệ hoặc đã hết hạn")

    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(401, "User không tồn tại")

    return user


@app.get("/api/me")
def me(current: User = Depends(get_current_user)):
    return {"id": current.id, "username": current.username}


# ==================== SETUP API KEY ====================
@app.get("/api/setup-account")
def get_setup_account(current: User = Depends(get_current_user)):
    return {
        "has_api": bool(current.api_key and current.api_secret),
    }


@app.post("/api/setup-account")
def setup_account(
    payload: SetupAccountReq,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current.api_key = payload.api_key.strip()
    current.api_secret = payload.api_secret.strip()
    db.commit()
    db.refresh(current)
    return {"ok": True}


@app.get("/api/account-status")
def account_status(current: User = Depends(get_current_user)):
    has_api = bool(current.api_key and current.api_secret)
    balance = None
    if has_api:
        try:
            balance = get_balance(current.api_key, current.api_secret)
        except Exception as e:
            print("Lỗi get_balance:", e)

    return {
        "has_api": has_api,
        "balance": balance,
    }


# ==================== BOT CONFIG API ====================
@app.get("/api/bot-config")
def get_bot_config(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == current.id)
        .order_by(BotConfig.id.desc())
        .first()
    )
    if not cfg:
        return {
            "bot_mode": "static",
            "symbol": "BTCUSDT",
            "lev": 10,
            "percent": 5.0,
            "tp": 10.0,
            "sl": 20.0,
            "roi_trigger": None,
            "bot_count": 1,
        }

    return {
        "bot_mode": cfg.bot_mode,
        "symbol": cfg.symbol,
        "lev": cfg.lev,
        "percent": cfg.percent,
        "tp": cfg.tp,
        "sl": cfg.sl,
        "roi_trigger": cfg.roi_trigger,
        "bot_count": cfg.bot_count,
    }


@app.post("/api/bot-config")
def save_bot_config(
    payload: BotConfigReq,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == current.id)
        .order_by(BotConfig.id.desc())
        .first()
    )
    if not cfg:
        cfg = BotConfig(user_id=current.id, bot_mode=payload.bot_mode)
        db.add(cfg)

    cfg.bot_mode = payload.bot_mode
    cfg.symbol = payload.symbol
    cfg.lev = payload.lev
    cfg.percent = payload.percent
    cfg.tp = payload.tp
    cfg.sl = payload.sl
    cfg.roi_trigger = payload.roi_trigger
    cfg.bot_count = payload.bot_count

    db.commit()
    db.refresh(cfg)
    return {"ok": True}


# ==================== BOT START / STOP ====================
@app.post("/api/bot-start")
def bot_start(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 1. Lấy cấu hình bot gần nhất
    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == current.id)
        .order_by(BotConfig.id.desc())
        .first()
    )
    if not cfg:
        raise HTTPException(
            status_code=400,
            detail="Chưa có cấu hình bot, hãy vào mục 'Cấu hình bot' và bấm Lưu trước."
        )

    # 2. Lấy BotManager (đã có try/except trong get_bm)
    bm = get_bm(current, db)

    # 3. Gọi add_bot nhưng bắt hết lỗi lại, không để 500
    try:
        ok = bm.add_bot(
            symbol=cfg.symbol,
            lev=cfg.lev,
            percent=cfg.percent,
            tp=cfg.tp,
            sl=cfg.sl,
            roi_trigger=cfg.roi_trigger,
            bot_mode=cfg.bot_mode,
            bot_count=cfg.bot_count,
            strategy_type="RSI-volume-auto",
        )
    except Exception as e:
        print("❌ Lỗi add_bot:", e)
        traceback.print_exc()
        raise HTTPException(
            status_code=400,
            detail=f"Lỗi khởi động bot (add_bot): {e}"
        )

    if not ok:
        # trading_bot_lib.add_bot trả False khi không thể tạo bot
        raise HTTPException(
            status_code=400,
            detail="Không thể khởi tạo bot từ BotManager. Có thể do API Binance không kết nối được hoặc API Key sai."
        )

    return {"ok": True}



@app.post("/api/bot-stop")
def bot_stop(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Dừng TẤT CẢ bot của user hiện tại:
    - Đóng toàn bộ bot (stop_all)
    - Xóa luôn BotManager khỏi BOT_MANAGERS để chắc chắn bot_status = False
    """
    bm = BOT_MANAGERS.get(current.id)
    if not bm:
        # Không có bot nào đang chạy -> coi như đã dừng
        return {"ok": True}

    # Dừng tất cả bot trong manager
    try:
        bm.stop_all()
    except Exception as e:
        print(f"❌ Lỗi stop_all cho user {current.id}: {e}")

    # Xoá hẳn BotManager khỏi bộ nhớ
    BOT_MANAGERS.pop(current.id, None)

    return {"ok": True}


@app.get("/api/bot-status")
def bot_status(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Trả về trạng thái bot:
    - running: True/False
    - mode, symbol: đọc từ cấu hình cuối cùng
    - bot_count: số bot trong BotManager
    - active_symbols: danh sách các symbol bot đang chạy
    - bots: danh sách từng bot (id, strategy, symbol, active_symbols, status, max_coins)
    """
    bm = BOT_MANAGERS.get(current.id)
    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == current.id)
        .order_by(BotConfig.id.desc())
        .first()
    )

    mode = cfg.bot_mode if cfg else "unknown"
    symbol = cfg.symbol if cfg else None

    if not bm or not getattr(bm, "bots", None):
        # Không có bot trong memory
        return {
            "running": False,
            "mode": mode,
            "symbol": symbol,
            "bot_count": 0,
            "active_symbols": [],
            "bots": [],
        }

    # Gom thông tin các bot đang chạy
    bot_count = len(bm.bots)
    active_symbols = []
    bots_info = []
    try:
        for bot_id, bot in bm.bots.items():
            syms = list(getattr(bot, "active_symbols", []) or [])
            if syms:
                active_symbols.extend(syms)
            bots_info.append(
                {
                    "id": bot_id,
                    "strategy": getattr(bot, "strategy_name", None),
                    "symbol": getattr(bot, "symbol", None),
                    "active_symbols": syms,
                    "status": getattr(bot, "status", None),
                    "max_coins": getattr(bot, "max_coins", None),
                }
            )
    except Exception as e:
        print(f"⚠ Lỗi đọc active_symbols: {e}")

    return {
        "running": True,
        "mode": mode,
        "symbol": symbol,
        "bot_count": bot_count,
        "active_symbols": active_symbols,
        "bots": bots_info,
    }


@app.post("/api/bot-stop-one")
def bot_stop_one(
    payload: StopBotReq,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dừng 1 bot theo bot_id (dựa trên BotManager.bots)."""
    bm = BOT_MANAGERS.get(current.id)
    if not bm or not getattr(bm, "bots", None):
        raise HTTPException(400, "Không có bot nào đang chạy")

    ok = False
    try:
        if hasattr(bm, "stop_bot"):
            ok = bm.stop_bot(payload.bot_id)
    except Exception as e:
        print(f"❌ Lỗi stop_bot_one cho user {current.id}: {e}")
        ok = False

    if not ok:
        raise HTTPException(404, f"Không tìm thấy bot id={payload.bot_id}")

    return {"ok": True}


# ==================== (TÙY CHỌN) CÁC API CŨ GIỮ LẠI NẾU MUỐN DÙNG THÊM ====================
@app.get("/api/summary")
def summary(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    configs = db.query(BotConfig).filter(BotConfig.user_id == current.id).all()
    total_bots = len(configs)
    return {
        "total_configs": total_bots,
        "configs": [
            {
                "id": c.id,
                "mode": c.bot_mode,
                "symbol": c.symbol,
                "lev": c.lev,
                "percent": c.percent,
                "tp": c.tp,
                "sl": c.sl,
                "roi_trigger": c.roi_trigger,
                "bot_count": c.bot_count,
            }
            for c in configs
        ],
    }


@app.get("/api/bots")
def get_bots(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    configs = db.query(BotConfig).filter(BotConfig.user_id == current.id).all()
    bots = []
    for cfg in configs:
        bots.append(
            {
                "id": cfg.id,
                "symbol": cfg.symbol,
                "lev": cfg.lev,
                "percent": cfg.percent,
                "tp": cfg.tp,
                "sl": cfg.sl,
                "roi_trigger": cfg.roi_trigger,
                "bot_mode": cfg.bot_mode,
                "bot_count": cfg.bot_count,
            }
        )
    return {"bots": bots}


@app.post("/api/add-bot")
def add_bot_old(
    payload: AddBotReq,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Endpoint cũ, giữ lại nếu bạn muốn quản lý nhiều bot kiểu danh sách riêng."""
    bm = get_bm(current, db)
    bm.add_bot(
        symbol=payload.symbol,
        lev=payload.lev,
        percent=payload.percent,
        tp=payload.tp,
        sl=payload.sl,
        roi_trigger=payload.roi_trigger,
        bot_mode=payload.bot_mode,
        bot_count=payload.bot_count,
        strategy_type="RSI-volume-auto",
    )

    cfg = BotConfig(
        user_id=current.id,
        bot_mode=payload.bot_mode,
        symbol=payload.symbol,
        lev=payload.lev,
        percent=payload.percent,
        tp=payload.tp,
        sl=payload.sl,
        roi_trigger=payload.roi_trigger,
        bot_count=payload.bot_count,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)

    return {"ok": True, "id": cfg.id}


# ==================== WEBSOCKET: GIÁ & PnL ====================
@app.websocket("/ws/price")
async def ws_price(
    ws: WebSocket,
    token: Optional[str] = None,
    symbol: str = "BTCUSDT",
    interval: str = "1s",
):
    """
    WebSocket giá realtime: backend lấy giá Futures từ Binance rồi đẩy ra frontend.

    - Frontend gọi: /ws/price?token=...&symbol=BTCUSDT&interval=1s|1m|1h|1d
    - symbol: coin do người dùng nhập (BTCUSDT, ETHUSDT, XRPUSDT, ...)
    - interval:
        + "1s" (mặc định): dùng ticker price, cập nhật từng giây
        + "1m", "5m", "15m", "1h", "4h", "1d": dùng nến kline tương ứng
    """
    await ws.accept()
    symbol = (symbol or "BTCUSDT").upper()
    interval = (interval or "1s").lower()
    print(f"📡 WS /ws/price start for symbol={symbol}, interval={interval}")
    try:
        while True:
            try:
                if interval in ("1m", "5m", "15m", "1h", "4h", "1d"):
                    # Lấy nến gần nhất cho khung thời gian người dùng chọn
                    resp = requests.get(
                        "https://fapi.binance.com/fapi/v1/klines",
                        params={"symbol": symbol, "interval": interval, "limit": 1},
                        timeout=5,
                    )
                    resp.raise_for_status()
                    klines = resp.json()
                    if not klines:
                        raise RuntimeError("Không lấy được dữ liệu kline")
                    k = klines[0]
                    # k[1]=open, k[2]=high, k[3]=low, k[4]=close, k[0]=openTime(ms), k[6]=closeTime(ms)
                    price = float(k[4])
                    ts = int(int(k[6]) / 1000) if len(k) > 6 else int(time.time())
                else:
                    # Mặc định: dùng ticker price cập nhật nhanh theo giây
                    resp = requests.get(
                        "https://fapi.binance.com/fapi/v1/ticker/price",
                        params={"symbol": symbol},
                        timeout=5,
                    )
                    resp.raise_for_status()
                    js = resp.json()
                    price = float(js.get("price", 0.0))
                    ts = int(time.time())
            except Exception as e:
                print(f"❌ Binance price error for {symbol}: {e}")
                await ws.send_json(
                    {
                        "error": "BINANCE_PRICE_ERROR",
                        "message": str(e),
                        "symbol": symbol,
                        "timestamp": int(time.time()),
                        "interval": interval,
                    }
                )
                await asyncio.sleep(3)
                continue

            data = {
                "symbol": symbol,
                "price": round(price, 4),
                "timestamp": ts,
                "interval": interval,
            }
            await ws.send_json(data)

            # Tốc độ cập nhật: khung lớn thì không cần quá nhanh
            if interval == "1s":
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(5)
    except WebSocketDisconnect:
        print("🔌 Client đóng WebSocket /ws/price")
    except Exception as e:
        print("❌ WS error /ws/price:", e)


@app.websocket("/ws/pnl")
async def ws_pnl(ws: WebSocket, token: str):
    """
    WebSocket gửi số dư thực từ Binance Futures (thông qua trading_bot_lib.get_balance)
    Frontend đang gọi: /ws/pnl?token=authToken
    """
    await ws.accept()
    db: Session = SessionLocal()
    try:
        uid = TOKEN_STORE.get(token)
        if not uid:
            await ws.send_json({"error": "INVALID_TOKEN"})
            await ws.close()
            return

        user = db.query(User).filter(User.id == uid).first()
        if not user or not user.api_key or not user.api_secret:
            await ws.send_json({"error": "NO_API"})
            await ws.close()
            return

        print(f"📡 WS /ws/pnl start for user={uid}")
        while True:
            try:
                bal = get_balance(user.api_key, user.api_secret)
            except Exception as e:
                print("❌ get_balance error in WS:", e)
                await ws.send_json({"error": "BALANCE_ERROR", "message": str(e)})
                await asyncio.sleep(5)
                continue

            await ws.send_json(
                {
                    "balance": bal,
                    "timestamp": int(time.time()),
                }
            )
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        print("🔌 Client đóng WebSocket /ws/pnl")
    except Exception as e:
        print("❌ WS error /ws/pnl:", e)
    finally:
        db.close()


# ==================== SERVE FRONTEND ====================
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")


@app.get("/", response_class=HTMLResponse)
def index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>Backend OK, nhưng thiếu frontend/index.html</h1>")


@app.get("/frontend/{path:path}")
def serve_frontend(path: str):
    file_path = os.path.join(FRONTEND_DIR, path)
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")
    return FileResponse(file_path)


# ==================== CHẠY LOCAL ====================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
