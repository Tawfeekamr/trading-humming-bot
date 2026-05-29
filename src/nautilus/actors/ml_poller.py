"""Actor that periodically polls the FastAPI ML regime prediction service.

Publishes MLPrediction objects to NautilusTrader's internal message bus
so strategies can gate their trading logic on regime state.

NOTE: This module handles the case where nautilus_trader is not installed
by using import guards and creating minimal stub classes for testing.
"""
import asyncio
from typing import Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None  # Will be mocked in tests

try:
    from nautilus_trader.common.actor import Actor
    from nautilus_trader.config import ActorConfig
    from nautilus_trader.core.data import Data
    from nautilus_trader.model.data import DataType
    NAUTILUS_AVAILABLE = True
except ImportError:
    # nautilus_trader not available - create minimal stubs for testing
    NAUTILUS_AVAILABLE = False

    class ActorConfig:
        """Minimal ActorConfig placeholder."""
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class Actor:
        """Minimal Actor placeholder for testing."""
        def __init__(self, config):
            self.config = config
            self.log = _LogStub()
            self.clock = None

        def on_start(self):
            pass

        def on_stop(self):
            pass

        def publish_data(self, data_type, data):
            pass

    class _LogStub:
        """Minimal logger stub."""
        def info(self, msg, *args):
            pass

        def warning(self, msg, *args):
            pass

        def error(self, msg, *args):
            pass

    class DataType:
        """Minimal DataType placeholder."""
        def __init__(self, data_type):
            self.data_type = data_type

from src.nautilus.models import MLPrediction, NAUTILUS_AVAILABLE as MODELS_HAVE_NAUTILUS


class MLPollerConfig(ActorConfig):
    """Configuration for the ML poller actor.

    Attributes
    ----------
    ml_service_url : str
        URL of the ML prediction service endpoint
    poll_interval_secs : float
        Seconds between polling attempts (default: 300 = 5 minutes)
    request_timeout_secs : float
        HTTP request timeout in seconds (default: 5)
    enabled : bool
        Whether the poller should run (default: True)
    """

    def __init__(
        self,
        ml_service_url: str = "http://bot:8080/predict",
        poll_interval_secs: float = 300.0,
        request_timeout_secs: float = 5.0,
        enabled: bool = True,
    ):
        self.ml_service_url = ml_service_url
        self.poll_interval_secs = poll_interval_secs
        self.request_timeout_secs = request_timeout_secs
        self.enabled = enabled


class MLPoller(Actor):
    """Periodically polls the shared FastAPI ML service and publishes
    regime predictions to NautilusTrader's message bus.

    The poller runs on a timer and handles network errors gracefully,
    stopping after consecutive errors exceed a threshold.

    Attributes
    ----------
    last_prediction : MLPrediction or None
        The most recent prediction received from the ML service
    """

    def __init__(self, config: MLPollerConfig):
        super().__init__(config)
        self._session = None
        self._last_prediction: Optional[MLPrediction] = None
        self._consecutive_errors: int = 0
        self._max_consecutive_errors: int = 10

    @property
    def last_prediction(self) -> Optional[MLPrediction]:
        """Get the most recent prediction received."""
        return self._last_prediction

    def on_start(self):
        """Initialize the timer when the actor starts."""
        if not self.config.enabled:
            self.log.info("ML poller disabled, skipping start")
            return

        self.log.info(
            f"ML poller starting, url={self.config.ml_service_url}, "
            f"interval={self.config.poll_interval_secs}s"
        )
        self._timer = self.clock.timer(
            name="ml_poll",
            interval=self.config.poll_interval_secs,
            callback=self._on_timer,
        )

    def on_stop(self):
        """Clean up timer and session when the actor stops."""
        self.log.info("ML poller stopping")
        if hasattr(self, "_timer") and self.clock:
            self.clock.cancel_timer("ml_poll")
        if self._session and not self._session.closed:
            asyncio.ensure_future(self._close_session())

    async def _close_session(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def _on_timer(self, event):
        """Timer callback that triggers a poll."""
        asyncio.ensure_future(self._poll())

    async def _poll(self):
        """Poll the ML service and publish predictions."""
        if aiohttp is None:
            self.log.error("aiohttp not available, cannot poll ML service")
            return

        url = self.config.ml_service_url
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_secs)

        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(timeout=timeout)

            async with self._session.get(url) as resp:
                if resp.status != 200:
                    self.log.warning(f"ML service returned HTTP {resp.status}")
                    self._consecutive_errors += 1
                    # Check if we should stop
                    if self._consecutive_errors >= self._max_consecutive_errors:
                        self.log.error(
                            f"ML service unreachable after {self._max_consecutive_errors} attempts, "
                            f"stopping poller"
                        )
                        if self.clock:
                            self.clock.cancel_timer("ml_poll")
                    return

                data = await resp.json()
                prediction = MLPrediction(
                    instrument_id=data.get("instrument_id", "UNKNOWN"),
                    regime=data.get("regime", "UNKNOWN"),
                    confidence=float(data.get("confidence", 0.0)),
                    ts_event=self.clock.utc_now(),
                    ts_init=self.clock.utc_now(),
                )

                self._last_prediction = prediction
                self._consecutive_errors = 0
                self.publish_data(DataType(MLPrediction), prediction)

                self.log.info(
                    f"ML prediction received: regime={prediction.regime}, "
                    f"confidence={prediction.confidence:.2f}"
                )

        except asyncio.TimeoutError:
            self._consecutive_errors += 1
            self.log.warning(
                f"ML service timeout after {self.config.request_timeout_secs}s "
                f"(errors: {self._consecutive_errors})"
            )
        except Exception as e:
            self._consecutive_errors += 1
            self.log.error(f"ML service error: {e} (errors: {self._consecutive_errors})")

        if self._consecutive_errors >= self._max_consecutive_errors:
            self.log.error(
                f"ML service unreachable after {self._max_consecutive_errors} attempts, "
                f"stopping poller"
            )
            if self.clock:
                self.clock.cancel_timer("ml_poll")


__all__ = ["MLPoller", "MLPollerConfig", "NAUTILUS_AVAILABLE"]
