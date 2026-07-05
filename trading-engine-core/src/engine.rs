use anyhow::Result;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Instant, Duration};
use tracing::{info, warn, error};
use crate::config::AppConfig;
use crate::connector::Connector;
use crate::connector::binance_ws::{BinanceWs, WsEvent};
use crate::connector::types::*;
use crate::risk::RiskManager;
use crate::capital::CapitalManager;
use crate::notifications::TelegramBot;
use crate::strategy::{Strategy, TickContext, MarketRegime};
use crate::strategy::status_cache::StrategyStatusCache;
use crate::strategy::regime_cache::RegimeCache;
use crate::strategy::routing_cache::RoutingCache;
use crate::models::bar::Bar;
use crate::bar_cache::BarCache;
use crate::signal::SignalEngine;

pub struct Engine {
    config: AppConfig,
    connector: Arc<dyn Connector>,
    strategies: Vec<Box<dyn Strategy>>,
    risk: RiskManager,
    telegram: TelegramBot,
    signal: Option<SignalEngine>,
    bar_buffers: BarCache,
    order_books: HashMap<String, OrderBook>,
    status_cache: StrategyStatusCache,
    regime_cache: RegimeCache,
    /// PPO router decision (active engine + size mult + flat). Read each tick
    /// at the top of `tick_strategies` to pause non-active engines and force
    /// flat when the router says so. None (or stale) ⇒ route unchanged.
    routing_cache: RoutingCache,
    capital: CapitalManager,
    /// Throttle for risk-state persistence (see feed_breaker). None = save next tick.
    last_risk_save: Option<Instant>,
    /// client_order_id (owner-tagged) → (symbol, exchange order_id) for orders
    /// this engine has placed, so strategies can cancel their own resting orders
    /// (e.g. swing's resting TP1 / hard stop) via `pending_cancels`.
    placed_orders: HashMap<String, (String, String)>,
}

impl Engine {
    pub fn new(
        config: AppConfig,
        connector: Arc<dyn Connector>,
        risk: RiskManager,
        telegram: TelegramBot,
        bar_cache: BarCache,
        status_cache: StrategyStatusCache,
        regime_cache: RegimeCache,
        routing_cache: RoutingCache,
        capital: CapitalManager,
    ) -> Self {
        let mut engine = Self {
            config,
            connector,
            strategies: Vec::new(),
            risk,
            telegram: telegram.clone_for_signal(),
            signal: None, // initialized below
            bar_buffers: bar_cache,
            order_books: HashMap::new(),
            status_cache,
            regime_cache,
            routing_cache,
            capital,
            last_risk_save: None,
            placed_orders: HashMap::new(),
        };
        // Init signal engine after self.config is set
        engine.signal = engine.config.signal.as_ref().filter(|s| s.enabled).map(|sc| {
            SignalEngine::new(sc, Some(telegram.clone_for_signal()))
        });
        engine
    }

    pub fn add_strategy(&mut self, strategy: Box<dyn Strategy>) {
        info!("Added strategy: {} on {}", strategy.name(), strategy.trading_pair());
        self.strategies.push(strategy);
    }

