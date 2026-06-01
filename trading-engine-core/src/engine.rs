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
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use crate::signal::SignalEngine;

pub struct Engine {
    config: AppConfig,
    connector: Arc<dyn Connector>,
    strategies: Vec<Box<dyn Strategy>>,
    risk: RiskManager,
    telegram: TelegramBot,
    signal: Option<SignalEngine>,
    bar_buffers: HashMap<String, Vec<Bar>>,
    order_books: HashMap<String, OrderBook>,
    started_at: Instant,
    last_update_id: i64,
    last_telegram_poll: Instant,
}

impl Engine {
    pub fn new(
        config: AppConfig,
        connector: Arc<dyn Connector>,
        risk: RiskManager,
        telegram: TelegramBot,
    ) -> Self {
        let mut engine = Self {
            config,
            connector,
            strategies: Vec::new(),
            risk,
            telegram: telegram.clone_for_signal(),
            signal: None, // initialized below
            bar_buffers: HashMap::new(),
            order_books: HashMap::new(),
            started_at: Instant::now(),
            last_update_id: 0,
            last_telegram_poll: Instant::now(),
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
        for pair in &pairs {
            let symbol = pair.replace("-", "");
            match self.connector.get_klines(&symbol, &self.config.timeframe, 100).await {
                Ok(bars) => {
                    let count = bars.len();
                    self.bar_buffers.insert(pair.clone(), bars);
                    info!("Preloaded {} historical bars for {}", count, pair);
                }
                Err(e) => warn!("Failed to preload bars for {}: {}", pair, e),
            }
        }

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

            // Poll and dispatch Telegram commands (throttled to every 3 seconds)
            if self.last_telegram_poll.elapsed() >= std::time::Duration::from_secs(3) {
                self.last_telegram_poll = std::time::Instant::now();
                if let Err(e) = self.handle_telegram_commands().await {
                    warn!("Telegram command polling error: {}", e);
                }
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

    // ══════════════════════════════════════════════════════════════════
    // Telegram command handling
    // ══════════════════════════════════════════════════════════════════

    /// Poll Telegram for new commands and dispatch them
    async fn handle_telegram_commands(&mut self) -> Result<()> {
        let commands = self.telegram.poll_commands(&mut self.last_update_id).await?;
        for text in commands {
            let reply = self.dispatch_command(&text).await;
            if let Some(msg) = reply {
                if let Err(e) = self.telegram.send(&msg).await {
                    warn!("Failed to send Telegram reply: {}", e);
                }
            }
        }
        Ok(())
    }

    /// Parse a raw message and route to the right handler
    async fn dispatch_command(&mut self, text: &str) -> Option<String> {
        let cmd = text.strip_prefix('/')?;
        // Strip bot username suffix (e.g. "/status@mybot") and get first word
        let cmd = cmd.split('@').next()?;
        let cmd = cmd.split_whitespace().next()?.to_lowercase();

        match cmd.as_str() {
            "status" => Some(self.cmd_status().await),
            "system" | "server" => Some(self.cmd_system().await),
            "help" => Some(self.cmd_help()),
            "price" => self.cmd_price().await,
            "balance" => self.cmd_balance().await,
            "grid_status" => Some(self.cmd_grid_status()),
            "trend_status" => Some(self.cmd_trend_status()),
            "pnl" => Some(self.cmd_pnl()),
            "pending" => self.cmd_pending().await,
            "pause" => Some(self.cmd_pause()),
            "resume" => Some(self.cmd_resume()),
            "reset" => Some(self.cmd_reset()),
            // Signal commands
            "signal_status" => self.cmd_signal_status().await,
            "signal_prices" => self.cmd_signal_prices().await,
            "signal_pnl" => self.cmd_signal_pnl().await,
            "signal_pause" => self.cmd_signal_pause(),
            "signal_resume" => self.cmd_signal_resume(),
            "signal_close" => self.cmd_signal_close(text).await,
            _ => None,
        }
    }

    // ── System commands ──────────────────────────────────────────────

    async fn cmd_status(&self) -> String {
        let uptime = self.started_at.elapsed();
        let hours = uptime.as_secs() / 3600;
        let minutes = (uptime.as_secs() % 3600) / 60;
        let mode = if self.config.exchange.testnet { "TESTNET" } else { "PRODUCTION" };

        let mut lines = vec![
            format!("📊 <b>Daily Status</b> — {}", mode),
            "•••".to_string(),
            format!("⏱ Up: {}h {}m", hours, minutes),
        ];

        // Strategy states
        for s in &self.strategies {
            let st = s.status();
            let pnl_sign = if st.pnl >= 0.0 { "+" } else { "" };
            lines.push(format!(
                "{} <b>{}:</b> {} | P&L: {}${:.2} | Orders: {}",
                match st.name.as_str() {
                    "grid" => "🤖",
                    "trend" => "📈",
                    _ => "📊",
                },
                st.pair, st.state, pnl_sign, st.pnl, st.open_orders
            ));
        }

        // Circuit breaker
        let cb = if self.risk.circuit_breaker.is_halted() { "🛑 HALTED" } else { "✅ OK" };
        lines.push(format!("🛡️ CB: {}", cb));

        // Signal engine
        if let Some(ref signal) = self.signal {
            let sig_status = signal.get_status().await;
            let mode_tag = if sig_status.audit_mode { "AUDIT" } else { "LIVE" };
            lines.push(format!(
                "📡 <b>Signal ({}):</b> {} | Positions: {} | P&L: ${:.2} | Trades: {}/{}",
                mode_tag, sig_status.state, sig_status.open_positions,
                sig_status.daily_pnl, sig_status.trades_today, sig_status.max_trades
            ));
        } else {
            lines.push("📡 <b>Signal:</b> Disabled".to_string());
        }

        // System resources
        let sys = system_stats();
        lines.push("•••".to_string());
        lines.push(format!("💻 CPU: {:.0}% | RAM: {:.0}% | Disk: {:.0}%",
            sys.cpu_pct, sys.ram_pct, sys.disk_pct));

        lines.join("\n")
    }

    async fn cmd_system(&self) -> String {
        let mode = if self.config.exchange.testnet { "TESTNET" } else { "PRODUCTION" };
        let pairs: Vec<&String> = self.config.pairs.keys().collect();
        let sys = system_stats();

        let mut lines = vec![
            "🖥️ <b>System Status</b>".to_string(),
            "•••".to_string(),
            format!("⚙️ <b>Mode:</b> {}", mode),
            format!("💰 <b>Capital:</b> ${:.0}", self.config.grid.capital_usdt),
            format!("📊 <b>Pairs:</b> {}", pairs.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")),
            "•••".to_string(),
        ];

        // Strategy states
        lines.push("🤖 <b>Strategies</b>".to_string());
        for s in &self.strategies {
            let st = s.status();
            let emoji = if st.state.contains("Active") { "🟢" } else { "🔴" };
            lines.push(format!("  {} {}: {}", emoji, st.pair, st.state));
        }

        // Risk
        let cb = if self.risk.circuit_breaker.is_halted() { "🛑 HALTED" } else { "✅ OK" };
        lines.push("•••".to_string());
        lines.push(format!("🛡️ <b>Circuit Breaker:</b> {}", cb));
        lines.push(format!("📉 Max Drawdown: {:.0}% | Daily Loss: {:.0}%",
            self.config.risk.max_drawdown_pct, self.config.risk.daily_loss_limit_pct));

        // Signal engine
        if let Some(ref signal) = self.signal {
            let sig_status = signal.get_status().await;
            let mode_tag = if sig_status.audit_mode { "AUDIT" } else { "LIVE" };
            lines.push("•••".to_string());
            lines.push(format!("📡 <b>Signal Copy Engine ({})</b>", mode_tag));
            lines.push(format!("  State: <b>{}</b> | Positions: {}",
                sig_status.state, sig_status.open_positions));
            lines.push(format!("  Trades today: {}/{} | Daily P&L: ${:.2}",
                sig_status.trades_today, sig_status.max_trades, sig_status.daily_pnl));
            if sig_status.halted {
                lines.push("  🚨 RISK HALTED".to_string());
            }
        }

        // Resources
        lines.push("•••".to_string());
        lines.push(format!("💻 CPU: {:.0}% | RAM: {:.0}% | Disk: {:.0}%",
            sys.cpu_pct, sys.ram_pct, sys.disk_pct));
        lines.push(format!("💾 {:.1}/{:.1} GB", sys.disk_used_gb, sys.disk_total_gb));

        lines.join("\n")
    }

    fn cmd_help(&self) -> String {
        let pairs: Vec<&String> = self.config.pairs.keys().collect();
        let pair_display = pairs.first().map(|s| s.as_str()).unwrap_or("BTCUSDT");

        format!(
            "📖 <b>Available Commands</b>\n\
             •••\n\
             <b>System:</b>\n\
             /status — Daily summary (strategies, CB, signal, server)\n\
             /system — Full engine details + resources\n\
             /price — Current {pair} price from order book\n\
             •••\n\
             <b>Grid:</b>\n\
             /grid_status — Grid state, pending orders, capital, growth\n\
             /pnl — P&L summary (grid + trend)\n\
             /balance — Account balances (USDT, base asset)\n\
             /pending — Open orders from exchange\n\
             /pause — Pause all strategies\n\
             /resume — Resume all strategies\n\
             /reset — Reset circuit breaker\n\
             •••\n\
             <b>Trend:</b>\n\
             /trend_status — Trend positions, signals, P&L per pair\n\
             •••\n\
             <b>Signal:</b>\n\
             /signal_status — Signal engine status & positions\n\
             /signal_prices — Live prices & unrealized P&L for open positions\n\
             /signal_pnl — Signal trades P&L report\n\
             /signal_pause — Pause signal execution\n\
             /signal_resume — Resume signal execution\n\
             /signal_close PAIR — Close a signal position\n\
             •••\n\
             /help — This message",
            pair = pair_display
        )
    }

    async fn cmd_price(&self) -> Option<String> {
        let pair = self.strategies.first()
            .map(|s| s.trading_pair().to_string())
            .unwrap_or("BTCUSDT".to_string());

        let ob = self.order_books.get(&pair)?;
        let mid = ob.mid_price()?;

        Some(format!(
            "◎ <b>{}</b>\n\
             •••\n\
             💲 <b>${:.2}</b>\n\
             •••\n\
             📈 Bid: ${:.2}\n\
             📉 Ask: ${:.2}\n\
             📏 Spread: ${:.2}",
            pair, mid,
            ob.best_bid().unwrap_or(0.0),
            ob.best_ask().unwrap_or(0.0),
            ob.best_ask().unwrap_or(0.0) - ob.best_bid().unwrap_or(0.0),
        ))
    }

    // ── Grid commands ────────────────────────────────────────────────

    fn cmd_grid_status(&self) -> String {
        let uptime = self.started_at.elapsed();
        let hours = uptime.as_secs() / 3600;
        let minutes = (uptime.as_secs() % 3600) / 60;
        let secs = uptime.as_secs() % 60;

        let mode = if self.config.exchange.testnet { "TESTNET" } else { "PRODUCTION" };
        let cb = if self.risk.circuit_breaker.is_halted() { "🛑 HALTED" } else { "✅ OK" };

        let mut lines = vec![
            "📊 <b>Grid Status</b>".to_string(),
            "•••".to_string(),
            format!("Mode: {} | CB: {}", mode, cb),
            format!("⏱ <b>Up:</b> {}h {}m {}s", hours, minutes, secs),
            "•••".to_string(),
        ];

        for s in &self.strategies {
            let st = s.status();
            let cur = s.current_capital();
            let init = s.initial_capital();
            let growth_pct = if init > 0.0 { (cur - init) / init * 100.0 } else { 0.0 };

            lines.push(format!("📐 <b>{}:</b> {}", st.pair, st.state));
            lines.push(format!("  Levels: {} | Pending: {}", self.config.grid.levels, st.open_orders));
            lines.push(format!("  💰 Base: ${:.0} | Comp: ${:.2} ({:+.1}%)", init, cur, growth_pct));
        }

        lines.join("\n")
    }

    // ── Trend commands ─────────────────────────────────────────────

    fn cmd_trend_status(&self) -> String {
        let mode = if self.config.exchange.testnet { "TESTNET" } else { "PRODUCTION" };
        let cb = if self.risk.circuit_breaker.is_halted() { "🛑 HALTED" } else { "✅ OK" };

        let mut lines = vec![
            "📈 <b>Trend Status</b>".to_string(),
            "•••".to_string(),
            format!("Mode: {} | CB: {}", mode, cb),
            "•••".to_string(),
        ];

        let mut found = false;
        for s in &self.strategies {
            if s.name() != "trend" { continue; }
            found = true;
            let st = s.status();
            let cur = s.current_capital();
            let init = s.initial_capital();
            let growth_pct = if init > 0.0 { (cur - init) / init * 100.0 } else { 0.0 };

            lines.push(format!("📈 <b>{}:</b> {}", st.pair, st.state));

            // Show position details from the strategy status details field
            if !st.details.is_empty() {
                for detail_line in st.details.split('\n') {
                    if !detail_line.is_empty() {
                        lines.push(format!("  {}", detail_line));
                    }
                }
            }

            lines.push(format!("  💰 P&L: ${:.2} | Growth: {:+.1}%", st.pnl, growth_pct));
        }

        if !found {
            lines.push("No trend strategies active.".to_string());
        }

        lines.join("\n")
    }

    fn cmd_pnl(&self) -> String {
        let mut lines = vec![
            "💰 <b>P&L Report</b>".to_string(),
            "•••".to_string(),
        ];

        for s in &self.strategies {
            let st = s.status();
            let sign = if st.pnl >= 0.0 { "+" } else { "" };
            let cur = s.current_capital();
            let init = s.initial_capital();
            let growth = if init > 0.0 { cur / init } else { 1.0 };

            lines.push(format!(
                "{} <b>{}:</b> {}${:.2} | Growth: {:.2}x",
                match st.name.as_str() {
                    "grid" => "🤖",
                    "trend" => "📈",
                    _ => "📊",
                },
                st.pair, sign, st.pnl, growth
            ));
        }

        lines.join("\n")
    }

    async fn cmd_balance(&self) -> Option<String> {
        let balances = self.connector.get_balances().await.ok()?;

        let usdt = balances.get("USDT").copied().unwrap_or(0.0);

        // Find first non-USDT balance with value
        let base = balances.iter()
            .filter(|(k, _)| *k != "USDT")
            .find(|(_, &v)| v > 0.0);

        let mut lines = vec![
            "💰 <b>Account Balance</b>".to_string(),
            "•••".to_string(),
            format!("💵 USDT: ${:.2}", usdt),
        ];

        if let Some((asset, amount)) = base {
            // Try to get price from order book
            let pair = format!("{}USDT", asset);
            let price = self.order_books.get(&pair)
                .and_then(|ob| ob.mid_price())
                .unwrap_or(0.0);
            let value = amount * price;
            lines.push(format!("◎ {}: {:.4} (${:.2})", asset, amount, value));
            lines.push("•••".to_string());
            lines.push(format!("📊 <b>Eq:</b> ${:.2}", usdt + value));
        }

        let mode = if self.config.exchange.testnet { "TESTNET" } else { "PRODUCTION" };
        lines.push(format!("⚙️ <b>Env:</b> {}", mode));

        Some(lines.join("\n"))
    }

    async fn cmd_pending(&self) -> Option<String> {
        let pair = self.strategies.first()
            .map(|s| s.trading_pair().to_string())
            .unwrap_or("BTCUSDT".to_string());

        let orders = self.connector.get_open_orders(&pair).await.ok()?;

        if orders.is_empty() {
            return Some("📋 No pending orders.".to_string());
        }

        let buys: Vec<&OpenOrder> = orders.iter().filter(|o| matches!(o.side, OrderSide::Buy)).collect();
        let sells: Vec<&OpenOrder> = orders.iter().filter(|o| matches!(o.side, OrderSide::Sell)).collect();

        let mut lines = vec![
            format!("📋 <b>Pending Orders ({})</b>", orders.len()),
            "•••".to_string(),
        ];

        if !buys.is_empty() {
            lines.push(format!("📈 <b>BUY ({})</b>", buys.len()));
            for o in &buys {
                let val = o.price * o.quantity;
                lines.push(format!("  ${:.2} × {:.4} (${:.2})", o.price, o.quantity, val));
            }
        }

        if !sells.is_empty() {
            lines.push(format!("📉 <b>SELL ({})</b>", sells.len()));
            for o in &sells {
                let val = o.price * o.quantity;
                lines.push(format!("  ${:.2} × {:.4} (${:.2})", o.price, o.quantity, val));
            }
        }

        lines.push("•••".to_string());
        let total_buy: f64 = buys.iter().map(|o| o.price * o.quantity).sum();
        let total_sell: f64 = sells.iter().map(|o| o.price * o.quantity).sum();
        lines.push(format!("💰 Buy value: ${:.2} | Sell value: ${:.2}", total_buy, total_sell));

        Some(lines.join("\n"))
    }

    fn cmd_pause(&mut self) -> String {
        for s in &mut self.strategies {
            s.set_paused(true);
        }
        info!("Telegram /pause — strategies paused");
        "⏸️ All strategies paused. Use /resume to restart.".to_string()
    }

    fn cmd_resume(&mut self) -> String {
        for s in &mut self.strategies {
            s.set_paused(false);
        }
        info!("Telegram /resume — strategies resumed");
        "🟢 All strategies resumed. Will activate on next valid signal.".to_string()
    }

    fn cmd_reset(&mut self) -> String {
        // Estimate equity from balances + order books
        let equity = self.estimate_equity();
        self.risk.circuit_breaker.reset(equity);
        info!("Telegram /reset — circuit breaker reset, equity=${:.2}", equity);
        "🔄 Circuit breaker reset. Bot will resume on next tick.".to_string()
    }

    // ── Signal commands ────────────────────────────────────────────────

    async fn cmd_signal_status(&self) -> Option<String> {
        // Read signal engine state from Rust's own tracking (accurate)
        let signal = self.signal.as_ref()?;
        let sig_status = signal.get_status().await;
        let mode_tag = if sig_status.audit_mode { "AUDIT" } else { "LIVE" };

        let mut lines = vec![
            format!("📡 <b>SIGNAL ENGINE ({})</b>", mode_tag),
            "•••".to_string(),
            format!("State: <b>{}</b>", sig_status.state),
            format!("Open positions: {}", sig_status.open_positions),
            format!("Trades today: {}/{}", sig_status.trades_today, sig_status.max_trades),
            format!("Daily P&L: ${:.2}", sig_status.daily_pnl),
        ];

        if sig_status.halted {
            lines.push("🚨 Risk guard halted — use /signal_resume".to_string());
        }

        // Show positions from Rust's signal_positions.json (source of truth)
        let positions_content = tokio::fs::read_to_string("data/signal_positions.json")
            .await
            .ok()
            .unwrap_or_default();

        if !positions_content.is_empty() {
            if let Ok(positions) = serde_json::from_str::<serde_json::Value>(&positions_content) {
                let open_positions: Vec<&serde_json::Value> = positions.as_object()
                    .map(|m| m.values().filter(|p| !p.get("is_closed").and_then(|v| v.as_bool()).unwrap_or(true)).collect())
                    .unwrap_or_default();

                if !open_positions.is_empty() {
                    lines.push("•••".to_string());
                    lines.push("📈 <b>Open Positions:</b>".to_string());
                    for pos in open_positions {
                        let symbol = pos.get("symbol").and_then(|v| v.as_str()).unwrap_or("?");
                        let entry = pos.get("entry_price").and_then(|v| v.as_f64()).unwrap_or(0.0);
                        let sl = pos.get("stop_loss").and_then(|v| v.as_f64()).unwrap_or(0.0);
                        let tp1 = if pos.get("tp1_hit").and_then(|v| v.as_bool()).unwrap_or(false) { "✅" } else { "⬜" };
                        let tp2 = if pos.get("tp2_hit").and_then(|v| v.as_bool()).unwrap_or(false) { "✅" } else { "⬜" };
                        let tp3 = if pos.get("tp3_hit").and_then(|v| v.as_bool()).unwrap_or(false) { "✅" } else { "⬜" };
                        let channel = pos.get("channel_name").and_then(|v| v.as_str()).unwrap_or("");
                        lines.push(format!(
                            "  {}: ${:.4} SL=${:.4} TPs={}{}{} {}",
                            symbol, entry, sl, tp1, tp2, tp3, channel
                        ));
                    }
                }
            }
        }

        Some(lines.join("\n"))
    }

    async fn cmd_signal_prices(&self) -> Option<String> {
        // Read positions from Rust's signal_positions.json
        let positions_content = tokio::fs::read_to_string("data/signal_positions.json")
            .await
            .ok()
            .unwrap_or_default();

        if positions_content.is_empty() {
            return Some("📡 No signal positions file found.".to_string());
        }

        let positions: serde_json::Value = serde_json::from_str(&positions_content).ok()?;
        let open: Vec<&serde_json::Value> = positions.as_object()
            .map(|m| m.values().filter(|p| !p.get("is_closed").and_then(|v| v.as_bool()).unwrap_or(true)).collect())
            .unwrap_or_default();

        if open.is_empty() {
            return Some("📡 No open signal positions.".to_string());
        }

        let mut lines = vec![
            "💰 <b>Signal Prices</b>".to_string(),
            "•••".to_string(),
        ];

        for pos in &open {
            let symbol = pos.get("symbol").and_then(|v| v.as_str()).unwrap_or("?");
            let entry = pos.get("entry_price").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let sl = pos.get("stop_loss").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let tps = pos.get("take_profits").and_then(|v| v.as_array()).cloned().unwrap_or_default();
            let tp1_hit = pos.get("tp1_hit").and_then(|v| v.as_bool()).unwrap_or(false);
            let tp2_hit = pos.get("tp2_hit").and_then(|v| v.as_bool()).unwrap_or(false);
            let tp3_hit = pos.get("tp3_hit").and_then(|v| v.as_bool()).unwrap_or(false);
            let remaining = pos.get("amount").and_then(|v| v.as_f64()).unwrap_or(0.0)
                - pos.get("amount_closed").and_then(|v| v.as_f64()).unwrap_or(0.0);

            // Fetch current price from Binance
            let binance_sym = symbol.replace("-", "");
            let price = match reqwest::get(&format!(
                "https://api.binance.com/api/v3/ticker/price?symbol={}", binance_sym
            )).await {
                Ok(resp) => resp.json::<serde_json::Value>().await
                    .ok()
                    .and_then(|d| d.get("price").and_then(|p| p.as_str()).and_then(|s| s.parse::<f64>().ok()))
                    .unwrap_or(0.0),
                Err(_) => 0.0,
            };

            if price <= 0.0 {
                lines.push(format!("{} — ⚠️ Price unavailable", symbol));
                continue;
            }

            let pnl_pct = (price - entry) / entry * 100.0;
            let unrealized = (price - entry) * remaining;
            let pnl_emoji = if unrealized >= 0.0 { "🟢" } else { "🔴" };

            lines.push(format!("{} {} <b>${:.4}</b> ({:+.1}%)", pnl_emoji, symbol, price, pnl_pct));
            lines.push(format!("  Entry: ${:.4} | SL: ${:.4}", entry, sl));
            lines.push(format!("  Unrealized: ${:.2} ({} tokens)", unrealized, remaining as i64));

            // Show TP status
            let mut tp_str = String::new();
            for (i, tp) in tps.iter().enumerate() {
                let tp_price = tp.as_f64().unwrap_or(0.0);
                let hit = match i {
                    0 => tp1_hit,
                    1 => tp2_hit,
                    2 => tp3_hit,
                    _ => false,
                };
                let marker = if hit { "✅" } else if price >= tp_price { "🔥" } else { "⬜" };
                tp_str.push_str(&format!("{}${:.4} ", marker, tp_price));
            }
            if !tp_str.is_empty() {
                lines.push(format!("  TPs: {}", tp_str.trim_end()));
            }
            lines.push("".to_string());
        }

        Some(lines.join("\n"))
    }

    async fn cmd_signal_pnl(&self) -> Option<String> {
        let signal = self.signal.as_ref()?;
        let journal = signal.journal();

        let today = journal.summary(0);
        let week = journal.summary(7);
        let month = journal.summary(30);
        let all = journal.summary(-1);

        Some(format!(
            "📊 <b>SIGNAL P&L</b>\n\
             •••\n\
             📅 Today: {} trades, ${:.2} ({:.0}% win)\n\
             📆 Week:  {} trades, ${:.2} ({:.0}% win)\n\
             🗓 Month: {} trades, ${:.2} ({:.0}% win)\n\
             🏦 All:   {} trades, ${:.2} ({:.0}% win)",
            today.total_trades, today.total_pnl, today.win_rate,
            week.total_trades, week.total_pnl, week.win_rate,
            month.total_trades, month.total_pnl, month.win_rate,
            all.total_trades, all.total_pnl, all.win_rate,
        ))
    }

    fn cmd_signal_pause(&mut self) -> Option<String> {
        let signal = self.signal.as_mut()?;
        signal.pause();
        Some("⏸ Signal engine paused.".to_string())
    }

    fn cmd_signal_resume(&mut self) -> Option<String> {
        let signal = self.signal.as_mut()?;
        signal.resume();
        Some("▶️ Signal engine resumed.".to_string())
    }

    async fn cmd_signal_close(&mut self, text: &str) -> Option<String> {
        let signal = self.signal.as_mut()?;
        let parts: Vec<&str> = text.split_whitespace().collect();
        if parts.len() < 2 {
            return Some("Usage: /signal_close BTC-USDT".to_string());
        }
        let pair = parts[1].to_uppercase().replace("/", "-");
        if !pair.ends_with("-USDT") {
            return Some(format!("Usage: /signal_close BTC-USDT"));
        }

        let closed = signal.manual_close(&pair).await;
        if closed {
            Some(format!("Closed signal position: {}", pair))
        } else {
            Some(format!("No open signal position for {}", pair))
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

// ══════════════════════════════════════════════════════════════════
// System stats helper
// ══════════════════════════════════════════════════════════════════

struct SystemStats {
    cpu_pct: f32,
    ram_pct: f32,
    disk_pct: f32,
    disk_used_gb: f64,
    disk_total_gb: f64,
}

fn system_stats() -> SystemStats {
    use sysinfo::{System, Disks};

    let mut sys = System::new();
    sys.refresh_all();
    let disks = Disks::new_with_refreshed_list();

    let cpu_pct = sys.global_cpu_usage();
    let ram_pct = (sys.used_memory() as f32 / sys.total_memory() as f32) * 100.0;

    // First disk
    let (disk_pct, disk_used_gb, disk_total_gb) = disks.iter().next()
        .map(|d| {
            let total = d.total_space() as f64 / 1_073_741_824.0;
            let used = (d.total_space() - d.available_space()) as f64 / 1_073_741_824.0;
            let pct = if total > 0.0 { (used / total) * 100.0 } else { 0.0 };
            (pct as f32, used, total)
        })
        .unwrap_or((0.0, 0.0, 0.0));

    SystemStats { cpu_pct, ram_pct, disk_pct, disk_used_gb, disk_total_gb }
}
