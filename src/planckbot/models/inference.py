"""Run inference, measure tokens and latency."""

import json
import math
import time
from dataclasses import dataclass


@dataclass
class InferenceResult:
    input_text: str
    output_text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


@dataclass
class PredictionResult:
    """Adapter prediction with a calibration-style confidence estimate."""
    output_text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    confidence: float  # 0..1, derived from mean per-token probability


def run_inference(
    model,
    tokenizer,
    prompt: str,
    max_new_tokens: int = 256,
    temperature: float = 0.1,
    device: str = "cpu",
) -> InferenceResult:
    """Run inference on a loaded model and measure performance."""
    import torch

    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=512)
    inputs = {k: v.to(device) for k, v in inputs.items()}
    input_len = inputs["input_ids"].shape[1]

    t0 = time.time()
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=max(temperature, 0.01),
            do_sample=temperature > 0,
            pad_token_id=tokenizer.pad_token_id,
        )
    latency = (time.time() - t0) * 1000

    output_ids = outputs[0][input_len:]
    output_text = tokenizer.decode(output_ids, skip_special_tokens=True)

    return InferenceResult(
        input_text=prompt,
        output_text=output_text,
        input_tokens=input_len,
        output_tokens=len(output_ids),
        latency_ms=round(latency, 2),
    )


def predict(
    model,
    tokenizer,
    prompt: str,
    max_new_tokens: int = 256,
    device: str = "cpu",
) -> PredictionResult:
    """Greedy prediction with a confidence score from per-token probabilities.

    Confidence is the geometric mean of the top-token probability at each step
    (i.e. exp(mean(log p_i))). This is a cheap, well-behaved [0, 1] estimator:
    - A sequence where every step assigned ~0.9 to the top token ≈ 0.9 overall.
    - A sequence where the model is uncertain at every step collapses to low.
    It's not perfectly calibrated — that comes from the evaluator's threshold
    tuning. But it's monotonic with "the model knew what to say," which is
    what the proxy needs for a gating decision.
    """
    import torch

    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=512)
    inputs = {k: v.to(device) for k, v in inputs.items()}
    input_len = inputs["input_ids"].shape[1]

    t0 = time.time()
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            num_beams=1,
            pad_token_id=tokenizer.pad_token_id,
            return_dict_in_generate=True,
            output_scores=True,
        )
    latency = (time.time() - t0) * 1000

    output_ids = outputs.sequences[0][input_len:]
    output_text = tokenizer.decode(output_ids, skip_special_tokens=True)

    # Confidence from per-step top-token probability.
    log_probs: list[float] = []
    for step_scores, chosen_id in zip(outputs.scores, output_ids):
        probs = torch.softmax(step_scores[0], dim=-1)
        p = float(probs[chosen_id].item())
        if p > 0:
            log_probs.append(math.log(p))
    confidence = math.exp(sum(log_probs) / len(log_probs)) if log_probs else 0.0

    return PredictionResult(
        output_text=output_text,
        input_tokens=input_len,
        output_tokens=int(output_ids.shape[0]),
        latency_ms=round(latency, 2),
        confidence=round(confidence, 4),
    )


def format_prompt(tool_name: str, input_data: str, strategy: str = "filter_output") -> str:
    """Format a prompt for the Planck model based on strategy."""
    if strategy == "filter_output":
        return (
            f"### Tool: {tool_name}\n"
            f"### Task: Filter the tool output to keep only relevant information.\n"
            f"### Input:\n{input_data}\n"
            f"### Filtered Output:\n"
        )
    elif strategy == "compress_input":
        return (
            f"### Tool: {tool_name}\n"
            f"### Task: Compress the input while preserving essential information.\n"
            f"### Original Input:\n{input_data}\n"
            f"### Compressed Input:\n"
        )
    elif strategy == "short_circuit":
        return (
            f"### Tool: {tool_name}\n"
            f"### Task: Predict the tool output without calling the tool.\n"
            f"### Input:\n{input_data}\n"
            f"### Predicted Output:\n"
        )
    else:
        return f"### Tool: {tool_name}\n### Input:\n{input_data}\n### Output:\n"