    /// Run the main trading loop
    pub async fn run(&mut self) -> Result<()> {
        // Startup notification
        let engines = format!(
            "Grid/Trend/MR | Swing {} | Signal {}",
            self.config.swing.as_ref()
                .filter(|s| s.enabled)
                .map(|s| format!("\u{2713} ({})", s.enabled_pairs.join(",")))
                .unwrap_or_else(|| "\u{2717}".into()),
            self.config.signal.as_ref()
                .filter(|s| s.enabled)
                .map(|_| "\u{2713}".to_string())
                .unwrap_or_else(|| "\u{2717}".into()),
        );
        self.telegram.send(
            &self.telegram.format_startup_message(
                if self.config.exchange.testnet { "testnet" } else { "production" },
                &self.config.pairs.iter().filter(|(_, pc)| pc.enabled).map(|(s, _)| s.clone()).collect::<Vec<_>>().join(", "),
                &engines,
            )
        ).await?;

        // Initialize strategies
        let mut all_orders = Vec::new();
        for (i, strategy) in self.strategies.iter_mut().enumerate() {
            match strategy.on_start().await {
                Ok(orders) => all_orders.extend(Self::tag_owner(i, orders)),
                Err(e) => error!("Strategy {} start failed: {}", strategy.name(), e),
            }
        }
        self.submit_orders(all_orders).await?;

        // Connect to WebSocket (multi-pair) — only enabled pairs
        let pairs: Vec<String> = self.config.pairs.iter()
            .filter(|(_, pc)| pc.enabled)
            .map(|(s, _)| s.clone())
            .collect();
        let ws = BinanceWs::new(self.config.exchange.testnet);
        let mut ws_rx = ws.subscribe_multi(&pairs, &self.config.timeframe).await?;

        info!("Engine running — processing events for {} pairs", pairs.len());

        // Preload historical bars so strategies can evaluate state immediately
        // First try loading from persisted file (instant warmup)
        let loaded_from_disk = self.load_bar_buffers().await;
        for pair in &pairs {
            if !loaded_from_disk.contains(pair) {
                let symbol = pair.replace("-", "");
                match self.connector.get_klines(&symbol, &self.config.timeframe, 100).await {
                    Ok(bars) => {
                        let count = bars.len();
                        self.bar_buffers.set(pair.clone(), bars).await;
                        info!("Preloaded {} historical bars for {}", count, pair);
                    }
                    Err(e) => warn!("Failed to preload bars for {}: {}", pair, e),
                }
            }
        }

        // Replay loaded bars through strategies to restore indicator state
        self.replay_bars_to_strategies().await;

        // Load regime cache from file (fallback for when Python hasn't pushed yet)
        self.regime_cache.load_from_file().await;
        info!("Regime cache loaded from file");

        // Load routing cache from file (fallback for when the PPO router hasn't
        // pushed yet — same cold-start shape as the regime cache above).
        self.routing_cache.load_from_file().await;
        info!("Routing cache loaded from file");

        // Restore circuit-breaker state (peak equity, daily baseline, halt) across restarts.
        let risk_path = std::env::var("RISK_STATE_PATH").unwrap_or_else(|_| "data/risk_state.json".to_string());
        let boot_balances = self.connector.get_balances().await.unwrap_or_default();
        let boot_equity = Self::portfolio_equity_mtm(&boot_balances, &self.order_books);
        crate::risk::load_state(&mut self.risk.circuit_breaker, &risk_path, boot_equity);
        info!("Circuit breaker loaded: peak={:.0} sod={:.0} halted={}",
            self.risk.circuit_breaker.peak_equity(),
            self.risk.circuit_breaker.start_of_day_equity(),
            self.risk.circuit_breaker.is_halted_raw());

        // Main event loop
        while let Some(event) = ws_rx.recv().await {
            match event {
                WsEvent::OrderBookUpdate { symbol, bids, asks } => {
                    // Normalize WebSocket symbol ("XRPUSDT") to config pair key ("XRP-USDT")
                    let pair_key = self.find_pair_for_symbol(&symbol)
                        .unwrap_or_else(|| symbol.clone());
                    self.order_books.insert(pair_key.clone(), OrderBook {
                        symbol: pair_key,
                        bids,
                        asks,
                        timestamp: chrono::Utc::now().timestamp_millis(),
                    });
                    self.tick_strategies().await?;
                    self.process_paper_fills().await?;
                    self.feed_breaker().await;
                }
                WsEvent::Kline { symbol, bar, is_closed } => {
                    if is_closed {
                        // WebSocket uses "DOGEUSDT" format; config uses "DOGE-USDT".
                        let pair_key = self.find_pair_for_symbol(&symbol)
                            .unwrap_or(symbol);
                        self.bar_buffers.push_closed_bar(pair_key, bar).await;
                        // Persist bar buffers every closed candle
                        self.save_bar_buffers().await;
                    }
                }
                WsEvent::Trade { symbol, price, .. } => {
                    let _ = (symbol, price);
                }
                _ => {}
            }

            // Signal engine: manage positions on every tick
            if let Some(ref signal) = self.signal {
                signal.manage_positions(&*self.connector).await;
            }
        }

        warn!("WebSocket event stream ended");
        Ok(())
    }

