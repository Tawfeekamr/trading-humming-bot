use crate::signal::types::ParsedSignal;
use anyhow::{Result, Context};
use reqwest::Client;
use serde_json::Value;
use tracing::{warn, error};

const SYSTEM_PROMPT: &str = r#"You are a trading signal parser and quality scorer. Extract structured trade information from Telegram messages and score signal quality.

RULES:
1. Only extract ACTIONABLE trading signals. General market commentary, motivation posts, questions, or charts without clear entry/exit are NOT signals.
2. We trade SPOT only. Ignore any leverage mentions (e.g. "2-5x", "10x") — still extract the signal. Only reject if the direction is explicitly SHORT/SELL as an opening position.
3. Normalize all pairs to format: "BTC-USDT", "ETH-USDT", etc. Add -USDT suffix if missing.
4. If the trader gives a price range for entry (e.g., "95-96k"), extract both entry_low and entry_high.
5. If only one entry price is given, set entry_low = entry_high.
6. Take-profit targets should be sorted ascending (lowest first).
7. If the message says "close", "exit", "take profit", "out", "book" for a specific pair, the action is CLOSE.
8. If the message updates stop-loss only (e.g., "move SL to entry"), the action is UPDATE_SL.
9. If no stop-loss is given for a new position, set stop_loss to null.
10. Convert shorthand: "95k" = 95000, "0.5" stays 0.5, "$100" = 100.0
11. If the message is just market commentary, analysis, or chat with no specific entry/exit, action is NOT_A_SIGNAL.

CRITICAL — Distinguishing NEW ENTRY signals from RESULT UPDATES:
A message is a NEW ENTRY signal (action: OPEN_LONG) if it has an ENTRY price/zone and TARGETS without checkmarks (✅).
A message is a RESULT UPDATE (action: NOT_A_SIGNAL) if targets have ✅ checkmarks, show "X% Profit", or say "Loss" — these report past results, not new trades.

Examples:
- "ENTRY: 56.80 - 57.00 | TARGETS: 59.50 - 62.00 - 65.00 | STOP LOSS: 52.00" → OPEN_LONG
- "Target 1: 59.50✅ | Target 2: 62.00✅ | 🔥70.2% Profit🔥" → NOT_A_SIGNAL
- "STOP LOSS: 0.0650 | 🚫19.4% Loss🚫" → NOT_A_SIGNAL

QUALITY SCORING (1-10):
- 8-10: Excellent R:R (2:1+), clear SL, multiple TPs, technical confluence
- 5-7: Decent signal but some weaknesses
- 1-4: Poor signal (no SL, unrealistic TPs, vague entry)

OUTPUT FORMAT (JSON only, no markdown, no code blocks):
{"action":"OPEN_LONG"|\"CLOSE\"|\"UPDATE_SL\"|\"UPDATE_TP\"|\"NOT_A_SIGNAL\",\"pair\":\"BTC-USDT\"|null,\"entry_low\":95000.0|null,\"entry_high\":96000.0|null,\"stop_loss\":93500.0|null,\"take_profits\":[98000.0,100000.0],\"confidence\":\"high\"|\"medium\"|\"low\",\"quality_score\":8,\"quality_reason\":\"...\",\"is_market_entry\":false,\"reasoning\":\"...\"}"#;

pub struct SignalParser {
    api_key: String,
    model: String,
    client: Client,
}

impl SignalParser {
    pub fn new(api_key: &str, model: &str) -> Self {
        Self {
            api_key: api_key.to_string(),
            model: model.to_string(),
            client: Client::new(),
        }
    }

    pub async fn parse(&self, message: &str) -> ParsedSignal {
        if self.api_key.is_empty() {
            warn!("DEEPSEEK_API_KEY not set, cannot parse signals");
            return ParsedSignal::not_a_signal(message);
        }

        let prompt = format!("Parse this trading signal message:\n\n{}", message);

        match self.call_api(&prompt).await {
            Ok(json) => self.json_to_signal(&json, message),
            Err(e) => {
                error!("Signal parsing failed: {}", e);
                ParsedSignal::not_a_signal(message)
            }
        }
    }

    async fn call_api(&self, prompt: &str) -> Result<Value> {
        let body = serde_json::json!({
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        });

        let resp: Value = self.client
            .post("https://api.deepseek.com/chat/completions")
            .header("Content-Type", "application/json")
            .header("Authorization", format!("Bearer {}", self.api_key))
            .json(&body)
            .send()
            .await
            .context("DeepSeek API request failed")?
            .json()
            .await
            .context("DeepSeek API response parse failed")?;

        if let Some(err) = resp.get("error") {
            anyhow::bail!("DeepSeek API error: {}", err);
        }

        let mut text = resp["choices"][0]["message"]["content"]
            .as_str()
            .unwrap_or("")
            .to_string();

        // Strip markdown code blocks if present
        text = text.trim().to_string();
        if text.starts_with("```") {
            text = text.splitn(2, '\n').nth(1).unwrap_or("").to_string();
        }
        if text.ends_with("```") {
            if let Some(pos) = text.rfind("```") {
                text = text[..pos].to_string();
            }
        }

        let parsed: Value = serde_json::from_str(text.trim())
            .context("Failed to parse DeepSeek response as JSON")?;
        Ok(parsed)
    }

    fn json_to_signal(&self, data: &Value, raw_message: &str) -> ParsedSignal {
        let mut pair = data["pair"].as_str().unwrap_or("").to_string();
        if !pair.is_empty() {
            pair = pair.to_uppercase().replace("/", "-");
            if !pair.ends_with("-USDT") {
                pair = format!("{}-USDT", pair);
            }
        }

        let tps: Vec<f64> = data["take_profits"]
            .as_array()
            .map(|arr| {
                let mut v: Vec<f64> = arr.iter()
                    .filter_map(|v| v.as_f64())
                    .collect();
                v.sort_by(|a, b| a.partial_cmp(b).unwrap());
                v
            })
            .unwrap_or_default();

        let entry_low = data["entry_low"].as_f64();
        let entry_high = data["entry_high"].as_f64();
        let stop_loss = data["stop_loss"].as_f64();
        let quality_score = (data["quality_score"].as_u64().unwrap_or(5) as u8).clamp(1, 10);

        ParsedSignal {
            action: data["action"].as_str().unwrap_or("NOT_A_SIGNAL").to_string(),
            pair: if pair.is_empty() { None } else { Some(pair) },
            entry_low,
            entry_high,
            stop_loss,
            take_profits: tps,
            confidence: data["confidence"].as_str().unwrap_or("medium").to_string(),
            quality_score,
            quality_reason: data["quality_reason"].as_str().unwrap_or("").to_string(),
            is_market_entry: data["is_market_entry"].as_bool().unwrap_or(false),
            reasoning: data["reasoning"].as_str().unwrap_or("").to_string(),
            raw_message: raw_message.to_string(),
        }
    }
}
