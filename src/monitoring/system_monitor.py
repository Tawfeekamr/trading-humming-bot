"""
system_monitor.py
─────────────────
Server resource monitoring: CPU, RAM, Disk.
Provides current stats and threshold alerting via Telegram.
"""

import asyncio
import logging
import time
import threading
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)

ALERT_THRESHOLD = 75.0
ALERT_COOLDOWN_SEC = 1800  # 30 minutes between repeated alerts


@dataclass
class SystemStats:
    cpu_percent: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float

    @property
    def alerts(self) -> list[str]:
        hit = []
        if self.cpu_percent >= ALERT_THRESHOLD:
            hit.append(f"CPU at {self.cpu_percent:.0f}%")
        if self.ram_percent >= ALERT_THRESHOLD:
            hit.append(f"RAM at {self.ram_percent:.0f}%")
        if self.disk_percent >= ALERT_THRESHOLD:
            hit.append(f"Disk at {self.disk_percent:.0f}%")
        return hit

    def format_telegram(self) -> str:
        cpu_bar = _bar(self.cpu_percent)
        ram_bar = _bar(self.ram_percent)
        disk_bar = _bar(self.disk_percent)

        cpu_flag = " ⚠️" if self.cpu_percent >= ALERT_THRESHOLD else ""
        ram_flag = " ⚠️" if self.ram_percent >= ALERT_THRESHOLD else ""
        disk_flag = " ⚠️" if self.disk_percent >= ALERT_THRESHOLD else ""

        return (
            f"🖥️ <b>Server Status</b>\n"
            f"•••\n"
            f"💻 CPU:  {cpu_bar} {self.cpu_percent:.0f}%{cpu_flag}\n"
            f"🧠 RAM:  {ram_bar} {self.ram_percent:.0f}%{ram_flag}\n"
            f"        {self.ram_used_gb:.1f} / {self.ram_total_gb:.1f} GB\n"
            f"💾 Disk: {disk_bar} {self.disk_percent:.0f}%{disk_flag}\n"
            f"        {self.disk_used_gb:.1f} / {self.disk_total_gb:.1f} GB\n"
            f"•••\n"
            f"💰 <b>Cost:</b> ~$15/mo (t3.small)\n"
            f"⚠️ <b>Alert:</b> {ALERT_THRESHOLD:.0f}%"
        )


def _bar(pct: float, width: int = 10) -> str:
    filled = int(pct / 100 * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def get_stats() -> SystemStats:
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return SystemStats(
        cpu_percent=psutil.cpu_percent(interval=1),
        ram_percent=mem.percent,
        ram_used_gb=mem.used / (1024 ** 3),
        ram_total_gb=mem.total / (1024 ** 3),
        disk_percent=disk.percent,
        disk_used_gb=disk.used / (1024 ** 3),
        disk_total_gb=disk.total / (1024 ** 3),
    )


class SystemAlertMonitor:
    """Background thread that checks resource thresholds and fires Telegram alerts."""

    def __init__(self, telegram_bot, interval_sec: int = 300):
        self._bot = telegram_bot
        self._interval = interval_sec
        self._last_alert: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="SystemMonitor")
        self._thread.start()
        logger.info(f"System alert monitor started (interval={self._interval}s, threshold={ALERT_THRESHOLD}%)")

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self._interval):
            try:
                self._check()
            except Exception as e:
                logger.error(f"System monitor check failed: {e}")

    def _check(self):
        stats = get_stats()
        alerts = stats.alerts
        if not alerts:
            return

        now = time.time()
        for alert_key in alerts:
            last = self._last_alert.get(alert_key, 0)
            if now - last < ALERT_COOLDOWN_SEC:
                continue

            self._last_alert[alert_key] = now
            msg = (
                f"⚠️ <b>RESOURCE ALERT</b>\n"
                f"•••\n"
                f"{'  |  '.join(alerts)}\n"
                f"•••\n"
                f"💻 CPU:  {stats.cpu_percent:.0f}%\n"
                f"🧠 RAM:  {stats.ram_percent:.0f}% "
                f"({stats.ram_used_gb:.1f}/{stats.ram_total_gb:.1f} GB)\n"
                f"💾 Disk: {stats.disk_percent:.0f}% "
                f"({stats.disk_used_gb:.1f}/{stats.disk_total_gb:.1f} GB)"
            )
            logger.warning(f"Resource alert: {' | '.join(alerts)}")
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._bot.send(msg))
            except RuntimeError:
                pass