    async fn tick_strategies(&mut self) -> Result<()> {
        self.capital.reset_tick_grants(); // fresh per-tick allocation budget

        // Apply the current PPO routing decision (paper-gate). Read once per
        // tick; None (no entry yet, or stale per TTL) ⇒ leave strategies as-is
        // so a cold start or router outage doesn't accidentally pause everything.
        if let Some(r) = self.routing_cache.get().await {
            for s in self.strategies.iter_mut() {
                let is_active = s.name() == r.active_engine;
                s.set_paused(!is_active);
                if r.flat {
                    s.force_flat();
                }
            }
            // PPO size signal: scale the active engine's capital grant ceiling
            // (0.5 / 1.0 / 1.5). Paused engines don't request capital, so we only
            // push the mult for the active engine.
            self.capital.set_size_mult(&r.active_engine, r.size_mult);
        }

        let mut all_orders = Vec::new();
        let mut all_cancels: Vec<(usize, String)> = Vec::new();
        for (i, strategy) in self.strategies.iter_mut().enumerate() {
            let pair = strategy.trading_pair().to_string();
            let order_book = self.order_books.get(&pair).cloned().unwrap_or(OrderBook {
                symbol: pair.clone(),
                bids: Vec::new(),
                asks: Vec::new(),
                timestamp: 0,
            });

            let balances = self.connector.get_balances().await.unwrap_or_default();

            let (regime, regime_confidence) = self.regime_cache.get(&pair).await
                .map(|(r, c)| {
                    let regime = match r {
                        0 => MarketRegime::Ranging,
                        1 => MarketRegime::Trending,
                        _ => MarketRegime::Danger,
                    };
                    (Some(regime), c)
                })
                .unwrap_or((None, 0.0));

            let ctx = TickContext {
                order_book,
                recent_bars: self.bar_buffers.get(&pair, 1500).await,
                balances,
                open_orders: Vec::new(),
                regime,
                regime_confidence,
                timestamp: chrono::Utc::now().timestamp_millis(),
                capital: Some(self.capital.clone()),
                replay: false,
            };

            match strategy.on_tick(&ctx).await {
                Ok(orders) => all_orders.extend(Self::tag_owner(i, orders)),
                Err(e) => warn!("Strategy {} tick error: {}", strategy.name(), e),
            }
            for cid in strategy.pending_cancels() {
                all_cancels.push((i, cid));
            }
        }
        self.submit_orders(all_orders).await?;
        // Process cancels AFTER placing new orders, so a stop replacement keeps a
        // protective order live at all times (place runner stop, then cancel old).
        self.process_cancels(all_cancels).await;

        // Update shared status cache for API access
        let statuses: Vec<_> = self.strategies.iter().map(|s| s.status()).collect();
        self.status_cache.update(statuses).await;

        Ok(())
    }

    /// Tag each order with the owning strategy index (encoded in client_order_id)
    /// so a paper fill can be routed back to the strategy that placed the order.
    /// Any client_order_id the strategy already set is preserved after the tag.
    fn tag_owner(idx: usize, mut orders: Vec<OrderRequest>) -> Vec<OrderRequest> {
        for o in &mut orders {
            let existing = o.client_order_id.take().unwrap_or_default();
            o.client_order_id = Some(format!("owner:{}#{}", idx, existing));
        }
        orders
    }

    /// Recover the owning strategy index from a fill's client_order_id.
    fn owner_index(fill: &Fill) -> Option<usize> {
        let cid = fill.client_order_id.as_deref()?;
        let rest = cid.strip_prefix("owner:")?;
        rest.split('#').next()?.parse().ok()
    }

    /// Mark-to-market portfolio equity: USDT balance + Σ (base × mid) across
    /// order books. A grid buy (cash → inventory at the same price) is net-zero
    /// here, so it doesn't falsely look like a drawdown the way realized-PnL
    /// (cash-flow) accounting would.
    pub fn portfolio_equity_mtm(
        balances: &HashMap<String, f64>,
        order_books: &HashMap<String, OrderBook>,
    ) -> f64 {
        let mut equity = balances.get("USDT").copied().unwrap_or(0.0);
        for (pair, ob) in order_books {
            if let Some(mid) = ob.mid_price() {
                let base = pair.split('-').next().unwrap_or("");
                if !base.is_empty() {
                    equity += balances.get(base).copied().unwrap_or(0.0) * mid;
                }
            }
        }
        equity
    }

