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

# Use the dual-engine strategy script
export SCRIPT_CONFIG=ta_grid_trend_conf.yml

# Pre-create conf_client.yml so Hummingbot reads our MQTT settings on first load.
# If the file already exists (recreated container but mounted conf), patch it.
CONF="/home/hummingbot/conf/conf_client.yml"
mkdir -p /home/hummingbot/conf
if [ -f "$CONF" ]; then
    sed -i 's/mqtt_host: localhost/mqtt_host: mosquitto/' "$CONF"
    sed -i 's/mqtt_autostart: false/mqtt_autostart: true/' "$CONF"
    echo "Patched existing conf_client.yml"
else
    # Write minimal config with correct MQTT settings.
    # Hummingbot will merge/expand this with defaults for missing fields.
    cat > "$CONF" <<'YAML'
mqtt_bridge:
  mqtt_host: mosquitto
  mqtt_port: 1883
  mqtt_username: ''
  mqtt_password: ''
  mqtt_namespace: hbot
  mqtt_ssl: false
  mqtt_logger: true
  mqtt_notifier: true
  mqtt_commands: true
  mqtt_events: true
  mqtt_external_events: true
  mqtt_autostart: true
YAML
    echo "Created conf_client.yml with mosquitto MQTT config"
fi

# Patch MQTT retry loop to break after first failure instead of retrying forever.
# Without this, the infinite retry blocks the event loop and prevents ticks (Issue #8012).
MQTT_CMD="/home/hummingbot/hummingbot/client/command/mqtt_command.py"
if [ -f "$MQTT_CMD" ] && ! grep -q "break  # grid-bot" "$MQTT_CMD"; then
    sed -i 's/await asyncio.sleep(self._mqtt_sleep_rate_autostart_retry)/break  # grid-bot: fail fast so event loop ticks/' "$MQTT_CMD"
    echo "Patched MQTT retry loop"
fi

exec python ./bin/hummingbot_quickstart.py
