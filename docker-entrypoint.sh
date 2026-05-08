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

# Disable MQTT entirely — it spams reconnect logs and is not needed.
# 1) Force mqtt_autostart=false in the generated config
CONF="/home/hummingbot/conf/conf_client.yml"
if [ -f "$CONF" ]; then
    sed -i 's/mqtt_autostart: true/mqtt_autostart: false/' "$CONF"
fi

# 2) Comment out the mqtt_start() call so it never runs even if config is overridden
APP="/home/hummingbot/hummingbot/client/hummingbot_application.py"
if [ -f "$APP" ] && ! grep -q "MQTT disabled for trading bot" "$APP"; then
    cp "$APP" "$APP.bak"
    # Remove the MQTT validation check in run_headless
    sed -i '/if not self.client_config_map.mqtt_bridge.mqtt_autostart/,/raise RuntimeError("MQTT is required for headless mode")/c\            pass  # MQTT check disabled for trading bot' "$APP"
    # Disable the mqtt_start() call entirely
    sed -i 's/            self.mqtt_start()/            pass  # MQTT disabled for trading bot/' "$APP"

    # Verify patches succeeded
    if ! grep -q "MQTT disabled for trading bot" "$APP"; then
        echo "WARNING: MQTT disable patch failed"
    fi
fi

exec python ./bin/hummingbot_quickstart.py
