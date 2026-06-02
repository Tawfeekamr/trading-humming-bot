use anyhow::Result;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;
use tracing::{info, warn, error};
use crate::config::AppConfig;
use crate::connector::Connector;
use crate::connector::binance_ws::{BinanceWs, WsEvent};
use crate::connector::types::*;
use crate::risk::RiskManager;
use crate::notifications::TelegramBot;
use crate::strategy::{Strategy, TickContext};
use crate::strategy::status_cache::StrategyStatusCache;
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
    started_at: Instant,
    status_cache: StrategyStatusCache,
}

impl Engine {
    pub fn new(
        config: AppConfig,
        connector: Arc<dyn Connector>,
        risk: RiskManager,
        telegram: TelegramBot,
        bar_cache: BarCache,
        status_cache: StrategyStatusCache,
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
            started_at: Instant::now(),
            status_cache,
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
        self.telegram.send(
            &self.telegram.format_startup_message(
                if self.config.exchange.testnet { "testnet" } else { "production" },
                self.config.grid.capital_usdt,
                &self.config.pairs.iter().filter(|(_, pc)| pc.enabled).map(|(s, _)| s.clone()).collect::<Vec<_>>().join(", "),
                self.config.grid.levels as usize,
            )
        ).await?;

        // Initialize strategies
        let mut all_orders = Vec::new();
        for strategy in &mut self.strategies {
            match strategy.on_start().await {
                Ok(mut orders) => all_orders.append(&mut orders),
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

        // Main event loop
        while let Some(event) = ws_rx.recv().await {
            match event {
                WsEvent::OrderBookUpdate { symbol, bids, asks } => {
                    self.order_books.insert(symbol.clone(), OrderBook {
                        symbol: symbol.clone(),
                        bids,
                        asks,
                        timestamp: chrono::Utc::now().timestamp_millis(),
                    });
                    self.tick_strategies().await?;
                }
                WsEvent::Kline { symbol, bar, is_closed } => {
                    if is_closed {
                        self.bar_buffers.push_closed_bar(symbol, bar).await;
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
        let mut all_orders = Vec::new();
        for strategy in &mut self.strategies {
            let pair = strategy.trading_pair().to_string();
            let order_book = self.order_books.get(&pair).cloned().unwrap_or(OrderBook {
                symbol: pair.clone(),
                bids: Vec::new(),
                asks: Vec::new(),
                timestamp: 0,
            });

            let balances = self.connector.get_balances().await.unwrap_or_default();

            let ctx = TickContext {
                order_book,
                recent_bars: self.bar_buffers.get_exact(&pair).await.unwrap_or_default(),
                balances,
                open_orders: Vec::new(),
                regime: None,
                timestamp: chrono::Utc::now().timestamp_millis(),
            };

            match strategy.on_tick(&ctx).await {
                Ok(mut orders) => all_orders.append(&mut orders),
                Err(e) => warn!("Strategy {} tick error: {}", strategy.name(), e),
            }
        }
        self.submit_orders(all_orders).await?;

        // Update shared status cache for API access
        let statuses: Vec<_> = self.strategies.iter().map(|s| s.status()).collect();
        self.status_cache.update(statuses).await;

        Ok(())
    }

    async fn submit_orders(&self, orders: Vec<OrderRequest>) -> Result<()> {
        for req in orders {
            if let Err(e) = self.risk.check_trading_allowed() {
                warn!("Order vetoed by risk manager: {}", e);
                continue;
            }

            match self.connector.place_order(&req).await {
                Ok(resp) => info!("Order placed: {} {} {} @ {}",
                    resp.order_id, resp.symbol, resp.quantity, resp.price),
                Err(e) => error!("Order failed: {}", e),
            }
        }
        Ok(())
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
                        for (pair, bars) in buffers {
                            let n = bars.len();
                            info!("Loaded {} cached bars for {}", n, pair);
                            loaded.insert(pair.clone());
                            self.bar_buffers.set(pair, bars).await;
                        }
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
            if let Some(bars) = self.bar_buffers.get_exact(&pair).await {
                if bars.len() >= 10 {
                    info!("Replaying {} bars to warm up {} on {}", bars.len(), strategy.name(), pair);
                    let balances = std::collections::HashMap::new();
                    // Feed bars as tick context to restore indicator state
                    for bar in bars.iter() {
                        let ctx = TickContext {
                            order_book: OrderBook {
                                symbol: pair.clone(),
                                bids: vec![],
                                asks: vec![],
                                timestamp: bar.timestamp,
                            },
                            recent_bars: vec![bar.clone()],
                            balances: balances.clone(),
                            open_orders: vec![],
                            regime: None,
                            timestamp: bar.timestamp,
                        };
                        let _ = strategy.on_tick(&ctx).await;
                    }
                    info!("{} on {} warmed up from cached bars", strategy.name(), pair);
                }
            }
        }
    }

    /// Rough equity estimate from order book mid-price × USDT balance
    fn estimate_equity(&self) -> f64 {
        // Sum of configured capital per pair as baseline equity estimate
        // Each pair gets an equal share of the total grid capital
        let pair_count = self.config.pairs.0.len().max(1) as f64;
        let per_pair_capital = self.config.grid.capital_usdt / pair_count;
        let mut total = 0.0;
        for _ in self.config.pairs.0.iter() {
            total += per_pair_capital;
        }
        if total > 0.0 { total } else { self.config.grid.capital_usdt }
    }
}
