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

# Patch Hummingbot to remove MQTT requirement in headless mode
# MQTT reconnection loop blocks the asyncio event loop, preventing
# the Binance connector from becoming ready. We skip MQTT entirely.
APP="/home/hummingbot/hummingbot/client/hummingbot_application.py"
if [ -f "$APP" ]; then
    # Remove the MQTT validation check in run_headless
    sed -i '/if not self.client_config_map.mqtt_bridge.mqtt_autostart/,/raise RuntimeError("MQTT is required for headless mode")/c\            pass  # MQTT check disabled for trading bot' "$APP"
    # Keep MQTT autostart True so the code path doesn't crash elsewhere
fi

exec python ./bin/hummingbot_quickstart.py
