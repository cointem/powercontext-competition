"""Download public, pinned ONNX weights; no inference or credentials."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "Xenova/all-MiniLM-L6-v2"
REVISION = "751bff37182d3f1213fa05d7196b954e230abad9"
FILES = ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
         "onnx/model.onnx", "onnx/model_fp16.onnx", "onnx/model_quantized.onnx"]
target = ROOT / "public/models" / MODEL
manifest = {"model": MODEL, "revision": REVISION, "files": []}
for name in FILES:
    path = target / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".part")
        subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--output", str(temporary),
                        f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}"], check=True)
        temporary.replace(path)
    manifest["files"].append({"file": name, "bytes": path.stat().st_size,
                               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
(ROOT / "public/model-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest, indent=2))
