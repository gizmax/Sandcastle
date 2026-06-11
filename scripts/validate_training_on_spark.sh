#!/usr/bin/env bash
# Smoke-validate the real GPU LoRA trainer on a DGX Spark (or any CUDA host).
#
# Trains Qwen/Qwen2.5-0.5B-Instruct for 5 steps on a tiny synthetic dataset, then
# verifies the adapter lands in the registry and resolves as a local $0 model.
# Run from the repo root on the Spark:
#
#   pip install -e '.[training]'
#   bash scripts/validate_training_on_spark.sh
#
# Expected runtime: a few minutes (model download dominates the first run).
set -euo pipefail

python - <<'PY'
import asyncio
import time
from types import SimpleNamespace

from sandcastle.engine.adapter_registry import AdapterRegistry
from sandcastle.engine.providers import resolve_model
from sandcastle.engine.training.gpu_trainer import GPUTrainer

config = SimpleNamespace(
    lora_base_model_id="Qwen/Qwen2.5-0.5B-Instruct",
    lora_r=8,
    lora_alpha=16,
    lora_dropout=0.05,
    lora_lr=1e-4,
    lora_epochs=1,
    lora_max_steps=5,
    lora_seed=42,
    lora_batch_size=1,
    lora_grad_accum=1,
    lora_max_seq_len=512,
    lora_quantize="none",
    lora_output_dir="",  # -> ~/.sandcastle/adapters
)
pairs = [{"input": f"What is {i} + {i}?", "output": str(i + i)} for i in range(16)]

result = asyncio.run(GPUTrainer().train("spark-validation", pairs, config))
print(f"trained: {result.adapter_id}  metrics={result.metrics}")

reg = AdapterRegistry()
reg.register(
    result.adapter_id, result.base_model, result.metrics,
    result.samples, result.lora_config, created_at=time.time(),
)
assert reg.get(result.adapter_id) is not None, "adapter not registered"
adapter_dir = reg.get_path(result.adapter_id)
assert (adapter_dir / "adapter_model.safetensors").exists(), "no adapter weights written"

info = resolve_model(f"adapter/{result.adapter_id}")
assert info.region == "local" and info.input_price_per_m == 0.0

print(f"OK: adapter registered at {adapter_dir}")
print(f"Serve it:  vllm serve {result.base_model} --enable-lora "
      f"--lora-modules {result.adapter_id}={adapter_dir}")
PY
