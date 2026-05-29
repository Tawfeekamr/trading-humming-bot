"""Tests for MQTT bridge actor."""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.nautilus.actors.mqtt_bridge import MQTTBridge, MQTTBridgeConfig


def _make_bridge(host="mosquitto", port=1883, prefix="hbot/nautilus", enabled=True):
    config = MQTTBridgeConfig(
        mqtt_host=host,
        mqtt_port=port,
        topic_prefix=prefix,
        enabled=enabled,
    )
    bridge = MQTTBridge(config)
    bridge.log = MagicMock()
    bridge.clock = MagicMock()
    bridge.clock.utc_now.return_value = 0
    bridge.clock.timer = MagicMock()
    bridge.clock.cancel_timer = MagicMock()
    return bridge


def test_bridge_disabled_skips_start():
    bridge = _make_bridge(enabled=False)
    bridge.on_start()
    bridge.clock.timer.assert_not_called()


def test_bridge_start_registers_timer():
    bridge = _make_bridge()
    # Mock mqtt module availability
    with patch("src.nautilus.actors.mqtt_bridge.PAHO_MQTT_AVAILABLE", True):
        bridge.on_start()
    bridge.clock.timer.assert_called_once()


def test_build_status_payload():
    bridge = _make_bridge()
    bridge._positions = [{"symbol": "EUR/USD", "side": "LONG", "pnl": 50.0}]
    payload = bridge._build_status_payload()
    data = json.loads(payload)
    assert data["positions"][0]["symbol"] == "EUR/USD"
    assert data["positions"][0]["side"] == "LONG"


def test_build_status_payload_empty():
    bridge = _make_bridge()
    bridge._positions = []
    payload = bridge._build_status_payload()
    data = json.loads(payload)
    assert data["positions"] == []
    assert data["status"] == "running"


@patch("src.nautilus.actors.mqtt_bridge.mqtt")
def test_publish_status_calls_mqtt(mock_mqtt):
    bridge = _make_bridge()
    mock_client = MagicMock()
    mock_client.publish.return_value = MagicMock(rc=0)
    bridge._client = mock_client
    bridge._connected = True
    bridge._positions = []

    bridge._publish_status()

    bridge._client.publish.assert_called_once()
    call_args = bridge._client.publish.call_args
    assert call_args[0][0] == "hbot/nautilus/status"
    assert "running" in call_args[0][1]
