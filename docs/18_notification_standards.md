# Telegram Notification Standards

This document outlines the standard procedure and formatting rules for adding new Telegram notifications to the Hummingbot dual-engine strategy.

## 1. The Watch-Friendly Format

All notifications must be optimized for readability on small screens (Apple Watch, mobile lock screens). Screen space is at a premium.

**Formatting Rules:**
1. **Contextual Emojis:** Always start the title with an emoji to instantly convey the type of message (e.g., `🚀` for entry, `💚`/`🔴` for profit/loss, `⚠️` for alerts, `⚙️` for environment).
2. **Short Labels:** Use abbreviated, bold labels. For example, use `<b>Size:</b>` instead of `Amount:`, and `<b>In:</b>` instead of `Entry Price:`.
3. **The Separator:** Use `•••` instead of long horizontal lines. Never use `━━━━━━━━━━━━━━━━━━━━━━` as it wraps terribly on small screens.
4. **HTML Parsing:** Telegram expects HTML. Use `<b>` for bold and `<i>` for italics. Avoid markdown `**` or `__`.

**Example Template:**
```python
msg = (
    f"🚀 <b>ACTION: {self.display_pair}</b>\n"
    f"•••\n"
    f"💵 <b>Price:</b> ${price:,.2f}\n"
    f"📦 <b>Size:</b> {quantity} {self.base_asset}\n"
    f"⚙️ <b>Env:</b> {self.env.upper()}"
)
```

## 2. Sending the Notification (Async Dispatch)

Hummingbot runs its core strategy loop (`on_tick`, `did_fill_order`) synchronously. However, the `TelegramBot.send()` method is **asynchronous** (to prevent network latency from freezing the bot). 

To send a message, you must safely dispatch it to the running event loop without blocking the bot.

**Standard Dispatch Pattern:**
Whenever you want to send `msg`, wrap it in this exact try/catch block:
```python
try:
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # Fire and forget
        loop.create_task(self.telegram.send(msg))
except RuntimeError:
    # Failsafe if the event loop is closed or inaccessible
    pass
```

## 3. Best Practices by Location

* **Trade Executions:** Add these inside `did_fill_order(self, event)` in `ta_grid_trend.py`. Ensure you capture the actual filled `price` and `amount` from the `event` object, not just your target price.
* **State/Regime Changes:** Add these inside `_grid_tick()` or `_trend_tick()` right after the logic dictates a change (e.g., `if new_state != prev_state:`).
* **System Alerts / Errors:** Do not use `create_task` directly for hard crashes. Use the built-in `self._safe_telegram_crash(context, error)` which handles the formatting and traceback automatically.
