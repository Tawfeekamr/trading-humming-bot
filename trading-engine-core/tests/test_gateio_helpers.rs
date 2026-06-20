use trading_engine_core::connector::gateio_rest;

#[test]
fn test_to_gate_pair_dashed() {
    assert_eq!(gateio_rest::to_gate_pair("BTC-USDT"), "BTC_USDT");
}

#[test]
fn test_to_gate_pair_already_underscore() {
    assert_eq!(gateio_rest::to_gate_pair("BTC_USDT"), "BTC_USDT");
}

#[test]
fn test_from_gate_pair_to_dashed() {
    assert_eq!(gateio_rest::from_gate_pair("BTC_USDT"), "BTCUSDT");
}

#[test]
fn test_roundtrip_no_separator() {
    let gate = "BTC_USDT";
    assert_eq!(gateio_rest::to_gate_pair(&gateio_rest::from_gate_pair(gate)), gate);
}
