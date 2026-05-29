"""Tests for ML prediction data model and poller actor.

These tests are designed to work WITHOUT nautilus_trader installed.
All nautilus internals are mocked.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import asyncio


# Test imports work regardless of nautilus_trader availability
def test_models_importable():
    """Test that models can be imported even without nautilus_trader."""
    from src.nautilus.models import MLPrediction, NAUTILUS_AVAILABLE
    assert MLPrediction is not None
    assert isinstance(NAUTILUS_AVAILABLE, bool)


def test_ml_poller_importable():
    """Test that poller can be imported even without nautilus_trader."""
    from src.nautilus.actors.ml_poller import MLPoller, MLPollerConfig, NAUTILUS_AVAILABLE
    assert MLPoller is not None
    assert MLPollerConfig is not None
    assert isinstance(NAUTILUS_AVAILABLE, bool)


# MLPrediction tests
def test_ml_prediction_creation():
    """Test MLPrediction object creation with all fields."""
    from src.nautilus.models import MLPrediction

    pred = MLPrediction(
        instrument_id="EUR/USD.IDEALPRO",
        regime="TRENDING_UP",
        confidence=0.87,
        ts_event=1714320000000000000,
        ts_init=1714320000000000000,
    )
    assert pred.instrument_id == "EUR/USD.IDEALPRO"
    assert pred.regime == "TRENDING_UP"
    assert pred.confidence == 0.87
    assert pred.ts_event == 1714320000000000000
    assert pred.ts_init == 1714320000000000000


def test_ml_prediction_repr():
    """Test MLPrediction string representation."""
    from src.nautilus.models import MLPrediction

    pred = MLPrediction(
        instrument_id="GBP/USD.IDEALPRO",
        regime="RANGING",
        confidence=0.65,
        ts_event=0,
        ts_init=0,
    )
    r = repr(pred)
    assert "GBP/USD.IDEALPRO" in r
    assert "RANGING" in r
    assert "0.65" in r
    assert "MLPrediction" in r


def test_ml_prediction_confidence_rounding():
    """Test that confidence is displayed with 2 decimal places."""
    from src.nautilus.models import MLPrediction

    pred = MLPrediction(
        instrument_id="BTC/USD",
        regime="TRENDING_UP",
        confidence=0.876543,
        ts_event=0,
        ts_init=0,
    )
    r = repr(pred)
    assert "0.88" in r  # Should round to 2 decimal places


def test_ml_prediction_default_values():
    """Test MLPrediction with minimal data."""
    from src.nautilus.models import MLPrediction

    pred = MLPrediction(
        instrument_id="ETH/USD",
        regime="UNKNOWN",
        confidence=0.0,
        ts_event=0,
        ts_init=0,
    )
    assert pred.instrument_id == "ETH/USD"
    assert pred.regime == "UNKNOWN"
    assert pred.confidence == 0.0


# MLPollerConfig tests
def test_poller_config_defaults():
    """Test MLPollerConfig default values."""
    from src.nautilus.actors.ml_poller import MLPollerConfig

    config = MLPollerConfig()
    assert config.ml_service_url == "http://bot:8080/predict"
    assert config.poll_interval_secs == 300.0
    assert config.request_timeout_secs == 5.0
    assert config.enabled is True


def test_poller_config_custom_values():
    """Test MLPollerConfig with custom values."""
    from src.nautilus.actors.ml_poller import MLPollerConfig

    config = MLPollerConfig(
        ml_service_url="http://localhost:9000/predict",
        poll_interval_secs=60.0,
        request_timeout_secs=10.0,
        enabled=False,
    )
    assert config.ml_service_url == "http://localhost:9000/predict"
    assert config.poll_interval_secs == 60.0
    assert config.request_timeout_secs == 10.0
    assert config.enabled is False


# MLPoller helper
def _make_poller(url="http://localhost:8000/predict", interval=60.0, enabled=True):
    """Helper to create a poller with mocked clock and logger."""
    from src.nautilus.actors.ml_poller import MLPoller, MLPollerConfig

    config = MLPollerConfig(
        ml_service_url=url,
        poll_interval_secs=interval,
        enabled=enabled,
    )
    poller = MLPoller(config)

    # Mock the logger and clock (these come from nautilus Actor)
    poller.log = MagicMock()
    poller.clock = MagicMock()
    poller.clock.utc_now.return_value = 1714320000000000000
    poller.clock.timer = MagicMock()
    poller.clock.cancel_timer = MagicMock()
    poller.publish_data = MagicMock()

    return poller


# MLPoller lifecycle tests
def test_poller_initialization():
    """Test poller initializes with correct state."""
    poller = _make_poller()
    assert poller._last_prediction is None
    assert poller._consecutive_errors == 0
    assert poller._max_consecutive_errors == 10
    assert poller.config.enabled is True


def test_poller_disabled_skips_start():
    """Test that disabled poller doesn't register timer."""
    poller = _make_poller(enabled=False)
    poller.on_start()
    poller.clock.timer.assert_not_called()


def test_poller_start_registers_timer():
    """Test that enabled poller registers timer on start."""
    poller = _make_poller(interval=120.0)
    poller.on_start()

    poller.clock.timer.assert_called_once_with(
        name="ml_poll",
        interval=120.0,
        callback=poller._on_timer,
    )


def test_poller_stop_cancels_timer():
    """Test that stopping poller cancels timer."""
    poller = _make_poller()
    poller.on_start()
    poller.on_stop()

    poller.clock.cancel_timer.assert_called_once_with("ml_poll")


