# Overnight Self-Tune on a DGX Spark — real LoRA training

Sandcastle's evolution loop can fine-tune a **task-specific LoRA adapter** on a workflow's
own eval data and route the workflow to it — for $0, entirely on-box. This page covers the
real GPU path on an NVIDIA DGX Spark (GB10 Grace-Blackwell, ~128 GB unified memory).

## The loop

```
nightly evolution run (mutate_finetune, every 5th iteration)
        │ builds (input, output) pairs from the workflow's eval results
        ▼
GPUTrainer — LoRA SFT with transformers + peft (bf16, prompt-masked loss)
        │ writes adapter weights + dataset.jsonl + training_metadata.json
        ▼
AdapterRegistry — ~/.sandcastle/adapters/<adapter_id>/ (metadata.json)
        │ resolve_model("adapter/<id>") → local, $0.00/run
        ▼
local model server (vLLM / Ollama / NIM) serves base model + adapter
        │
        ▼
workflow steps now call adapter/<id>; next eval scores feed the next night
```

Sandcastle **trains and registers** adapters; it does **not serve** them. Serving is the
local OpenAI-compatible server's job.

## Enable it

```bash
pip install 'sandcastle-ai[training]'    # torch, transformers, peft, datasets, accelerate

export TRAINER_BACKEND=gpu               # default "mock" = deterministic, no GPU needed
export EVOLUTION_AUTO_FINETUNE=true      # opt-in: every 5th evolution iteration fine-tunes
export LORA_BASE_MODEL_ID="Qwen/Qwen2.5-7B-Instruct"   # HF model actually fine-tuned
```

Key knobs (all `settings.lora_*`, env-overridable): `LORA_R`, `LORA_ALPHA`, `LORA_DROPOUT`,
`LORA_LR`, `LORA_EPOCHS`, `LORA_MAX_STEPS` (0 = epochs decide), `LORA_SEED`,
`LORA_BATCH_SIZE`, `LORA_GRAD_ACCUM`, `LORA_MAX_SEQ_LEN`, `LORA_QUANTIZE`,
`LORA_OUTPUT_DIR` (empty = `~/.sandcastle/adapters`).

## Serving the adapter (vLLM example)

The adapter must be loaded over the **same base model** it was trained on
(`base_model` in the adapter's `metadata.json`):

```bash
ADAPTER=~/.sandcastle/adapters/<adapter_id>
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --enable-lora \
  --lora-modules <adapter_id>=$ADAPTER
```

Then requests with `"model": "<adapter_id>"` hit the tuned adapter. Point Sandcastle's
local provider at the server (e.g. `NIM_BASE_URL=http://localhost:8000`). Ollama
alternatively accepts a `Modelfile` with an `ADAPTER` directive; NVIDIA NIM supports
multi-LoRA via `NIM_PEFT_SOURCE`.

## Sizing & quantization tradeoff

- **Default: bf16 base + LoRA, Qwen2.5-7B-Instruct.** ~15 GB weights + optimizer/activations
  fits trivially in 128 GB unified memory and avoids any aarch64 wheel roulette.
- **70B-class bases need quantization**: bf16 weights alone are ~140 GB. Set
  `LORA_QUANTIZE=4bit` (QLoRA via bitsandbytes) and install `bitsandbytes` yourself —
  it is *not* part of the `[training]` extra because CUDA aarch64 wheels are not
  universally published; verify it imports on your DGX OS image first.
- On the Spark, install torch per NVIDIA's instructions for CUDA-on-aarch64 if the
  default PyPI wheel lacks CUDA support.

## Validate on the Spark

One command (trains Qwen2.5-0.5B for 5 steps, registers + resolves the adapter):

```bash
pip install -e '.[training]' && bash scripts/validate_training_on_spark.sh
```

## Reproducibility

The adapter id is a pure hash of (base model, hyperparameters, dataset sha256) —
re-training on unchanged data is idempotent. Each adapter dir contains `dataset.jsonl`
(the exact chat-format training set, one `{"messages": [...]}` per line) and
`training_metadata.json` (hyperparams, dataset hash, eval loss, library versions).
