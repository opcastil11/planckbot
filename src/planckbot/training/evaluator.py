"""Evaluate a checkpoint on held-out triples."""

import json
import time
from dataclasses import dataclass

from planckbot.db.models import Triple
from planckbot.experiments.metrics import (
    compute_token_savings, compute_accuracy, count_tokens_approx,
)
from planckbot.models.inference import format_prompt, run_inference


@dataclass
class EvalResult:
    num_samples: int = 0
    avg_accuracy_exact: float = 0.0
    avg_accuracy_bleu: float = 0.0
    avg_token_savings: float = 0.0
    avg_latency_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_filtered_tokens: int = 0


def evaluate_checkpoint(
    model,
    tokenizer,
    triples: list[Triple],
    strategy: str = "filter_output",
    device: str = "cpu",
    max_new_tokens: int = 256,
) -> EvalResult:
    """Evaluate a model checkpoint against held-out triples."""
    if not triples:
        return EvalResult()

    exact_scores = []
    bleu_scores = []
    savings = []
    latencies = []
    total_in = 0
    total_out = 0
    total_filt = 0

    for t in triples:
        prompt = format_prompt(t.tool_name, t.input_data, strategy)

        # Reference output
        if strategy == "filter_output":
            reference = t.filtered_output or t.output_data
        else:
            reference = t.output_data

        result = run_inference(
            model, tokenizer, prompt,
            max_new_tokens=max_new_tokens,
            device=device,
        )

        exact_scores.append(compute_accuracy(reference, result.output_text, "exact"))
        bleu_scores.append(compute_accuracy(reference, result.output_text, "bleu"))

        ref_tokens = count_tokens_approx(reference)
        out_tokens = count_tokens_approx(result.output_text)
        orig_tokens = t.output_tokens or count_tokens_approx(t.output_data)

        if orig_tokens > 0:
            savings.append(compute_token_savings(orig_tokens, out_tokens))

        latencies.append(result.latency_ms)
        total_in += result.input_tokens
        total_out += orig_tokens
        total_filt += out_tokens

    n = len(triples)
    return EvalResult(
        num_samples=n,
        avg_accuracy_exact=round(sum(exact_scores) / n, 4) if exact_scores else 0,
        avg_accuracy_bleu=round(sum(bleu_scores) / n, 4) if bleu_scores else 0,
        avg_token_savings=round(sum(savings) / len(savings), 2) if savings else 0,
        avg_latency_ms=round(sum(latencies) / n, 2) if latencies else 0,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_filtered_tokens=total_filt,
    )
