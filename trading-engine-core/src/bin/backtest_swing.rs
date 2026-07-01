use std::collections::HashMap;
use std::env;
use std::fs::File;
use std::io::{BufRead, BufReader};
use trading_engine_core::config::{RunnerExitMode, SwingConfig};
use trading_engine_core::connector::types::OrderBook;
use trading_engine_core::models::bar::Bar;
use trading_engine_core::notifications::TelegramBot;
use trading_engine_core::strategy::{Strategy, TickContext};

#[tokio::main]
async fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        println!("Usage: cargo run --bin backtest_swing <path_to_csv>");
        return;
    }

    let csv_path = &args[1];
    let file = File::open(csv_path).expect("Failed to open CSV");
    let reader = BufReader::new(file);

    let config = SwingConfig {
        enabled: true,
        runner_exit: RunnerExitMode::BandOrChandelier,
        htf_period: "1h".to_string(),
        ltf_period: "5m".to_string(),
        donchian_period: 20,
        band_atr_mult: 0.5,
        rsi_period: 14,
        rsi_oversold: 30.0,
        volume_multiplier: 1.5,
        volume_avg_period: 20,
        atr_period: 14,
        atr_stop_mult: 1.5,
        min_rr: 2.0,
        min_score: 3,
        risk_per_trade_pct: 1.0,
        adx_range_entry: 22.0,
        adx_trend_exit: 28.0,
        capital: 1000.0,
        max_bars_in_trade: 48,
        enabled_pairs: vec![],
        step_size: None,
        tick_size: None,
        maker_entry: false,
        entry_timeout_bars: 2,
    };

    let mut strategy = trading_engine_core::strategy::swing::SwingStrategy::new(
        "BTC-USDT",
        &config,
        TelegramBot::new("", ""), // No telegram for backtest
    );

    let mut recent_bars: Vec<Bar> = Vec::new();
    let mut lines = reader.lines();
    
    // Skip header if present
    if let Some(Ok(header)) = lines.next() {
        if !header.contains("timestamp") && !header.contains("open") {
            // It wasn't a header, we should probably parse it
        }
    }

    let mut tick_count = 0;

    for line in lines {
        let line = line.expect("Failed to read line");
        let parts: Vec<&str> = line.split(',').collect();
        if parts.len() < 6 { continue; }

        let timestamp: i64 = parts[0].parse().unwrap_or(0);
        let open: f64 = parts[1].parse().unwrap_or(0.0);
        let high: f64 = parts[2].parse().unwrap_or(0.0);
        let low: f64 = parts[3].parse().unwrap_or(0.0);
        let close: f64 = parts[4].parse().unwrap_or(0.0);
        let volume: f64 = parts[5].parse().unwrap_or(0.0);

        let bar = Bar::new(open, high, low, close, volume, timestamp);
        recent_bars.push(bar);
        
        // Keep window bounded
        if recent_bars.len() > 2000 {
            recent_bars.remove(0);
        }

        let ctx = TickContext {
            order_book: OrderBook {
                symbol: "BTCUSDT".to_string(),
                bids: vec![(close, 1.0)],
                asks: vec![(close, 1.0)],
                timestamp,
            },
            recent_bars: recent_bars.clone(),
            balances: HashMap::new(),
            open_orders: vec![],
            regime: None,
            regime_confidence: 0.0,
            timestamp,
            capital: None,
        };

        if let Ok(orders) = strategy.on_tick(&ctx).await {
            for order in orders {
                // Simulate instant fill
                let fill = trading_engine_core::connector::types::Fill {
                    fill_id: format!("fill_{}", tick_count),
                    order_id: format!("order_{}", tick_count),
                    client_order_id: None,
                    symbol: "BTCUSDT".to_string(),
                    side: order.side,
                    price: close, // Fill at close price
                    quantity: order.quantity,
                    fee: 0.0,
                    timestamp,
                };
                let _ = strategy.on_fill(&fill).await;
            }
        }
        tick_count += 1;
    }

    let status = strategy.status();
    println!("Backtest complete. Processed {} ticks.", tick_count);
    println!("Final Realized PnL: {:.2} USDT", status.pnl);
    println!("Final State: {}", status.state);
}
