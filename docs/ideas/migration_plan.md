# Architectural Migration Plan: Go/Python Microservices

This plan outlines the strategy for splitting your current monolithic Hummingbot strategy into a two-part microservices architecture. The goal is to achieve an ultra-small Docker image size (~20MB) and near-zero latency execution via Go, while preserving your existing Machine Learning and Pandas infrastructure in Python.

## Major Trade-offs & Warnings

> **Abandoning Hummingbot:** Moving execution to Go means we can no longer use the Hummingbot framework (`StrategyV2Base`). We will have to build the Binance WebSocket connectors, order tracking, and rate-limiting logic from scratch using a Go SDK (e.g., `adshao/go-binance`).

> **Infrastructure Shift:** You will be deploying **two** Docker containers instead of one. They must be able to communicate over a local network (e.g., via `docker-compose`).

## Proposed Architecture

The system will be decoupled into two independent services:

### 1. The Execution Engine (Golang)
- **Role:** Handles live market data, executes trades, manages the grid/trend state, and sends Telegram alerts.
- **Tech Stack:** Go 1.21+, `adshao/go-binance` (Exchange API), `markcheno/go-talib` (Real-time indicators).
- **Footprint:** ~20MB Docker Image, <50MB RAM.

### 2. The ML Brain (Python)
- **Role:** Acts as an internal API service. It receives historical candles, calculates complex `pandas_ta` features, and returns the Scikit-learn Regime prediction.
- **Tech Stack:** Python 3.10+, `FastAPI`, `scikit-learn`, `pandas`, `pandas_ta`.
- **Footprint:** ~800MB Docker Image.

---

## Proposed Changes

### Phase 1: Python ML Microservice
We will strip away Hummingbot from the Python codebase, leaving only the data science components wrapped in a lightweight web server.

- **`src/api/main.py`**: Create a `FastAPI` server with a single endpoint `POST /predict_regime`. It will accept JSON containing the last 250 hourly candles.
- **`src/ml/regime_classifier.py`**: Modify the inputs to accept raw JSON payloads from the API instead of Hummingbot's `CandleFeed`.
- **`Dockerfile.python`**: Create a slimmed-down Dockerfile that only installs `pandas`, `scikit-learn`, and `fastapi`.

### Phase 2: Go Execution Engine
Re-implementing the core logic in Go.

- **`go-bot/main.go`**: Initialization, config loading, and Telegram bot setup.
- **`go-bot/exchange/binance.go`**: Replaces Hummingbot. Sets up WebSockets for `kline` (candles) and `user_data` (order fills/updates) using `adshao/go-binance`.
- **`go-bot/strategy/grid.go` & `go-bot/strategy/trend.go`**: Port `src/grid/grid_manager.py` and `src/trend/trend_manager.py` to Go structs and methods.
- **`go-bot/ml_client.go`**: An HTTP client that sends candles to `http://python-api:8000/predict_regime` every hour to update the current market regime state.
- **`Dockerfile.go`**: A multi-stage Docker build that compiles the Go binary and places it in a bare `scratch` container.

### Phase 3: Deployment Orchestration
- **`docker-compose.yml`**: Update the compose file to run both services on a private Docker network.

## Open Questions for Review

1. **Exchange Flexibility:** Are you exclusively trading on Binance? Writing custom Go exchange connectors is manageable for one exchange, but becomes exponentially difficult if you plan to support OKX, Bybit, and Kucoin simultaneously (which Hummingbot does out of the box).
2. **Time Investment:** Rebuilding order execution and WebSocket management from scratch in Go is a heavy lift (estimated 2-3 weeks). Are the Docker image size and microsecond latency worth the rewrite?
