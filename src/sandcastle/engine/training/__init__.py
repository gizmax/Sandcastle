"""LoRA fine-tune trainers for Overnight Self-Tune (Spark Mode).

The evolution loop's ``mutate_finetune`` trains a task-specific LoRA adapter on a
workflow's own eval data. Training is pluggable: a deterministic ``MockTrainer``
(default, no GPU - powers all tests/CI) or the real ``GPUTrainer`` (SFT via
transformers + peft) that needs the ``[training]`` extras + a CUDA GPU (e.g. a
DGX Spark). Pick via ``settings.trainer_backend``.
"""
