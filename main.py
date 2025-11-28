from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import BotConfig
from trading_bot_lib import create_bot_manager, GLOBAL_EVENT_BUS

app = FastAPI(title="Futures Queue Trading Bot")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static frontend
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

# Khởi tạo DB
init_db()

# BotManager global
bot_manager = create_bot_manager(event_bus=GLOBAL_EVENT_BUS)


# --------- Pydantic Models --------- #

class ConfigIn(BaseModel):
    api_key: str
    api_secret: str
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


class BotIn(BaseModel):
    strategy_name: str = "RSI-Volume-System-Queue"
    lev: int = 50
    percent: float = 5.0
    tp: float | None = 20.0
    sl: float | None = 200.0
    roi_trigger: float | None = 30.0
    max_coins: int = 1


# --------- API ROUTES --------- #

@app.post("/api/config")
def update_config(cfg: ConfigIn, db: Session = Depends(get_db)):
    """
    Cập nhật cấu hình:
    - Xoá config cũ trong DB
    - Lưu config mới
    - Gọi bot_manager.set_config(...) với restart_bots=True
    """
    db.query(BotConfig).delete()
    db.add(
        BotConfig(
            api_key=cfg.api_key,
            api_secret=cfg.api_secret,
            telegram_bot_token=cfg.telegram_bot_token,
            telegram_chat_id=cfg.telegram_chat_id,
        )
    )
    db.commit()

    bot_manager.set_config(
        api_key=cfg.api_key,
        api_secret=cfg.api_secret,
        telegram_bot_token=cfg.telegram_bot_token,
        telegram_chat_id=cfg.telegram_chat_id,
        restart_bots=True,
    )

    return {"ok": True, "msg": "Cập nhật & khởi động lại hệ thống thành công"}


@app.get("/api/state")
def get_state():
    """Snapshot toàn hệ thống cho dashboard"""
    return bot_manager.get_state()


@app.get("/api/events")
def get_events(limit: int = 200):
    """Log + giá realtime cho frontend"""
    return GLOBAL_EVENT_BUS.get_recent(limit)


@app.post("/api/add-bot")
def add_bot(cfg: BotIn):
    """Thêm bot mới"""
    bot_id = bot_manager.add_bot(
        strategy_name=cfg.strategy_name,
        lev=cfg.lev,
        percent=cfg.percent,
        tp=cfg.tp,
        sl=cfg.sl,
        roi_trigger=cfg.roi_trigger,
        max_coins=cfg.max_coins,
    )
    return {"ok": True, "bot_id": bot_id}


@app.post("/api/stop-bot/{bot_id}")
def stop_bot(bot_id: str):
    """Dừng bot theo ID (đóng từng coin trong bot đó)"""
    bot_manager.stop_bot(bot_id)
    return {"ok": True, "msg": f"Bot {bot_id} đã dừng"}


@app.post("/api/stop-all")
def stop_all():
    """Dừng tất cả bot"""
    bot_manager.stop_all()
    return {"ok": True, "msg": "Đã dừng toàn bộ bot"}


@app.get("/api/chart")
def get_chart(symbol: str, limit: int = 60):
    """
    Dữ liệu biểu đồ nến 1m cho symbol:
    - limit: 20–60 (frontend truyền)
    """
    return bot_manager.get_chart_data(symbol, limit)
