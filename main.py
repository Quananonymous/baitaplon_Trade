# backend/main.py
import traceback
import asyncio
import random
import time
import os
import secrets
import requests
import trading_bot_lib
import logging
from typing import Dict, Optional

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Header,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    ForeignKey,
    create_engine,
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("trade_app")

# ==================== DB CONFIG ====================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")

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
    password = Column(String(200))
    api_key = Column(String(200), nullable=True)
    api_secret = Column(String(200), nullable=True)

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
            print("⚠ add_bot FAKE:", kwargs)

        def set_config(self, **kwargs):
            print("⚠ set_config FAKE:", kwargs)

        def start(self):
            print("⚠ start FAKE")

        def stop_all(self):
            print("⚠ stop_all FAKE")

        def stop_bot(self, bot_id):
            print("🔴 stop_bot FAKE:", bot_id)
            return True

        def list_bots(self):
            return []

        def get_status(self):
            return {"running": False, "bots": []}

        def scan_and_set_n_coins(self, *args, **kwargs):
            print("⚠ scan_and_set_n_coins FAKE")

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

@app.middleware("http")
async def log_requests(request, call_next):
    start_time = time.time()
    logger.info(f"➡️ {request.method} {request.url.path}")
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("❌ Unhandled server error")
        raise
    duration_ms = (time.time() - start_time) * 1000
    logger.info(
        f"⬅️ {request.method} {request.url.path} {response.status_code} ({duration_ms:.2f} ms)"
    )
    return response


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


def get_bm(current: User, db):
    bm = BOT_MANAGERS.get(current.id)
    if not bm:
        bm = BotManager(
            api_key=current.api_key,
            api_secret=current.api_secret,
        )
        BOT_MANAGERS[current.id] = bm
    return bm


def get_current_user(
    token: str = Header(None, alias="X-Auth-Token"),
    db: SessionLocal = Depends(get_db),
) -> User:
    uid = TOKEN_STORE.get(token or "")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ==================== AUTH API ====================
@app.post("/api/register")
def register(payload: RegisterReq, db: SessionLocal = Depends(get_db)):
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(400, "Username already exists")

    user = User(username=payload.username, password=payload.password)
    db.add(user)
    db.commit()
    db.refresh(user)

    token = secrets.token_hex(24)
    TOKEN_STORE[token] = user.id

    return {"token": token, "username": user.username}


@app.post("/api/login")
def login(payload: LoginReq, db: SessionLocal = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or user.password != payload.password:
        raise HTTPException(401, "Sai username hoặc password")

    token = secrets.token_hex(24)
    TOKEN_STORE[token] = user.id

    return {"token": token, "username": user.username}


# ==================== ACCOUNT API ====================
@app.post("/api/setup-account")
def setup_account(
    payload: SetupAccountReq,
    current: User = Depends(get_current_user),
    db: SessionLocal = Depends(get_db),
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
            logger.error("Lỗi get_balance: %s", e)

    return {
        "has_api": has_api,
        "balance": balance,
    }


# ==================== BOT CONFIG API ====================
@app.get("/api/bot-config")
def get_bot_config(
    current: User = Depends(get_current_user),
    db: SessionLocal = Depends(get_db),
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
def update_bot_config(
    payload: BotConfigReq,
    current: User = Depends(get_current_user),
    db: SessionLocal = Depends(get_db),
):
    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == current.id)
        .order_by(BotConfig.id.desc())
        .first()
    )
    if not cfg:
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
    else:
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

    bm = get_bm(current, db)
    bm.set_config(
        bot_mode=cfg.bot_mode,
        symbol=cfg.symbol,
        lev=cfg.lev,
        percent=cfg.percent,
        tp=cfg.tp,
        sl=cfg.sl,
        roi_trigger=cfg.roi_trigger,
        bot_count=cfg.bot_count,
    )

    return {"ok": True}


@app.get("/api/bot-configs")
def list_bot_configs(
    current: User = Depends(get_current_user),
    db: SessionLocal = Depends(get_db),
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
    db: SessionLocal = Depends(get_db),
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
    db: SessionLocal = Depends(get_db),
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

    return {"ok": True, "bot_id": cfg.id}


# ==================== BOT START / STOP ====================
@app.post("/api/bot-start")
def bot_start(
    current: User = Depends(get_current_user),
    db: SessionLocal = Depends(get_db),
):
    if not current.api_key or not current.api_secret:
        raise HTTPException(400, "Chưa cấu hình API Binance")

    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == current.id)
        .order_by(BotConfig.id.desc())
        .first()
    )
    if not cfg:
        raise HTTPException(400, "Chưa có cấu hình bot")

    bm = get_bm(current, db)
    bm.set_config(
        bot_mode=cfg.bot_mode,
        symbol=cfg.symbol,
        lev=cfg.lev,
        percent=cfg.percent,
        tp=cfg.tp,
        sl=cfg.sl,
        roi_trigger=cfg.roi_trigger,
        bot_count=cfg.bot_count,
    )
    bm.start()
    return {"ok": True}


@app.post("/api/bot-stop")
def bot_stop(
    current: User = Depends(get_current_user),
    db: SessionLocal = Depends(get_db),
):
    """
    Dừng TẤT CẢ bot của user hiện tại:
    - Đóng toàn bộ bot (stop_all)
    - Xóa luôn BotManager khỏi BOT_MANAGERS để chắc chắn bot_status = False
    """
    bm = BOT_MANAGERS.get(current.id)
    if not bm:
        return {"ok": True}

    try:
        bm.stop_all()
    except Exception as e:
        logger.error(f"❌ Lỗi stop_all cho user {current.id}: {e}")

    BOT_MANAGERS.pop(current.id, None)
    return {"ok": True}


@app.get("/api/bot-status")
def bot_status(
    current: User = Depends(get_current_user),
    db: SessionLocal = Depends(get_db),
):
    bm = BOT_MANAGERS.get(current.id)
    if not bm:
        return {"running": False, "bots": []}

    try:
        status = bm.get_status()
    except Exception as e:
        logger.error("Lỗi get_status: %s", e)
        return {"running": False, "bots": []}
    return status


# ==================== WEBSOCKET PRICE ====================
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
    logger.info(f"📡 WS /ws/price start for symbol={symbol}, interval={interval}")
    try:
        while True:
            try:
                if interval in ("1m", "5m", "15m", "1h", "4h", "1d"):
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
                    price = float(k[4])
                    ts = int(int(k[6]) / 1000) if len(k) > 6 else int(time.time())
                else:
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
                logger.error(f"❌ Binance price error for {symbol}: {e}")
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

            if interval == "1s":
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(5)
    except WebSocketDisconnect:
        logger.info("🔌 Client đóng WebSocket /ws/price")
    except Exception as e:
        logger.exception("❌ WS error /ws/price: %s", e)


# ==================== WEBSOCKET PNL / BALANCE ====================
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

        logger.info(f"📡 WS /ws/pnl start for user={uid}")
        while True:
            try:
                bal = get_balance(user.api_key, user.api_secret)
            except Exception as e:
                logger.error("❌ get_balance error in WS: %s", e)
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
        logger.info("🔌 Client đóng WebSocket /ws/pnl")
    except Exception as e:
        logger.exception("❌ WS error /ws/pnl: %s", e)
    finally:
        db.close()


@app.get("/api/ping")
def api_ping():
    """
    Endpoint ping đơn giản để frontend gọi mỗi 20 giây,
    giúp backend không ngủ và ghi log để debug.
    """
    logger.info("🔁 /api/ping")
    return {"ok": True, "time": int(time.time())}


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
