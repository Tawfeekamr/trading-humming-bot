import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.notifications.telegram_bot import TelegramBot


@pytest.fixture
def mock_bot():
    """Create a mock bot instance."""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock())
    return bot


@pytest.fixture
def telegram_bot(mock_bot):
    """Create a TelegramBot instance with mock credentials and mocked bot."""
    with patch.dict('os.environ', {
        'TELEGRAM_BOT_TOKEN': 'test_token_123',
        'TELEGRAM_CHAT_ID': 'test_chat_id'
    }):
        bot = TelegramBot()
        # Inject mock bot
        bot._bot = mock_bot
        yield bot


@pytest.fixture
def disabled_telegram_bot():
    """Create a TelegramBot instance without credentials."""
    with patch.dict('os.environ', {}, clear=True):
        bot = TelegramBot()
        bot._bot = None
        yield bot


class TestTelegramBotRetry:
    """Test retry behavior with exponential backoff."""

    @pytest.mark.asyncio
    async def test_send_succeeds_on_first_attempt(self, telegram_bot, mock_bot):
        """Successful send should not retry."""
        await telegram_bot.send("Test message")

        mock_bot.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_retries_on_transient_failure(self, telegram_bot, mock_bot):
        """Should retry on failure with exponential backoff."""
        call_count = 0
        async def failing_then_succeeding(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Network error")
            return MagicMock()

        mock_bot.send_message = AsyncMock(side_effect=failing_then_succeeding)

        with patch('asyncio.sleep') as mock_sleep:
            await telegram_bot.send("Test message")

        # Should have been called twice (initial + 1 retry)
        assert mock_bot.send_message.call_count == 2
        # First retry uses 2^0 = 1 second delay
        mock_sleep.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_send_retries_up_to_max_attempts(self, telegram_bot, mock_bot):
        """Should retry up to 3 times before giving up."""
        mock_bot.send_message = AsyncMock(
            side_effect=Exception("Persistent failure")
        )

        with patch('asyncio.sleep') as mock_sleep:
            await telegram_bot.send("Test message")

        # Should attempt 3 times (initial + 2 retries)
        assert mock_bot.send_message.call_count == 3
        # Should sleep with exponential backoff: 1s, 2s
        assert mock_sleep.call_count == 2
        sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_calls == [1, 2]

    @pytest.mark.asyncio
    async def test_send_logs_retry_warnings(self, telegram_bot, mock_bot, caplog):
        """Should log warnings for each retry attempt."""
        mock_bot.send_message = AsyncMock(
            side_effect=Exception("Network timeout")
        )

        with patch('asyncio.sleep'):
            await telegram_bot.send("Test message")

        # Check that retry warnings were logged
        assert any("retry 1/3" in record.message.lower() for record in caplog.records)
        assert any("retry 2/3" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_send_logs_error_after_max_retries(self, telegram_bot, mock_bot, caplog):
        """Should log final error after exhausting retries."""
        mock_bot.send_message = AsyncMock(
            side_effect=Exception("Permanent failure")
        )

        with patch('asyncio.sleep'):
            await telegram_bot.send("Test message")

        # Check final error log
        assert any("failed after 3 attempts" in record.message.lower()
                  for record in caplog.records)

    @pytest.mark.asyncio
    async def test_disabled_bot_skips_send(self, disabled_telegram_bot, caplog):
        """Disabled bot should log warning and not attempt send."""
        await disabled_telegram_bot.send("Test message")

        assert any("not configured" in record.message.lower()
                  for record in caplog.records)


class TestTelegramBotAlertMethods:
    """Test that alert methods use retry logic."""

    @pytest.mark.asyncio
    async def test_alert_startup_retries_on_failure(self, telegram_bot, mock_bot):
        """alert_startup should retry on send failure."""
        call_count = 0
        async def failing_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Network error")
            return MagicMock()

        mock_bot.send_message = AsyncMock(side_effect=failing_once)

        with patch('asyncio.sleep'):
            await telegram_bot.alert_startup("test", 1000.0)

        # Should have retried
        assert mock_bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_alert_shutdown_retries_on_failure(self, telegram_bot, mock_bot):
        """alert_shutdown should retry on send failure."""
        mock_bot.send_message = AsyncMock(
            side_effect=[Exception("Error"), MagicMock()]
        )

        with patch('asyncio.sleep'):
            await telegram_bot.alert_shutdown("test")

        assert mock_bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_alert_error_retries_on_failure(self, telegram_bot, mock_bot):
        """alert_error should retry on send failure."""
        mock_bot.send_message = AsyncMock(
            side_effect=[Exception("Error"), MagicMock()]
        )

        with patch('asyncio.sleep'):
            await telegram_bot.alert_error("source", "error")

        assert mock_bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_alert_crash_retries_on_failure(self, telegram_bot, mock_bot):
        """alert_crash should retry on send failure."""
        mock_bot.send_message = AsyncMock(
            side_effect=[Exception("Error"), MagicMock()]
        )

        with patch('asyncio.sleep'):
            await telegram_bot.alert_crash("source", "error", "traceback")

        assert mock_bot.send_message.call_count == 2
