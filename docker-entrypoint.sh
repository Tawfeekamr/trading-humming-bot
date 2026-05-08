#!/bin/bash
set -e

# Initialize password verification file on first run
if [ ! -f /home/hummingbot/conf/.password_verification ]; then
    source /opt/conda/etc/profile.d/conda.sh
    conda activate hummingbot
    python -c "
from hummingbot.client.config.config_crypt import ETHKeyFileSecretManger, PASSWORD_VERIFICATION_PATH, PASSWORD_VERIFICATION_WORD
sm = ETHKeyFileSecretManger('${CONFIG_PASSWORD}')
encrypted = sm.encrypt_secret_value(PASSWORD_VERIFICATION_WORD, PASSWORD_VERIFICATION_WORD)
with open(PASSWORD_VERIFICATION_PATH, 'w') as f:
    f.write(encrypted)
print('Password verification file created')
"
fi

# Start hummingbot
source /opt/conda/etc/profile.d/conda.sh
conda activate hummingbot

# Disable MQTT reconnect spam while keeping the event loop alive.
# Keep mqtt_autostart=true so the async MQTT task runs (it keeps Hummingbot's
# event loop ticking). Patch the retry loop in mqtt_command.py to stop after
# the first failure instead of retrying forever.
CONF="/home/hummingbot/conf/conf_client.yml"
if [ -f "$CONF" ]; then
    sed -i 's/mqtt_autostart: false/mqtt_autostart: true/' "$CONF"
fi

APP="/home/hummingbot/hummingbot/client/hummingbot_application.py"
MQTT="/home/hummingbot/hummingbot/client/command/mqtt_command.py"

if [ -f "$APP" ] && ! grep -q "MQTT check disabled for trading bot" "$APP"; then
    cp "$APP" "$APP.bak"
    # Remove the MQTT validation check in run_headless
    sed -i '/if not self.client_config_map.mqtt_bridge.mqtt_autostart/,/raise RuntimeError("MQTT is required for headless mode")/c\            pass  # MQTT check disabled for trading bot' "$APP"

    if ! grep -q "MQTT check disabled for trading bot" "$APP"; then
        echo "WARNING: MQTT check patch failed"
    fi
fi

if [ -f "$MQTT" ] && ! grep -q "MQTT retry disabled for trading bot" "$MQTT"; then
    cp "$MQTT" "$MQTT.bak"
    # Replace the autostart retry sleep with a break — fail once, then stop
    sed -i 's/await asyncio.sleep(self._mqtt_sleep_rate_autostart_retry)/break  # MQTT retry disabled for trading bot/' "$MQTT"

    if ! grep -q "MQTT retry disabled for trading bot" "$MQTT"; then
        echo "WARNING: MQTT retry patch failed"
    fi
fi

exec python ./bin/hummingbot_quickstart.py
