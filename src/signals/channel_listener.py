"""
channel_listener.py — Telegram channel listener using Telethon (MTProto).

Connects as a USER (not a bot) to read messages from signal channels.
First run requires interactive phone + OTP verification.
After that, the session file persists the auth.
"""

import asyncio
import json
import logging
import queue
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ChannelListener:
    """Listens to Telegram channels for trade signals using Telethon."""

    def __init__(self, api_id: int, api_hash: str,
                 channel_ids: list[int],
                 session_name: str = "signal_listener",
                 on_signal: Optional[Callable] = None):
        self._api_id = api_id
        self._api_hash = api_hash
        self._channel_ids = set(channel_ids)
        self._session_name = session_name
        self._on_signal = on_signal
        self._client = None
        self._running = False
        self._processed_ids: deque = deque(maxlen=500)
        self._message_queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: Optional[threading.Thread] = None
        self._queue_path = Path("data/signal_queue.jsonl")

    def get_message(self) -> Optional[dict]:
        """Non-blocking get from message queue. Called from on_tick()."""
        try:
            return self._message_queue.get_nowait()
        except queue.Empty:
            return None

    def start(self):
        """Start Telethon listener in a background thread."""
        self._load_persisted_messages()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._client:
            asyncio.run_coroutine_threadsafe(self._client.disconnect(), self._client.loop)

    def _persist_message(self, msg: dict):
        """Append message to JSONL file so it survives restarts."""
        try:
            self._queue_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._queue_path, "a") as f:
                f.write(json.dumps(msg, default=str) + "\n")
        except Exception as e:
            logger.error(f"Failed to persist signal message: {e}")

    def _load_persisted_messages(self):
        """Load unconsumed messages from disk into memory queue on startup."""
        if not self._queue_path.exists():
            return
        try:
            content = self._queue_path.read_text().strip()
            if not content:
                return

            count = 0
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Add to dedup so Telethon won't re-add these on reconnect
                msg_id = msg.get("message_id")
                if msg_id is not None:
                    self._processed_ids.append(msg_id)

                try:
                    self._message_queue.put_nowait(msg)
                    count += 1
                except queue.Full:
                    logger.warning("Queue full during startup load, some persisted messages dropped")
                    break

            # Clear file — fresh start; new arrivals will re-persist
            self._queue_path.write_text("")

            if count > 0:
                logger.info(f"Recovered {count} persisted signal(s) from disk")
        except Exception as e:
            logger.error(f"Failed to load persisted signals: {e}")

    def _run(self):
        """Run Telethon event loop in background thread."""
        asyncio.run(self._start())

    async def _start(self):
        try:
            from telethon import TelegramClient, events
        except ImportError:
            logger.error("Telethon not installed. Run: pip install telethon")
            return

        session_path = f"data/{self._session_name}"
        self._client = TelegramClient(session_path, self._api_id, self._api_hash)
        await self._client.start()

        me = await self._client.get_me()
        logger.info(f"Signal listener connected as: {me.first_name} (ID: {me.id})")

        @self._client.on(events.NewMessage(chats=list(self._channel_ids)))
        async def handler(event):
            if event.message.id in self._processed_ids:
                return
            self._processed_ids.append(event.message.id)

            text = event.message.text or ""
            if not text.strip():
                return

            channel_name = getattr(event.chat, 'title', str(event.chat_id))
            logger.info(f"Signal [{channel_name}]: {text[:120]}...")

            msg = {
                "channel_id": event.chat_id,
                "channel_name": channel_name,
                "text": text,
                "message_id": event.message.id,
                "timestamp": event.message.date.timestamp(),
            }

            self._persist_message(msg)

            if self._on_signal:
                try:
                    self._on_signal(msg)
                except Exception as e:
                    logger.error(f"Signal callback error: {e}")

            try:
                self._message_queue.put_nowait(msg)
            except queue.Full:
                logger.warning("Signal message queue full, dropping oldest")
                try:
                    self._message_queue.get_nowait()
                    self._message_queue.put_nowait(msg)
                except queue.Empty:
                    pass

        @self._client.on(events.MessageEdited(chats=list(self._channel_ids)))
        async def edit_handler(event):
            text = event.message.text or ""
            if not text.strip():
                return

            channel_name = getattr(event.chat, 'title', str(event.chat_id))
            logger.info(f"Signal EDIT [{channel_name}]: {text[:120]}...")

            msg = {
                "channel_id": event.chat_id,
                "channel_name": channel_name,
                "text": f"[EDIT] {text}",
                "message_id": event.message.id,
                "timestamp": event.message.date.timestamp(),
            }

            self._persist_message(msg)

            if self._on_signal:
                try:
                    self._on_signal(msg)
                except Exception as e:
                    logger.error(f"Signal edit callback error: {e}")

            try:
                self._message_queue.put_nowait(msg)
            except queue.Full:
                pass

        self._running = True
        logger.info(f"Signal listener started: {len(self._channel_ids)} channel(s)")
        await self._client.run_until_disconnected()
