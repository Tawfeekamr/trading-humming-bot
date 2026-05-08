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

source /opt/conda/etc/profile.d/conda.sh
conda activate hummingbot

# Configure MQTT to connect to the mosquitto sidecar broker
CONF="/home/hummingbot/conf/conf_client.yml"
if [ -f "$CONF" ]; then
    sed -i 's/mqtt_host: localhost/mqtt_host: mosquitto/' "$CONF"
    sed -i 's/mqtt_autostart: false/mqtt_autostart: true/' "$CONF"
fi

exec python ./bin/hummingbot_quickstart.py
