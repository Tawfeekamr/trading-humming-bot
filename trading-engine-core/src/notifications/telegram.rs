use anyhow::Result;
use reqwest::Client;
use tracing::{info, warn, error};

pub struct TelegramBot {
    token: String,
    chat_id: String,
    client: Client,
    enabled: bool,
}

impl TelegramBot {
    pub fn new(token: &str, chat_id: &str) -> Self {
        let enabled = !token.is_empty() && !chat_id.is_empty();
        info!(
            "Telegram bot initialized — enabled: {}, token_len: {}, chat_id: {}",
            enabled, token.len(), chat_id
        );
        Self {
            token: token.to_string(),
            chat_id: chat_id.to_string(),
            client: Client::new(),
            enabled,
        }
    }

    pub fn enabled(&self) -> bool {
        self.enabled
    }

    /// Create a copy sharing the same HTTP client (cheap clone)
    pub fn clone_for_signal(&self) -> Self {
        Self {
            token: self.token.clone(),
            chat_id: self.chat_id.clone(),
            client: self.client.clone(),
            enabled: self.enabled,
        }
    }

    pub async fn send(&self, message: &str) -> Result<()> {
        if !self.enabled {
            warn!("Telegram send skipped — bot is DISABLED (token or chat_id empty)");
            return Ok(());
        }

        let url = format!("https://api.telegram.org/bot{}/sendMessage", self.token);
        info!("Telegram send → api.telegram.org ({} chars)", message.len());

        for attempt in 0..3 {
            match self.client
                .post(&url)
                .form(&[
                    ("chat_id", self.chat_id.as_str()),
                    ("text", message),
                    ("parse_mode", "HTML"),
                ])
                .send()
                .await
            {
                Ok(resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        info!("Telegram send OK (status {})", status);
                        return Ok(());
                    } else {
                        let body = resp.text().await.unwrap_or_default();
                        warn!("Telegram send failed — HTTP {}: {}", status, body);
                        if attempt >= 2 {
                            return Err(anyhow::anyhow!("Telegram API error HTTP {}: {}", status, body));
                        }
                        tokio::time::sleep(tokio::time::Duration::from_millis(500 * (attempt + 1) as u64)).await;
                    }
                }
                Err(e) => {
                    if attempt < 2 {
                        warn!("Telegram send failed (attempt {}): {}", attempt + 1, e);
                        tokio::time::sleep(tokio::time::Duration::from_millis(500 * (attempt + 1) as u64)).await;
                    } else {
                        error!("Telegram send failed after 3 attempts: {}", e);
                        return Err(e.into());
                    }
                }
            }
        }
        Ok(())
    }

    pub fn format_status_message(
        &self,
        pair: &str,
        state: &str,
        pnl: f64,
        open_orders: usize,
        details: &str,
    ) -> String {
        format!(
            "📊 <b>Status — {}</b>\n\
             State: {}\n\
             PnL: ${:.2}\n\
             Open Orders: {}\n\
             {}",
            pair, state, pnl, open_orders, details
        )
    }

    pub fn format_startup_message(
        &self,
        env: &str,
        capital: f64,
        pairs: &str,
        grid_levels: usize,
    ) -> String {
        format!(
            "🚀 <b>Trading Bot Started</b>\n\
             Env: {}\n\
             Capital: ${:.2}\n\
             Pairs: {}\n\
             Grid Levels: {}",
            env, capital, pairs, grid_levels
        )
    }

    pub fn format_error_message(&self, source: &str, error: &str) -> String {
        format!(
            "🚨 <b>Error</b>\n\
             Source: {}\n\
             Error: {}",
            source, error
        )
    }

    pub fn format_shutdown_message(&self, reason: &str) -> String {
        format!("🛑 <b>Bot Stopped</b>\nReason: {}", reason)
    }
}
