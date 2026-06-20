# Capital Manager Product Requirements Document (PRD)

## 1. Overview
Currently, capital allocation in the `trading-engine-core` is heavily fragmented. Each trading strategy (Grid, Swing, Signal, Mean Reversion) statically defines its own capital limits in independent configurations or hardcoded logic. 

**Problems with Current State:**
- **No Global Visibility**: It is impossible to query how much total capital is actively locked in trades versus sitting idle across the entire engine.
- **Static Scaling**: Strategies cannot dynamically scale up their size during highly favorable market regimes (or scale down during unfavorable ones).
- **Inefficient Reinvestment**: Profits realized by one strategy (e.g., Grid) are isolated and cannot be reallocated to fund entries for another strategy (e.g., Signal or Mean Reversion).
- **Hardcoded Limitations**: The Mean Reversion strategy currently hardcodes a `100 USDT` base allocation, and the Signal engine relies on a static `capital_pct`.

**Target State:**
A centralized **Capital Manager** module that acts as the engine's internal bank. It maintains real-time visibility of total portfolio equity, enforces a global cash reserve, and dynamically routes capital to strategies based on their real-time performance and ML-predicted market regimes.

---

## 2. Business Requirements

### 2.1 Centralized Capital Pool
- **Total Equity Tracking**: The Capital Manager must continuously sync with the exchange to track `Total Account Equity` (combining USDT balances and Mark-to-Market value of held inventory).
- **Global Reserve (Safeguard)**: A configurable minimum reserve percentage (e.g., `20%`) must be maintained in pure USDT at all times. Strategies cannot draw from this reserve.
- **Free Capital**: The remaining capital (`Total Equity - Locked in Trades - Global Reserve`) is the `Free Capital` pool.

### 2.2 Dynamic Allocation & Routing
- **Request/Release Lifecycle**: Before placing orders, a strategy MUST request an allocation from the Capital Manager. Upon closing a trade, the strategy MUST release the principal + realized P&L back to the Capital Manager.
- **Auto-Compounding (Reinvestment)**: When a strategy returns a positive P&L, the Capital Manager will reinvest a configurable percentage back into the `Free Capital` pool, raising the overall purchasing power for subsequent trades across all strategies.
- **Regime-Based Shifting**: 
  - If the `RegimeCache` detects a **Trending** market, the Capital Manager should actively reduce allocations available to Mean Reversion/Grid strategies and prioritize requests from Trend/Swing strategies.
  - If the `RegimeCache` detects a **Ranging** market, capital should flow toward the Grid strategy.
- **Performance-Based Throttling**: If a strategy's win-rate or recent PnL drops below a specific threshold (tracked via `UnifiedTradeJournal`), the Capital Manager must reduce its maximum allowed allocation limit.

### 2.3 System Visibility (UI / Notifications)
- **API Visibility**: Expose a new HTTP endpoint (`GET /api/v1/capital`) that returns a strict breakdown of `Total Equity`, `Reserve`, `Free Capital`, and `Allocated per Strategy`.
- **Telegram Commands**: Introduce a `/capital` command for the Telegram bot to return a human-readable snapshot of the current allocation state.

---

## 3. Technical Implementation Blueprint (For AI Coder)

### 3.1 New Core Module: `src/capital/`
Create a new directory and module `src/capital/` containing:

1. **`manager.rs`**: 
   - Struct: `CapitalManager`
   - State: `Arc<RwLock<CapitalState>>`
   - Fields: `total_equity`, `reserve_limit_pct`, `allocations: HashMap<String, f64>`.
   - Methods: `sync_equity(balances, order_books)`, `request_capital(strategy, amount) -> Result<f64>`, `release_capital(strategy, amount, pnl)`.

2. **`dynamic_scaler.rs`**:
   - Evaluates rules for the `CapitalManager`.
   - Reads ML regimes from `RegimeCache` and historical performance from `UnifiedTradeJournal`.
   - Method: `calculate_max_allocation(strategy, base_config_limit) -> f64`.

### 3.2 Strategy Refactoring Tasks
Each strategy must be updated to stop relying entirely on static configs:

- **Grid Strategy (`src/strategy/grid.rs`)**:
  - Replace `self.config.capital_usdt` logic.
  - Before laying out the grid, call `capital_manager.request_capital("grid", desired_amount)`. If it returns less than requested, adjust the grid size or density.
  - On `Grid SELL`, call `release_capital` with the returned principal + `realized_pnl`.

- **Swing Strategy (`src/strategy/swing.rs`)**:
  - Replace `self.config.capital`.
  - On entry, request capital. On exit, release capital + P&L.

- **Signal Strategy (`src/signal/engine.rs`)**:
  - Instead of taking a flat `capital_pct` from total, take `capital_pct` out of the `Free Capital` explicitly provided by the `CapitalManager`.

- **Mean Reversion Strategy (`src/strategy/mean_reversion.rs`)**:
  - Locate `let qty = (100.0 * size_mult) / mid;` (Line 203 approx).
  - Remove the `100.0` hardcode. Dynamically request allocation based on the ML `size_mult`.

### 3.3 Engine Integration (`src/engine.rs`)
- Instantiate `CapitalManager` on boot.
- Pass an `Arc<CapitalManager>` to all strategies.
- In the `while let Some(event) = ws_rx.recv().await` loop, update the `CapitalManager` with the latest balances during `feed_breaker`.

### 3.4 API & Notifications Integration
- **`src/api/handlers.rs`**: Add a route pointing to the `CapitalManager`'s read-only state.
- **`src/notifications/telegram.rs`**: Add the `/capital` command matching. Inject a handle to the `CapitalManager` to format the response string.

## 4. Edge Cases & Safety Checks
- **Concurrent Requests**: Ensure `request_capital` is thread-safe and prevents overallocation if two strategies fire concurrently on the same tick.
- **Orphaned Capital**: If a strategy errors out or is force-stopped, ensure its locked capital is properly reclaimed by the manager.
- **Circuit Breaker Precedence**: The Risk Manager (`circuit_breaker.rs`) holds ultimate authority over halting trading. The Capital Manager handles *how much* money is used, while the Risk Manager handles *if* money can be used at all.