def test_poller_stop_closes_session():
    """Test that stopping poller schedules session close."""
    poller = _make_poller()

    # Create a mock session
    mock_session = AsyncMock()
    mock_session.closed = False

    poller._session = mock_session

    # Mock asyncio.ensure_future to capture the coroutine
    with patch("asyncio.ensure_future") as mock_ensure:
        poller.on_stop()
        # Verify that ensure_future was called (async scheduling)
        mock_ensure.assert_called_once()


def test_last_prediction_property():
    """Test last_prediction property returns current prediction."""
    from src.nautilus.models import MLPrediction

    poller = _make_poller()
    pred = MLPrediction(
        instrument_id="XAU/USD",
        regime="TRENDING_DOWN",
        confidence=0.75,
        ts_event=0,
        ts_init=0,
    )
    poller._last_prediction = pred

    assert poller.last_prediction is pred
    assert poller.last_prediction.regime == "TRENDING_DOWN"


# MLPoller polling tests
@pytest.mark.asyncio
async def test_poll_success():
    """Test successful poll publishes prediction."""
    poller = _make_poller()

    # Mock HTTP response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={
        "instrument_id": "EUR/USD.IDEALPRO",
        "regime": "TRENDING_UP",
        "confidence": 0.92,
    })

    # Create async context manager mock
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    # Mock HTTP session
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.closed = False

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await poller._poll()

    # Verify prediction was created and published
    assert poller._last_prediction is not None
    assert poller._last_prediction.regime == "TRENDING_UP"
    assert poller._last_prediction.confidence == 0.92
    assert poller._consecutive_errors == 0
    poller.publish_data.assert_called_once()


@pytest.mark.asyncio
async def test_poll_creates_new_session_if_needed():
    """Test that poll creates new session if current one is closed."""
    poller = _make_poller()

    # Mock HTTP response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={
        "instrument_id": "GBP/USD",
        "regime": "RANGING",
        "confidence": 0.50,
    })
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.closed = True  # Session is closed, should create new one

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await poller._poll()

    assert poller._session is mock_session


@pytest.mark.asyncio
async def test_poll_http_error_increments_errors():
    """Test that HTTP error increments error counter."""
    poller = _make_poller()

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.closed = False

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await poller._poll()

    assert poller._consecutive_errors == 1
    assert poller._last_prediction is None
    poller.publish_data.assert_not_called()


@pytest.mark.asyncio
async def test_poll_timeout_increments_errors():
    """Test that timeout increments error counter."""
    poller = _make_poller()

    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=asyncio.TimeoutError())
    mock_session.closed = False

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await poller._poll()

    assert poller._consecutive_errors == 1


@pytest.mark.asyncio
async def test_poll_network_error_increments_errors():
    """Test that network error increments error counter."""
    poller = _make_poller()

    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=Exception("Connection refused"))
    mock_session.closed = False

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await poller._poll()

    assert poller._consecutive_errors == 1


@pytest.mark.asyncio
async def test_successful_poll_resets_error_counter():
    """Test that successful poll resets error counter."""
    poller = _make_poller()
    poller._consecutive_errors = 5  # Start with some errors

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={
        "instrument_id": "BTC/USD",
        "regime": "TRENDING_UP",
        "confidence": 0.85,
    })
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.closed = False

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await poller._poll()

    assert poller._consecutive_errors == 0  # Should be reset


@pytest.mark.asyncio
async def test_max_consecutive_errors_stops_poller():
    """Test that max consecutive errors stops the poller."""
    poller = _make_poller()
    poller._consecutive_errors = 9  # One short of max

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.closed = False

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await poller._poll()

    # Should have canceled timer after hitting max errors (9 -> 10 -> stop)
    poller.clock.cancel_timer.assert_called_once_with("ml_poll")


@pytest.mark.asyncio
async def test_poll_with_missing_json_fields():
    """Test poll handles missing JSON fields gracefully."""
    poller = _make_poller()

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={
        # Missing instrument_id, regime, confidence
    })
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.closed = False

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await poller._poll()

    # Should use defaults from .get()
    assert poller._last_prediction is not None
    assert poller._last_prediction.instrument_id == "UNKNOWN"
    assert poller._last_prediction.regime == "UNKNOWN"
    assert poller._last_prediction.confidence == 0.0


@pytest.mark.asyncio
async def test_poll_with_invalid_confidence():
    """Test poll handles invalid confidence value."""
    poller = _make_poller()

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={
        "instrument_id": "EUR/USD",
        "regime": "TRENDING_UP",
        "confidence": "invalid",  # Not a number
    })
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.closed = False

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await poller._poll()

    # Invalid confidence raises exception, which is caught and increments error counter
    assert poller._last_prediction is None  # Should not have a prediction
    assert poller._consecutive_errors == 1  # Should have recorded an error


def test_on_timer_schedules_poll():
    """Test that timer callback schedules a poll."""
    poller = _make_poller()

    with patch("asyncio.ensure_future") as mock_ensure:
        poller._on_timer(None)
        mock_ensure.assert_called_once()


# Test NAUTILUS_AVAILABLE flags
def test_nautilus_available_flags():
    """Test that NAUTILUS_AVAILABLE flags are accessible."""
    from src.nautilus.models import NAUTILUS_AVAILABLE as MODELS_AVAILABLE
    from src.nautilus.actors.ml_poller import NAUTILUS_AVAILABLE as ACTOR_AVAILABLE

    # Both should be False in this environment
    assert isinstance(MODELS_AVAILABLE, bool)
    assert isinstance(ACTOR_AVAILABLE, bool)
    # In CI/test environment without nautilus installed:
    assert MODELS_AVAILABLE is False
    assert ACTOR_AVAILABLE is False
