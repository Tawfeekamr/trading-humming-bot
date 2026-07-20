use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize)]
pub enum SignalAction {
    #[serde(rename = "OPEN_LONG")]
    OpenLong,
    #[serde(rename = "CLOSE")]
    Close,
    #[serde(rename = "UPDATE_SL")]
    UpdateSl,
    #[serde(rename = "UPDATE_TP")]
    UpdateTp,
    #[serde(rename = "NOT_A_SIGNAL")]
    NotASignal,
}

impl SignalAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::OpenLong => "OPEN_LONG",
            Self::Close => "CLOSE",
            Self::UpdateSl => "UPDATE_SL",
            Self::UpdateTp => "UPDATE_TP",
            Self::NotASignal => "NOT_A_SIGNAL",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize)]
pub enum SignalConfidence {
    #[serde(rename = "high")]
    High,
    #[serde(rename = "medium")]
    Medium,
    #[serde(rename = "low")]
    Low,
}

impl SignalConfidence {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::High => "high",
            Self::Medium => "medium",
            Self::Low => "low",
        }
    }

    pub fn multiplier(&self) -> f64 {
        match self {
            Self::High => 1.0,
            Self::Medium => 0.66,
            Self::Low => 0.33,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum SignalEngineState {
    Listening,
    Paused,
    Disabled,
}

impl SignalEngineState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Listening => "LISTENING",
            Self::Paused => "PAUSED",
            Self::Disabled => "DISABLED",
        }
    }
}

/// Parsed signal from AI parser
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParsedSignal {
    pub action: String,
    pub pair: Option<String>,
    pub entry_low: Option<f64>,
    pub entry_high: Option<f64>,
    pub stop_loss: Option<f64>,
    pub take_profits: Vec<f64>,
    pub confidence: String,
    pub quality_score: u8,
    pub quality_reason: String,
    pub is_market_entry: bool,
    pub reasoning: String,

    #[serde(skip)]
    pub raw_message: String,
}

impl ParsedSignal {
    pub fn signal_action(&self) -> SignalAction {
        match self.action.as_str() {
            "OPEN_LONG" => SignalAction::OpenLong,
            "CLOSE" => SignalAction::Close,
            "UPDATE_SL" => SignalAction::UpdateSl,
            "UPDATE_TP" => SignalAction::UpdateTp,
            _ => SignalAction::NotASignal,
        }
    }

    pub fn signal_confidence(&self) -> SignalConfidence {
        match self.confidence.as_str() {
            "high" => SignalConfidence::High,
            "low" => SignalConfidence::Low,
            _ => SignalConfidence::Medium,
        }
    }

    pub fn not_a_signal(raw: &str) -> Self {
        Self {
            action: "NOT_A_SIGNAL".to_string(),
            raw_message: raw.to_string(),
            pair: None,
            entry_low: None,
            entry_high: None,
            stop_loss: None,
            take_profits: Vec::new(),
            confidence: "medium".to_string(),
            quality_score: 5,
            quality_reason: String::new(),
            is_market_entry: false,
            reasoning: String::new(),
        }
    }
}

/// Open signal position with TP tracking
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalPosition {
    pub symbol: String,
    pub entry_price: f64,
    pub amount: f64,
    pub stop_loss: f64,
    pub take_profits: Vec<f64>,
    pub signal_confidence: String,
    pub raw_message: String,
    pub channel_name: String,
    #[serde(default)]
    pub entry_timestamp: f64,
    #[serde(default)]
    pub tp1_hit: bool,
    #[serde(default)]
    pub tp2_hit: bool,
    #[serde(default)]
    pub tp3_hit: bool,
    #[serde(default)]
    pub amount_closed: f64,
    #[serde(default)]
    pub realized_pnl: f64,
    #[serde(default)]
    pub is_closed: bool,
    #[serde(default)]
    pub exit_reason: String,
    #[serde(default = "default_tp1_pct")]
    pub tp1_close_pct: f64,
    #[serde(default = "default_tp2_pct")]
    pub tp2_close_pct: f64,
    #[serde(default)]
    pub order_id: String,
    #[serde(default = "default_side")]
    pub side: String,
}


fn default_side() -> String { "long".to_string() }
fn default_tp1_pct() -> f64 { 0.33 }
fn default_tp2_pct() -> f64 { 0.50 }

impl SignalPosition {
    pub fn remaining_amount(&self) -> f64 {
        self.amount - self.amount_closed
    }

    pub fn hold_minutes(&self) -> i64 {
        if self.entry_timestamp <= 0.0 { return 0; }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as f64;
        ((now - self.entry_timestamp) / 60.0) as i64
    }
}

/// Trade record for journaling
#[derive(Debug, Clone)]
pub struct SignalTrade {
    pub timestamp: String,
    pub symbol: String,
    pub channel_name: String,
    pub action: String,
    pub entry_price: f64,
    pub current_price: f64,
    pub quantity: f64,
    pub realized_pnl: f64,
    pub exit_reason: String,
    pub signal_confidence: String,
    pub stop_loss: f64,
    pub take_profits: String,
    pub tp1_hit: i32,
    pub tp2_hit: i32,
    pub tp3_hit: i32,
    pub raw_message: String,
    pub parse_reasoning: String,
    pub is_audit: i32,
}

/// Message from channel listener
#[derive(Debug, Clone)]
pub struct ChannelMessage {
    pub channel_id: i64,
    pub channel_name: String,
    pub text: String,
    pub message_id: i32,
    pub timestamp: f64,
}
