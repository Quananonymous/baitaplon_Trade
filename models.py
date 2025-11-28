from sqlalchemy import Column, Integer, String
from database import Base


class BotConfig(Base):
    __tablename__ = "bot_config"

    id = Column(Integer, primary_key=True, index=True)
    api_key = Column(String, nullable=False)
    api_secret = Column(String, nullable=False)
    telegram_bot_token = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True)
