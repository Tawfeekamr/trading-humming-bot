//! Parse-check the production config so a typo fails CI instead of crashing the
//! bot on startup. This is the guard the `runner_exit: "BandOrChandelier"`
//! (serde expects `band_or_chandelier`) incident showed was missing: that typo
//! passed review and crashed the whole paper container on boot.
//!
//! Catches: wrong enum variant, bad value type, missing required field, stray
//! param — anything `AppConfig::load` (serde_yaml) would reject at startup.
//! Usage: validate_config <path-to-strategy.yaml>   (default: config/strategy.yaml)

use trading_engine_core::config::AppConfig;

fn main() {
    let path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "config/strategy.yaml".to_string());
    match AppConfig::load(&path) {
        Ok(_) => println!("PASS: {} parsed OK", path),
        Err(e) => {
            eprintln!("FAIL: {} did not parse: {}", path, e);
            eprintln!("       (a config typo like this would crash the bot on startup)");
            std::process::exit(1);
        }
    }
}
