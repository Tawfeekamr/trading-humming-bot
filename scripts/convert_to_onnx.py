import os
import joblib
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType

MODELS_DIR = "models"

def convert_all():
    print("Converting .pkl models to .onnx...")
    # The feature vector in Rust extract_features has 14 features.
    initial_type = [('float_input', FloatTensorType([None, 14]))]
    
    for filename in os.listdir(MODELS_DIR):
        if filename.endswith(".pkl") and not filename.endswith(".old.pkl") and not ".old" in filename:
            filepath = os.path.join(MODELS_DIR, filename)
            try:
                data = joblib.load(filepath)
                if isinstance(data, dict) and "model" in data:
                    model = data["model"]
                else:
                    model = data

                if hasattr(model, "predict"):
                    # We want output probabilities as a tensor instead of a sequence of maps.
                    # Setting zipmap=False produces a [None, 3] float tensor for probabilities.
                    onnx_model = convert_sklearn(
                        model, 
                        initial_types=initial_type, 
                        target_opset=12,
                        options={id(model): {'zipmap': False}}
                    )
                    
                    onnx_filename = filename.replace(".pkl", ".onnx")
                    onnx_filepath = os.path.join(MODELS_DIR, onnx_filename)
                    
                    with open(onnx_filepath, "wb") as f:
                        f.write(onnx_model.SerializeToString())
                    print(f"Converted {filename} -> {onnx_filename}")
                else:
                    print(f"Skipping {filename}: no predict method on {type(model)}")
            except Exception as e:
                print(f"Failed to convert {filename}: {e}")

if __name__ == "__main__":
    convert_all()
