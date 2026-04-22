"""Load/unload HuggingFace models with memory tracking."""

import gc
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class LoadedModel:
    name: str
    model: Any
    tokenizer: Any
    device: str
    load_time_s: float
    memory_mb: float


_loaded_models: dict[str, LoadedModel] = {}


def _get_memory_mb() -> float:
    """Get current process memory usage in MB."""
    try:
        import psutil
        import os
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def load_model(
    model_name: str,
    device: str = "cpu",
    adapter_path: str | None = None,
    trust_remote_code: bool = True,
) -> LoadedModel:
    """Load a HuggingFace model + tokenizer, optionally with LoRA adapter."""
    if model_name in _loaded_models:
        return _loaded_models[model_name]

    from transformers import AutoTokenizer, AutoModelForCausalLM

    mem_before = _get_memory_mb()
    t0 = time.time()

    tokenizer = AutoTokenizer.from_pretrained(
        model_name, trust_remote_code=trust_remote_code
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name, trust_remote_code=trust_remote_code
    )

    if adapter_path:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter_path)

    model = model.to(device)
    model.eval()

    load_time = time.time() - t0
    memory_used = _get_memory_mb() - mem_before

    loaded = LoadedModel(
        name=model_name,
        model=model,
        tokenizer=tokenizer,
        device=device,
        load_time_s=round(load_time, 2),
        memory_mb=round(max(0, memory_used), 1),
    )
    _loaded_models[model_name] = loaded
    return loaded


def unload_model(model_name: str):
    """Unload a model and free memory."""
    if model_name in _loaded_models:
        loaded = _loaded_models.pop(model_name)
        del loaded.model
        del loaded.tokenizer
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def get_loaded() -> dict[str, LoadedModel]:
    return dict(_loaded_models)


def unload_all():
    for name in list(_loaded_models.keys()):
        unload_model(name)
