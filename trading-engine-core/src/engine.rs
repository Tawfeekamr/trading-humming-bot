use anyhow::Result;
use std::collections::HashMap;
use tracing::{info, warn, error};
use crate::config::AppConfig;
use crate::connector::Connector;
use crate::connector::binance_ws::{BinanceWs, WsEvent};
use crate::connector::types::*;
use crate::risk::RiskManager;
use crate::notifications::TelegramBot;
use crate::strategy::{Strategy, TickContext};
use crate::models::bar::Bar;

pub struct Engine {
    config: AppConfig,
    connector: Box<dyn Connector>,
    strategies: Vec<Box<dyn Strategy>>,
    risk: RiskManager,
    telegram: TelegramBot,
    bar_buffers: HashMap<String, Vec<Bar>>,
    order_books: HashMap<String, OrderBook>,
}

impl Engine {
    pub fn new(
        config: AppConfig,
        connector: Box<dyn Connector>,
        risk: RiskManager,
        telegram: TelegramBot,
    ) -> Self {
        Self {
            config,
            connector,
            strategies: Vec::new(),
            risk,
            telegram,
            bar_buffers: HashMap::new(),
            order_books: HashMap::new(),
        }
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
                &self.config.pairs.keys().cloned().collect::<Vec<_>>().join(", "),
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

        // Connect to WebSocket
        let pair = self.strategies.first()
            .map(|s| s.trading_pair().to_string())
            .unwrap_or("BTCUSDT".to_string());

        let ws = BinanceWs::new(self.config.exchange.testnet);
        let mut ws_rx = ws.subscribe(&pair, "1m").await?;

        info!("Engine running — processing events for {}", pair);

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
                        self.bar_buffers.entry(symbol.clone()).or_default().push(bar);
                        if let Some(bars) = self.bar_buffers.get_mut(&symbol) {
                            if bars.len() > 500 {
                                bars.drain(0..bars.len() - 500);
                            }
                        }
                    }
                }
                WsEvent::Trade { symbol, price, .. } => {
                    let _ = (symbol, price);
                }
                _ => {}
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
                recent_bars: self.bar_buffers.get(&pair).cloned().unwrap_or_default(),
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
}
