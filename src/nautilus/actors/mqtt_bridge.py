"""MQTT bridge actor — publishes NautilusTrader status to Mosquitto.

This lets the existing Hummingbot dashboard and Telegram bot
monitor the Forex engine alongside the crypto engine.
"""
import json
from typing import Optional
from unittest.mock import MagicMock

# Import guard for environments without nautilus_trader installed
try:
    from nautilus_trader.common.actor import Actor
    from nautilus_trader.config import ActorConfig
    NAUTILUS_AVAILABLE = True
except ImportError:
    # Fallback for testing without nautilus_trader
    NAUTILUS_AVAILABLE = False

    class Actor:
        def __init__(self, config):
            self.config = config

        # Add stubs for Actor attributes used in tests
        log = None
        clock = None

    # Simple frozen config that works with keyword arguments
    # and properly handles the frozen=True keyword argument
    class ActorConfig:
        def __init_subclass__(cls, frozen=False, **kwargs):
            super().__init_subclass__(**kwargs)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

# Import guard for paho-mqtt (may not be installed in dev/test environments)
try:
    import paho.mqtt.client as mqtt
    PAHO_MQTT_AVAILABLE = True
except ImportError:
    PAHO_MQTT_AVAILABLE = False
    # Create a dummy module for tests
    mqtt = MagicMock()


class MQTTBridgeConfig(ActorConfig, frozen=True):
    """Configuration for the MQTT bridge actor."""
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    topic_prefix: str = "hbot/nautilus"
    status_interval_secs: float = 30.0
    enabled: bool = True


class MQTTBridge(Actor):
    """Bridges NautilusTrader status to the shared Mosquitto MQTT broker."""

    def __init__(self, config: MQTTBridgeConfig):
        super().__init__(config)
        self._client = None
        self._connected: bool = False
        self._positions: list[dict] = []

    def on_start(self):
        if not self.config.enabled:
            self.log.info("MQTT bridge disabled, skipping start")
            return

        self.log.info(
            f"MQTT bridge starting, host={self.config.mqtt_host}, "
            f"port={self.config.mqtt_port}"
        )

        if not PAHO_MQTT_AVAILABLE:
            self.log.warning("paho-mqtt not available, skipping MQTT setup")
            return

        try:
            self._client = mqtt.Client(client_id="nautilus-bridge", protocol=mqtt.MQTTv311)
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect

            self._client.connect(
                self.config.mqtt_host,
                self.config.mqtt_port,
                keepalive=60,
            )
            self._client.loop_start()
        except Exception as e:
            self.log.error(f"MQTT connection failed: {e}")
            return

        self._timer = self.clock.timer(
            name="mqtt_status",
            interval=self.config.status_interval_secs,
            callback=self._on_timer,
        )

    def on_stop(self):
        if hasattr(self, "_timer"):
            self.clock.cancel_timer("mqtt_status")
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

    def update_positions(self, positions: list[dict]):
        """Update the cached position list for status publishing."""
        self._positions = positions

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            self.log.info("MQTT bridge connected to broker")
        else:
            self.log.error(f"MQTT connection failed with code {rc}")

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        if rc != 0:
            self.log.warning(f"MQTT unexpected disconnect (rc={rc})")

    def _on_timer(self, event):
        self._publish_status()

    def _build_status_payload(self) -> str:
        """Build JSON status payload for MQTT publishing."""
        payload = {
            "status": "running",
            "trader_id": "FOREX-001",
            "positions": self._positions,
            "timestamp": str(self.clock.utc_now()),
        }
        return json.dumps(payload)

    def _publish_status(self):
        """Publish status to MQTT broker."""
        if not self._connected or not self._client:
            return

        topic = f"{self.config.topic_prefix}/status"
        payload = self._build_status_payload()

        result = self._client.publish(topic, payload, qos=1)
        if result.rc != 0:
            self.log.warning(f"MQTT publish failed (rc={result.rc})")
