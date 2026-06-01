#!/usr/bin/env python3
"""
Backfill missed signal messages from Telegram channels.

Fetches historical messages since the last processed message in the journal,
parses them through DeepSeek, and records trades.
"""
import asyncio
import sys
import os
import json
import sqlite3
from datetime import datetime, timezone

os.chdir("/app")
sys.path.insert(0, "/app")

from dotenv import load_dotenv
load_dotenv("/app/.env")

from telethon import TelegramClient
from src.signals.signal_parser import SignalParser, SignalAction
from src.signals.signal_validator import SignalValidator
from src.signals.signal_risk import SignalRiskGuard
from src.signals.signal_journal import SignalJournal


def get_last_message_time() -> datetime:
    """Get the timestamp of the last processed message from the journal."""
    try:
        c = sqlite3.connect("data/signal_journal.db")
        row = c.execute(
            "SELECT timestamp FROM raw_messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        c.close()
        if row:
            return datetime.fromisoformat(row[0])
    except Exception:
        pass
    # Default: 3 days ago
    from datetime import timedelta
    return datetime.now(timezone.utc) - timedelta(days=3)


def message_already_processed(journal_db: sqlite3, message_id: int, channel_id: int) -> bool:
    """Check if a message was already processed."""
    row = journal_db.execute(
        "SELECT COUNT(*) FROM raw_messages WHERE message_id = ? AND channel_id = ?",
        (message_id, channel_id),
    ).fetchone()
    return row[0] > 0


async def backfill():
    print("=" * 60)
    print("📡 SIGNAL BACKFILL — Fetching missed messages")
    print("=" * 60)

    # Config
    api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")
    channel_ids_str = os.environ.get("SIGNAL_CHANNEL_IDS", "")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")

    if not api_id or not api_hash or not channel_ids_str:
        print("❌ Missing TELEGRAM_API_ID, TELEGRAM_API_HASH, or SIGNAL_CHANNEL_IDS")
        return

    channel_ids = [int(c.strip()) for c in channel_ids_str.split(",") if c.strip()]
    print(f"Channels: {channel_ids}")
    print(f"DeepSeek key: {deepseek_key[:8]}..." if deepseek_key else "❌ No DeepSeek key")

    last_time = get_last_message_time()
    print(f"Last processed message: {last_time}")
    print()

    # Connect to Telegram — copy session to avoid locking conflict with live listener
    import shutil
    if os.path.exists("data/signal_listener.session"):
        shutil.copy2("data/signal_listener.session", "/tmp/backfill_listener.session")
    client = TelegramClient("/tmp/backfill_listener", api_id, api_hash)
    await client.start()
    me = await client.get_me()
    print(f"Connected as: {me.first_name} (ID: {me.id})")

    # Init components
    parser = SignalParser(api_key=deepseek_key, model="deepseek-chat")

    config = {
        "enabled": True,
        "audit_mode": False,
        "exchange": "gate_io_paper_trade",
        "ai_model": "deepseek-chat",
        "max_positions": 3,
        "per_trade_risk_pct": 3.0,
        "capital_pct": 100.0,
        "max_capital_usdt": 10000,
        "min_rr_ratio": 1.0,
        "max_sl_distance_pct": 10.0,
        "default_sl_atr_multiplier": 2.0,
        "max_entry_zone_pct": 3.0,
        "min_quality_score": 7,
        "tp1_close_pct": 33,
        "tp2_close_pct": 50,
        "daily_loss_limit_pct": 5.0,
        "max_trades_per_day": 10,
        "cooldown_minutes": 5,
        "use_btc_correlation_gate": False,
        "blacklisted_pairs": [],
        "session_name": "signal_listener",
    }
    validator = SignalValidator(config)
    risk = SignalRiskGuard(config)
    journal = SignalJournal()

    # Stats
    stats = {"fetched": 0, "skipped": 0, "not_signal": 0, "rejected": 0, "traded": 0, "errors": 0}

    for channel_id in channel_ids:
        try:
            entity = await client.get_entity(channel_id)
            channel_name = getattr(entity, "title", str(channel_id))
            print(f"\n📜 Channel: {channel_name} (ID: {channel_id})")
        except Exception as e:
            print(f"❌ Cannot resolve channel {channel_id}: {e}")
            continue

        # Fetch messages since last_time, in chronological order
        messages = await client.get_messages(
            channel_id,
            offset_date=None,  # start from newest
            reverse=False,
            limit=100,
        )

        # Filter by date and reverse to chronological
        filtered = []
        for msg in messages:
            if not msg.text or not msg.text.strip():
                continue
            if msg.date.replace(tzinfo=timezone.utc) <= last_time.replace(tzinfo=None).replace(tzinfo=timezone.utc):
                continue
            if message_already_processed(
                sqlite3.connect("data/signal_journal.db"), msg.id, channel_id
            ):
                stats["skipped"] += 1
                continue
            filtered.append(msg)

        # Reverse to process oldest first
        filtered.reverse()

        print(f"   Found {len(filtered)} new messages since {last_time}")

        for msg in filtered:
            stats["fetched"] += 1
            text = msg.text
            msg_date = msg.date.strftime("%Y-%m-%d %H:%M UTC")
            preview = text[:80].replace("\n", " ")
            print(f"\n   [{msg_date}] {preview}...")

            try:
                # Parse
                signal = parser.parse(text)
                print(f"   → Parsed: {signal.action.value} {signal.pair}")

                # Log raw message to journal
                journal.log_raw_message(
                    channel_id=channel_id,
                    channel_name=channel_name,
                    message_id=msg.id,
                    text=text,
                    parsed_action=signal.action.value,
                    parsed_pair=signal.pair or "",
                    parse_reasoning=signal.parse_reasoning,
                )

                if signal.action == SignalAction.NOT_A_SIGNAL:
                    stats["not_signal"] += 1
                    print(f"   → Not a signal (skip)")
                    continue

                # Validate
                valid, reason = validator.validate(signal)
                if not valid:
                    stats["rejected"] += 1
                    print(f"   → ❌ Rejected: {reason}")
                    journal.log_trade(
                        symbol=signal.pair or "UNKNOWN",
                        channel_name=channel_name,
                        action="rejected",
                        entry_price=signal.entry_high or 0,
                        current_price=0,
                        quantity=0,
                        realized_pnl=0,
                        exit_reason=reason,
                        signal_confidence=signal.confidence.value,
                        stop_loss=signal.stop_loss,
                        take_profits=signal.take_profits,
                        raw_message=text,
                        parse_reasoning=signal.parse_reasoning,
                    )
                    continue

                # Risk check
                if not risk.can_trade():
                    stats["rejected"] += 1
                    print(f"   → ❌ Risk guard blocked")
                    continue

                # Execute (simulate entry like the live engine does)
                entry = signal.entry_high or signal.entry_low or 0
                if entry > 0:
                    stats["traded"] += 1
                    print(f"   → ✅ WOULD TRADE: {signal.pair} @ ${entry:.4f}")
                    print(f"      SL: ${signal.stop_loss} | TPs: {signal.take_profits}")
                    journal.log_trade(
                        symbol=signal.pair,
                        channel_name=channel_name,
                        action="OPEN_LONG",
                        entry_price=entry,
                        current_price=entry,
                        quantity=0,  # Position sizing not applied in backfill
                        realized_pnl=0,
                        exit_reason="backfill_entry",
                        signal_confidence=signal.confidence.value,
                        stop_loss=signal.stop_loss,
                        take_profits=signal.take_profits,
                        raw_message=text,
                        parse_reasoning=signal.parse_reasoning,
                    )

            except Exception as e:
                stats["errors"] += 1
                print(f"   → ❌ Error: {e}")

    await client.disconnect()

    # Summary
    print("\n" + "=" * 60)
    print("📊 BACKFILL SUMMARY")
    print("=" * 60)
    print(f"Messages fetched:  {stats['fetched']}")
    print(f"Already processed: {stats['skipped']}")
    print(f"Not signals:       {stats['not_signal']}")
    print(f"Rejected:          {stats['rejected']}")
    print(f"Traded:            {stats['traded']}")
    print(f"Errors:            {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(backfill())