    /// Feed mark-to-market portfolio equity to the circuit breaker + persist.
    async fn feed_breaker(&mut self) {
        let balances = self.connector.get_balances().await.unwrap_or_default();
        let equity = Self::portfolio_equity_mtm(&balances, &self.order_books);
        let usdt = balances.get("USDT").copied().unwrap_or(0.0);
        // Capital accounting: equity + USDT + real per-strategy deployed capital.
        self.capital.sync_equity(equity, usdt);
        let mut deployed = std::collections::BTreeMap::new();
        for s in self.strategies.iter() {
            *deployed.entry(s.name().to_string()).or_insert(0.0) += s.deployed_capital();
        }
        self.capital.set_deployed(deployed);
        let was_halted = self.risk.circuit_breaker.is_halted_raw();
        self.risk.record_equity(equity);
        // Daily reset at UTC midnight.
        let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
        if self.risk.circuit_breaker.last_reset_date() != today {
            self.risk.circuit_breaker.set_start_of_day_equity(equity);
            self.risk.circuit_breaker.set_last_reset_date(today);
        }
        // Persist risk state at most every 5s, or immediately when the halt flag
        // changes. feed_breaker runs on every orderbook update (dozens/sec across
        // pairs); writing risk_state.json (fs::write + rename) on every tick starves
        // the Tokio worker with synchronous disk I/O. (#5 of the concurrency audit.)
        let path = std::env::var("RISK_STATE_PATH").unwrap_or_else(|_| "data/risk_state.json".to_string());
        let now = Instant::now();
        let halt_changed = was_halted != self.risk.circuit_breaker.is_halted_raw();
        let due = self.last_risk_save.map_or(true, |t| now.duration_since(t) >= Duration::from_secs(5));
        if halt_changed || due {
            crate::risk::save_state(&self.risk.circuit_breaker, &path);
            self.last_risk_save = Some(now);
        }
    }

    async fn submit_orders(&mut self, orders: Vec<OrderRequest>) -> Result<()> {
        for req in orders {
            // Reduce-only orders (exits) bypass the halt check so a circuit
            // breaker trip can't trap open positions.
            if !req.reduce_only {
                if let Err(e) = self.risk.check_trading_allowed() {
                    warn!("Order vetoed by risk manager (halted): {}", e);
                    continue;
                }
            }

            match self.connector.place_order(&req).await {
                Ok(resp) => {
                    info!("Order placed: {} {} {} @ {}",
                        resp.order_id, resp.symbol, resp.quantity, resp.price);
                    // Track so the owning strategy can cancel it later by client-id.
                    if let Some(cid) = &resp.client_order_id {
                        self.placed_orders.insert(
                            cid.clone(),
                            (resp.symbol.clone(), resp.order_id.clone()),
                        );
                    }
                }
                Err(e) => error!("Order failed: {}", e),
            }
        }
        Ok(())
    }

    /// Cancel resting orders strategies asked to cancel. Each entry is
    /// (strategy_index, client_id_as_strategy_set_it); the engine reconstructs
    /// the owner-tagged id it recorded at placement time.
    async fn process_cancels(&mut self, cancels: Vec<(usize, String)>) {
        for (idx, cid) in cancels {
            let tagged = format!("owner:{}#{}", idx, cid);
            if let Some((symbol, order_id)) = self.placed_orders.remove(&tagged) {
                if let Err(e) = self.connector.cancel_order(&symbol, &order_id).await {
                    warn!("Cancel failed for {} ({}): {}", symbol, cid, e);
                }
            }
        }
    }

    /// Save bar buffers to disk for warm startup after restart
    async fn save_bar_buffers(&self) {
        let snap = self.bar_buffers.snapshot().await;
        let path = std::path::PathBuf::from("data/bar_buffers.json");
        let _ = std::fs::create_dir_all("data");
        if let Ok(json) = serde_json::to_string_pretty(&snap) {
            if let Err(e) = std::fs::write(&path, json) {
                warn!("Failed to save bar buffers: {}", e);
            }
        }
    }

