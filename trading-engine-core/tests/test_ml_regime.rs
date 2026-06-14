use trading_engine_core::ml::regime::RegimeClassifier;
use trading_engine_core::models::bar::Bar;

#[test]
fn test_feature_extraction_produces_correct_size() {
    let bars: Vec<Bar> = (0..50).map(|i| {
        Bar::new(50000.0, 50100.0, 49900.0, 50050.0, 100.0, i * 60000)
    }).collect();

    let features = trading_engine_core::ml::regime::extract_features(&bars);
    assert_eq!(features.len(), 8, "Feature vector should have 8 elements");
}

#[test]
fn test_feature_extraction_returns_empty_for_few_bars() {
    let bars: Vec<Bar> = (0..5).map(|i| {
        Bar::new(50000.0, 50100.0, 49900.0, 50050.0, 100.0, i * 60000)
    }).collect();

    let features = trading_engine_core::ml::regime::extract_features(&bars);
    assert!(features.is_empty());
}

#[test]
fn test_regime_classifier_loads_model() {
    let model_path = "models/regime.onnx";
    if !std::path::Path::new(model_path).exists() {
        eprintln!("Skipping test - model file not found");
        return;
    }
    let classifier = RegimeClassifier::new(model_path);
    assert!(classifier.is_ok());
}
