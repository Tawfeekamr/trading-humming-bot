#!/usr/bin/env python3
"""Convert trained sklearn/XGBoost model to ONNX format for Rust inference."""
import sys
import pickle
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType

def convert(model_path: str, output_path: str, n_features: int):
    with open(model_path, "rb") as f:
        model = pickle.load(f)

    initial_type = [("float_input", FloatTensorType([None, n_features]))]
    onnx_model = convert_sklearn(model, initial_types=initial_type)

    with open(output_path, "wb") as f:
        f.write(onnx_model.SerializeToString())

    print(f"Converted {model_path} -> {output_path} ({n_features} features)")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <model.pkl> <output.onnx> <n_features>")
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2], int(sys.argv[3]))