    /// Load bar buffers from disk. Returns set of pairs that were loaded.
    async fn load_bar_buffers(&self) -> std::collections::HashSet<String> {
        let path = std::path::PathBuf::from("data/bar_buffers.json");
        if !path.exists() { return std::collections::HashSet::new(); }

        let mut loaded = std::collections::HashSet::new();
        match std::fs::read_to_string(&path) {
            Ok(content) => {
                match serde_json::from_str::<std::collections::HashMap<String, Vec<Bar>>>(&content) {
                    Ok(buffers) => {
                        let count = buffers.len();
                        for (pair, bars) in &buffers {
                            let n = bars.len();
                            info!("Loaded {} cached bars for {}", n, pair);
                            loaded.insert(pair.clone());
                        }
                        // Use bulk_load which deduplicates no-dash/with-dash entries
                        self.bar_buffers.bulk_load(buffers).await;
                        info!("Bar buffers restored from disk ({} pairs)", count);
                    }
                    Err(e) => warn!("Failed to parse bar buffers: {}", e),
                }
            }
            Err(e) => warn!("Failed to read bar buffers: {}", e),
        }
        loaded
    }

    /// Replay loaded bars through strategies to restore indicator state
    async fn replay_bars_to_strategies(&mut self) {
        if self.bar_buffers.is_empty().await { return; }

        for strategy in &mut self.strategies {
            let pair = strategy.trading_pair().to_string();
            let bars = self.bar_buffers.get(&pair, 500).await;
            if !bars.is_empty() {
                if bars.len() >= 10 {
                    info!("Replaying {} bars to warm up {} on {}", bars.len(), strategy.name(), pair);
                    let balances = std::collections::HashMap::new();
                    // Feed bars with growing window so indicators can compute
                    // (strategies need 20+ bars to evaluate RSI/BB)
                    for (i, bar) in bars.iter().enumerate() {
                        let window_end = i + 1;
                        let window_start = window_end.saturating_sub(200);
                        let window = bars[window_start..window_end].to_vec();
                        let ctx = TickContext {
                            order_book: OrderBook {
                                symbol: pair.clone(),
                                bids: vec![],
                                asks: vec![],
                                timestamp: bar.timestamp,
                            },
                            recent_bars: window,
                            balances: balances.clone(),
                            open_orders: vec![],
                            regime: None, // No regime during warmup replay
                            regime_confidence: 0.0,
                            timestamp: bar.timestamp,
                            capital: None,
                            replay: true,
                        };
                        let _ = strategy.on_tick(&ctx).await;
                    }
                    info!("{} on {} warmed up from cached bars", strategy.name(), pair);
                }
            }
        }
    }

