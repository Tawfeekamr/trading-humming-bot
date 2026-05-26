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

        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    # Exponential backoff: 1s, 2s, 4s
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    logger.warning(
                        f"Telegram send retry {attempt + 1}/{max_retries}: {e}"
                    )
                else:
                    logger.error(
                        f"Telegram send failed after {max_retries} attempts: {e}"
                    )

    async def alert_startup(self, env: str, capital: float, pairs: str = "",
                           engines: str = "", grid_levels: int = 0,
                           signal_channels: int = 0, audit_mode: bool = False) -> None:
        lines = [
            f"🟢 <b>Trading Bot STARTED</b>",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"⚙️ <b>Mode:</b> {env.upper()}",
            f"💰 <b>Capital:</b> ${capital:,.0f} USDT",
        ]
        if pairs:
            lines.append(f"📊 <b>Pairs:</b> {pairs}")
        if grid_levels:
            lines.append(f"📐 <b>Grid:</b> {grid_levels} levels/side")
        if engines:
            lines.append(f"🔧 <b>Engines:</b> {engines}")
        if signal_channels:
            mode_tag = "AUDIT" if audit_mode else "LIVE"
            lines.append(f"📡 <b>Signal Copy:</b> {signal_channels} channels ({mode_tag})")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"⏰ {self._now()} UTC")
        await self.send("\n".join(lines))

    async def alert_shutdown(self, reason: str = "manual") -> None:
        await self.send(
            f"🔴 <b>Grid Bot STOPPED</b>\n"
            f"•••\n"
            f"⚠️ <b>Why:</b> {reason}\n"
            f"⏰ Time: {self._now()} UTC"
        )

    async def alert_error(self, source: str, error: str, details: str = "") -> None:
        msg = (
            f"⚠️ <b>Error in {source}</b>\n"
            f"•••\n"
            f"❌ {error}\n"
            f"⏰ Time: {self._now()} UTC"
        )
        if details:
            msg += f"\n📝 {details[:500]}"
        await self.send(msg)

    async def alert_crash(self, source: str, error: str, traceback_str: str = "") -> None:
        msg = (
            f"🚨 <b>CRASH in {source}</b>\n"
            f"•••\n"
            f"❌ {error}\n"
            f"⏰ Time: {self._now()} UTC"
        )
        if traceback_str:
            tb = traceback_str[:600]
            msg += f"\n\n<pre>{tb}</pre>"
        await self.send(msg)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
