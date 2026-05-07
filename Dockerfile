FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data logs

# Run as non-root user for security
RUN groupadd -r botuser && useradd -r -g botuser -s /sbin/nologin botuser \
    && chown -R botuser:botuser /app
USER botuser

CMD ["python", "-m", "hummingbot", "start", "--script", "hummingbot_files/scripts/ta_grid_btcusdt.py"]