    /// For paper trading: attempt to fill open orders at current mid-price,
    /// then dispatch fills to strategies via on_fill().
    async fn process_paper_fills(&mut self) -> Result<()> {
        // Collect fills from all orderbooks
        let mut fills_by_pair: Vec<(String, Fill)> = Vec::new();
        for (symbol, ob) in &self.order_books {
            if let Some(mid_price) = ob.mid_price() {
                let pair_fills = self.connector.try_fill_at_price(symbol, mid_price).await;
                for fill in pair_fills {
                    fills_by_pair.push((symbol.clone(), fill));
                }
            }
        }

        if fills_by_pair.is_empty() {
            return Ok(());
        }

        for (_ob_symbol, fill) in &fills_by_pair {
            info!("Paper fill: {} {} {} @ ${:.2}",
                fill.side, fill.quantity, fill.symbol, fill.price);
        }

        // Dispatch each fill to the strategy that PLACED the order (encoded in
        // client_order_id). Previously routing matched by symbol, which delivered
        // a grid fill to the trend/mean-reversion strategies on the same pair and
        // corrupted their state. Fall back to symbol routing only for legacy
        // fills that carry no owner tag.
        let mut all_orders = Vec::new();
        let mut all_cancels: Vec<(usize, String)> = Vec::new();
        for (_ob_symbol, fill) in &fills_by_pair {
            // A resting order that filled is consumed — drop it from the cancel map.
            if let Some(cid) = fill.client_order_id.as_deref() {
                self.placed_orders.remove(cid);
            }
            match Self::owner_index(fill) {
                Some(idx) => {
                    if let Some(strategy) = self.strategies.get_mut(idx) {
                        match strategy.on_fill(fill).await {
                            Ok(orders) => all_orders.extend(Self::tag_owner(idx, orders)),
                            Err(e) => warn!("Strategy {} fill error: {}", strategy.name(), e),
                        }
                        for cid in strategy.pending_cancels() { all_cancels.push((idx, cid)); }
                    } else {
                        warn!("Fill owner index {} out of range for {}", idx, fill.symbol);
                    }
                }
                None => {
                    let fill_norm = fill.symbol.replace("-", "");
                    for (i, strategy) in self.strategies.iter_mut().enumerate() {
                        let strategy_norm = strategy.trading_pair().replace("-", "");
                        if strategy_norm == fill_norm {
                            match strategy.on_fill(fill).await {
                                Ok(orders) => all_orders.extend(Self::tag_owner(i, orders)),
                                Err(e) => warn!("Strategy {} fill error: {}", strategy.name(), e),
                            }
                            for cid in strategy.pending_cancels() { all_cancels.push((i, cid)); }
                        }
                    }
                }
            }
        }
        self.submit_orders(all_orders).await?;
        self.process_cancels(all_cancels).await;

        Ok(())
    }

    /// Given a WebSocket symbol like "DOGEUSDT", find the config pair key "DOGE-USDT".
    fn find_pair_for_symbol(&self, ws_symbol: &str) -> Option<String> {
        let ws_upper = ws_symbol.to_uppercase();
        for (pair_key, _) in self.config.pairs.iter() {
            if pair_key.replace('-', "").to_uppercase() == ws_upper {
                return Some(pair_key.clone());
            }
        }
        None
    }

}

#[cfg(test)]
mod tests {
    //! Engine-level tests for the routing gate (Task 4). These live inside the
    //! engine module so they can build an `Engine` via struct literal — bypassing
    //! `Engine::new`'s 9-argument signature — and exercise `tick_strategies`
    //! directly with a controlled `RoutingCache` and mock strategies.

    use super::*;
    use async_trait::async_trait;
    use std::sync::{Arc, Mutex};
    use crate::strategy::StrategyStatus;
    use crate::models::order::OrderSide;
    use crate::connector::types::OrderStatus;

    /// Records `set_paused` / `force_flat` calls so the test can assert on them
    /// after the strategies have been moved into the Engine.
    #[derive(Clone, Default)]
    struct CallLog {
        paused: Option<bool>,
        flat_called: bool,
    }

    /// Minimal Strategy impl for routing-gate tests. Records pause + flat calls
    /// into a shared `Arc<Mutex<CallLog>>` so the test can read them back, and
    /// also echoes them through `status()` so the engine's status-cache path
    /// (exercised at the end of tick_strategies) has something to consume.
    struct MockStrategy {
        name: String,
        pair: String,
        log: Arc<Mutex<CallLog>>,
    }

    impl MockStrategy {
        fn new(name: &str, log: Arc<Mutex<CallLog>>) -> Self {
            Self {
                name: name.into(),
                pair: "TESTUSDT".into(),
                log,
            }
        }
    }

    #[async_trait]
    impl Strategy for MockStrategy {
        fn name(&self) -> &str { &self.name }
        fn trading_pair(&self) -> &str { &self.pair }

        async fn on_tick(&mut self, _ctx: &TickContext) -> Result<Vec<OrderRequest>> {
            Ok(vec![])
        }
        async fn on_fill(&mut self, _fill: &Fill) -> Result<Vec<OrderRequest>> {
            Ok(vec![])
        }
        async fn on_start(&mut self) -> Result<Vec<OrderRequest>> { Ok(vec![]) }
        async fn on_stop(&mut self) -> Result<()> { Ok(()) }

        fn set_paused(&mut self, paused: bool) {
            self.log.lock().unwrap().paused = Some(paused);
        }
        fn force_flat(&mut self) {
            self.log.lock().unwrap().flat_called = true;
        }

