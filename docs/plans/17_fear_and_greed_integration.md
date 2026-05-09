# Integration of Crypto Fear & Greed Index

This plan outlines the integration of the Alternative.me Crypto Fear & Greed Index API into the grid bot to act as a safety circuit breaker.

## Overview
- **F&G Threshold**: The default threshold for pausing the bot will be set to `25` (Extreme Fear). The bot will automatically pause buying if the index drops below this number to avoid catching falling knives during market crashes.

## Proposed Changes

### 1. Indicators Module
#### [NEW] `src/indicators/fear_and_greed.py`
- Create a new async data fetcher class `FearAndGreed` that calls `https://api.alternative.me/fng/?limit=1`.
- It will cache the result to prevent spamming the API (the index updates daily, so fetching once per hour is sufficient).
- Runs an async background loop to keep the `current_value` updated without blocking the main bot tick.

### 2. Strategy Configuration
#### [MODIFY] `config/strategy.yaml`
- Add a new section under `indicators:` for `fear_and_greed` with a configuration parameter: `pause_below: 25`.

### 3. Grid State Machine
#### [MODIFY] `src/grid/grid_state.py`
- Update `GridStateMachine.evaluate()` to accept the `fng_value` and `fng_threshold`.
- Add a rule: If `fng_value <= fng_threshold`, set state to `GridState.PAUSED`.

### 4. Main Bot Script
#### [MODIFY] `hummingbot_files/scripts/ta_grid_btcusdt.py`
- Initialize the `FearAndGreed` indicator on startup.
- Read the `fng_threshold` from the configuration.
- Launch the background fetch loop via `asyncio.create_task`.
- Pass the cached `fng_value` into the `state_machine.evaluate()` during the `on_tick()` method.
- Update the event logger payload to include `fng_value` in the `indicators_updated` and `state_changed` logs for transparency.

## Verification Plan
### Automated Tests
- N/A - The API requires an external network call, so we will rely on manual verification and log monitoring.

### Manual Verification
- We will start the bot, verify the startup logs show the F&G API fetching successfully, and ensure the bot successfully evaluates its state based on the fetched value.
- If the current F&G index happens to be <= 25, we will confirm the bot immediately enters the `PAUSED` state with a "fear and greed" trigger reason.
