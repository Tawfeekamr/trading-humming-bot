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

# Configure Hummingbot log rotation and suppress noisy loggers
LOG_CONF="/home/hummingbot/conf/hummingbot_logs.yml"
if [ -f "$LOG_CONF" ]; then
    # Add log rotation (max 50MB, 3 backups) if not already configured
    if ! grep -q "maxBytes:" "$LOG_CONF"; then
        sed -i 's/class: FileHandler/class: logging.handlers.RotatingFileHandler/' "$LOG_CONF"
        sed -i '/class: logging.handlers.RotatingFileHandler/a\        maxBytes: 52428800\n        backupCount: 3' "$LOG_CONF"
        echo "Added log rotation to Hummingbot logging config"
    fi
    # Suppress noisy order_book event 901 errors (known Hummingbot paper trade bug)
    if ! grep -q "order_book:" "$LOG_CONF"; then
        printf '    hummingbot.core.data_type.order_book:\n        level: CRITICAL\n        propagate: false\n        handlers:\n            - file_handler\n' >> "$LOG_CONF"
        echo "Suppressed order_book event 901 errors in logging config"
    fi
fi

# Clean up old logs (keep last 7 days)
find /home/hummingbot/logs -name "bot_*.log*" -mtime +7 -delete 2>/dev/null
find /home/hummingbot/logs -name "events_*.jsonl" -mtime +7 -delete 2>/dev/null
find /home/hummingbot/logs -name "logs_*.log*" -mtime +3 -delete 2>/dev/null
find /home/hummingbot/logs -name "crashes.log*" -mtime +7 -delete 2>/dev/null
echo "Cleaned up old logs"

# Patch MQTT retry loop to break after first failure instead of retrying forever.
# Without this, the infinite retry blocks the event loop and prevents ticks (Issue #8012).
MQTT_CMD="/home/hummingbot/hummingbot/client/command/mqtt_command.py"
if [ -f "$MQTT_CMD" ] && ! grep -q "break  # grid-bot" "$MQTT_CMD"; then
    sed -i 's/await asyncio.sleep(self._mqtt_sleep_rate_autostart_retry)/break  # grid-bot: fail fast so event loop ticks/' "$MQTT_CMD"
    echo "Patched MQTT retry loop"
fi

exec python ./bin/hummingbot_quickstart.py
