use tokio::sync::{mpsc, oneshot};

use crate::connector::types::{CancelResult, OrderRequest, OrderResponse};

pub type EngineCommandResult<T> = Result<T, String>;

#[derive(Debug)]
pub enum EngineCommand {
    PlaceOrder {
        req: OrderRequest,
        respond_to: oneshot::Sender<EngineCommandResult<OrderResponse>>,
    },
    CancelOrder {
        symbol: String,
        order_id: String,
        respond_to: oneshot::Sender<EngineCommandResult<()>>,
    },
    CancelAllOrders {
        symbol: String,
        respond_to: oneshot::Sender<EngineCommandResult<Vec<CancelResult>>>,
    },
}

#[derive(Clone)]
pub struct EngineCommandBus {
    tx: mpsc::Sender<EngineCommand>,
}

impl EngineCommandBus {
    pub fn channel(capacity: usize) -> (Self, mpsc::Receiver<EngineCommand>) {
        let (tx, rx) = mpsc::channel(capacity);
        (Self { tx }, rx)
    }

    pub async fn place_order(&self, req: OrderRequest) -> EngineCommandResult<OrderResponse> {
        let (respond_to, response) = oneshot::channel();
        self.tx
            .send(EngineCommand::PlaceOrder { req, respond_to })
            .await
            .map_err(|_| "engine command queue closed".to_string())?;
        response
            .await
            .map_err(|_| "engine dropped order response".to_string())?
    }

    pub async fn cancel_order(&self, symbol: String, order_id: String) -> EngineCommandResult<()> {
        let (respond_to, response) = oneshot::channel();
        self.tx
            .send(EngineCommand::CancelOrder { symbol, order_id, respond_to })
            .await
            .map_err(|_| "engine command queue closed".to_string())?;
        response
            .await
            .map_err(|_| "engine dropped cancel response".to_string())?
    }

    pub async fn cancel_all_orders(&self, symbol: String) -> EngineCommandResult<Vec<CancelResult>> {
        let (respond_to, response) = oneshot::channel();
        self.tx
            .send(EngineCommand::CancelAllOrders { symbol, respond_to })
            .await
            .map_err(|_| "engine command queue closed".to_string())?;
        response
            .await
            .map_err(|_| "engine dropped cancel-all response".to_string())?
    }
}
