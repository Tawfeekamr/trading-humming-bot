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

# Disable MQTT (not needed, causes connector readiness issues)
CONF="/home/hummingbot/conf/conf_client.yml"
if [ -f "$CONF" ]; then
    sed -i 's/mqtt_logger: true/mqtt_logger: false/' "$CONF"
    sed -i 's/mqtt_notifier: true/mqtt_notifier: false/' "$CONF"
    sed -i 's/mqtt_commands: true/mqtt_commands: false/' "$CONF"
    sed -i 's/mqtt_events: true/mqtt_events: false/' "$CONF"
    sed -i 's/mqtt_external_events: true/mqtt_external_events: false/' "$CONF"
fi

# Patch quickstart to NOT force mqtt_autostart=True in headless mode
QUICKSTART="/home/hummingbot/bin/hummingbot_quickstart.py"
if [ -f "$QUICKSTART" ]; then
    sed -i 's/client_config_map.mqtt_bridge.mqtt_autostart = True/pass  # MQTT disabled for trading bot/' "$QUICKSTART"
fi

exec python ./bin/hummingbot_quickstart.py