        fn status(&self) -> StrategyStatus {
            let log = self.log.lock().unwrap();
            let state = match log.paused {
                Some(true) => "Paused",
                Some(false) => "Active",
                None => "Idle",
            };
            StrategyStatus {
                name: self.name.clone(),
                pair: self.pair.clone(),
                state: state.into(),
                pnl: 0.0,
                open_orders: 0,
                details: if log.flat_called { "force_flat called".into() } else { String::new() },
            }
        }
    }

    /// Mock Connector that returns empty results for everything. tick_strategies
    /// only calls `get_balances` on it; the rest are required by the trait.
    struct NullConnector;
    #[async_trait]
    impl Connector for NullConnector {
        async fn place_order(&self, _req: &OrderRequest) -> Result<OrderResponse> {
            Ok(OrderResponse {
                order_id: String::new(),
                client_order_id: None,
                symbol: String::new(),
                side: OrderSide::Buy,
                price: 0.0,
                quantity: 0.0,
                status: OrderStatus::New,
            })
        }
        async fn cancel_order(&self, _symbol: &str, _order_id: &str) -> Result<()> { Ok(()) }
        async fn cancel_all_orders(&self, _symbol: &str) -> Result<Vec<CancelResult>> { Ok(vec![]) }
        async fn get_balances(&self) -> Result<HashMap<String, f64>> { Ok(HashMap::new()) }
        async fn get_open_orders(&self, _symbol: &str) -> Result<Vec<OpenOrder>> { Ok(vec![]) }
        async fn get_order_book(&self, _symbol: &str, _limit: u16) -> Result<OrderBook> {
            Ok(OrderBook { symbol: String::new(), bids: vec![], asks: vec![], timestamp: 0 })
        }
        async fn get_klines(&self, _symbol: &str, _interval: &str, _limit: u16)
            -> Result<Vec<crate::models::bar::Bar>> { Ok(vec![]) }
    }

    /// Build a minimal Engine via struct literal. tick_strategies touches
    /// `capital`, `connector`, `order_books`, `regime_cache`, `routing_cache`,
    /// `status_cache`, and `strategies` — the rest are seeded empty/default.
    fn minimal_engine(
        strategies: Vec<Box<dyn Strategy>>,
        routing_cache: RoutingCache,
    ) -> Engine {
        let path = format!(
            "{}/../config/strategy.yaml",
            env!("CARGO_MANIFEST_DIR")
        );
        let config = AppConfig::load(&path).expect("strategy.yaml must load");
        Engine {
            config,
            connector: Arc::new(NullConnector),
            strategies,
            risk: RiskManager::new(
                crate::risk::PositionGuard::new(100.0, 10.0, 10_000.0),
                crate::risk::CircuitBreaker::new(50.0, 10.0),
            ),
            telegram: TelegramBot::new("dummy_token", "dummy_chat"),
            signal: None,
            bar_buffers: BarCache::new(),
            order_books: HashMap::new(),
            status_cache: StrategyStatusCache::new(),
            regime_cache: RegimeCache::new("/tmp/test_engine_regime.json", 0),
            routing_cache,
            capital: CapitalManager::new(20.0),
            last_risk_save: None,
            placed_orders: HashMap::new(),
        }
    }

    #[tokio::test]
    async fn test_routing_pauses_non_active_strategies() {
        // Two strategies sharing the same pair so both look up the same (empty)
        // order book entry in tick_strategies. Each writes to its own CallLog.
        let grid_log = Arc::new(Mutex::new(CallLog::default()));
        let trend_log = Arc::new(Mutex::new(CallLog::default()));

        let routing = RoutingCache::new("/tmp/test_engine_routing.json", 0);
        routing.update(crate::strategy::routing_cache::RoutingUpdate {
            active_engine: "grid".into(),
            size_mult: 1.0,
            flat: false,
        }).await;

        let mut engine = minimal_engine(
            vec![
                Box::new(MockStrategy::new("grid", grid_log.clone())),
                Box::new(MockStrategy::new("trend", trend_log.clone())),
            ],
            routing,
        );

        engine.tick_strategies().await.expect("tick must complete");

        assert_eq!(grid_log.lock().unwrap().paused, Some(false),
            "active engine (grid) must be unpaused");
        assert_eq!(trend_log.lock().unwrap().paused, Some(true),
            "non-active engine (trend) must be paused");
        // flat=false ⇒ neither should have force_flat called.
        assert!(!grid_log.lock().unwrap().flat_called);
        assert!(!trend_log.lock().unwrap().flat_called);
    }

