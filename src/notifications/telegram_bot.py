import os
import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone
from telegram import Bot
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self._bot: Optional[Bot] = None

    @property
    def bot(self) -> Bot:
        if not self._bot:
            self._bot = Bot(token=self.token)
        return self._bot

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, message: str) -> None:
        if not self.enabled:
            logger.warning("Telegram not configured — skipping alert")
            return
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    async def alert_startup(self, env: str, capital: float) -> None:
        await self.send(
            f"🟢 <b>Grid Bot STARTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 Mode: {env.upper()}\n"
            f"💰 Capital: ${capital:,.0f} USDT\n"
            f"📊 Pair: BTC/USDT\n"
            f"⏰ Time: {self._now()} UTC"
        )

    async def alert_shutdown(self, reason: str = "manual") -> None:
        await self.send(
            f"🔴 <b>Grid Bot STOPPED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Reason: {reason}\n"
            f"⏰ Time: {self._now()} UTC"
        )

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