    #[tokio::test]
    async fn test_routing_flat_calls_force_flat_on_all_strategies() {
        let grid_log = Arc::new(Mutex::new(CallLog::default()));
        let trend_log = Arc::new(Mutex::new(CallLog::default()));

        let routing = RoutingCache::new("/tmp/test_engine_routing_flat.json", 0);
        routing.update(crate::strategy::routing_cache::RoutingUpdate {
            active_engine: "grid".into(),
            size_mult: 1.0,
            flat: true,
        }).await;

        let mut engine = minimal_engine(
            vec![
                Box::new(MockStrategy::new("grid", grid_log.clone())),
                Box::new(MockStrategy::new("trend", trend_log.clone())),
            ],
            routing,
        );

        engine.tick_strategies().await.expect("tick must complete");

        // Even the active engine must be force_flat'd when flat=true.
        assert!(grid_log.lock().unwrap().flat_called,
            "active engine must still be force_flat'd when flat=true");
        assert!(trend_log.lock().unwrap().flat_called,
            "non-active engine must be force_flat'd when flat=true");
    }

    #[tokio::test]
    async fn test_no_routing_decision_leaves_strategies_unchanged() {
        // No routing push ⇒ no set_paused call, no force_flat call. This guards
        // against the gate accidentally pausing everything when the router is
        // offline (e.g. on cold start before Python has pushed anything).
        let grid_log = Arc::new(Mutex::new(CallLog::default()));
        let trend_log = Arc::new(Mutex::new(CallLog::default()));

        let routing = RoutingCache::new("/tmp/test_engine_routing_empty.json", 0);
        // Deliberately no update() — cache returns None.

        let mut engine = minimal_engine(
            vec![
                Box::new(MockStrategy::new("grid", grid_log.clone())),
                Box::new(MockStrategy::new("trend", trend_log.clone())),
            ],
            routing,
        );

        engine.tick_strategies().await.expect("tick must complete");

        assert_eq!(grid_log.lock().unwrap().paused, None,
            "no routing ⇒ set_paused must not be called");
        assert_eq!(trend_log.lock().unwrap().paused, None);
        assert!(!grid_log.lock().unwrap().flat_called);
        assert!(!trend_log.lock().unwrap().flat_called);
    }

    /// Task 6 boot test: when `data/routing_cache.json` exists at startup, the
    /// engine's `routing_cache` field must load it (mirrors the regime cache's
    /// boot load). This proves the file-fallback path works through the Engine
    /// — the production wire-up is `Engine::run()` calling
    /// `self.routing_cache.load_from_file().await` right after the regime load.
    #[tokio::test]
    async fn test_engine_loads_routing_from_file() {
        let path = "/tmp/test_engine_routing_boot.json";
        let entry = crate::strategy::routing_cache::RoutingEntry {
            active_engine: "trend".into(),
            size_mult: 1.0,
            flat: false,
            timestamp: chrono::Utc::now().timestamp_millis(),
        };
        std::fs::write(path, serde_json::to_string_pretty(&entry).unwrap())
            .expect("write routing_cache.json fixture");

        // Build an Engine whose routing_cache points at the fixture file. The
        // minimal_engine helper seeds an empty strategy vec — we don't tick.
        let routing = RoutingCache::new(path, 0);
        let engine = minimal_engine(vec![], routing);

        // Mirror what Engine::run() does at boot (right after regime_cache load).
        engine.routing_cache.load_from_file().await;

        let loaded = engine.routing_cache.get().await
            .expect("routing entry must be loaded from file");
        assert_eq!(loaded.active_engine, "trend");
        assert_eq!(loaded.size_mult, 1.0);
        assert!(!loaded.flat);

        let _ = std::fs::remove_file(path);
    }
}
