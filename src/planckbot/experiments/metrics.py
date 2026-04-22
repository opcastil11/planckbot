"""Metric computation: token savings, accuracy, token counting."""

import re


def count_tokens_approx(text: str) -> int:
    """Approximate token count (~4 chars per token for English)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def count_tokens_tiktoken(text: str, model: str = "gpt-4") -> int:
    """Count tokens using tiktoken."""
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except (ImportError, KeyError):
        return count_tokens_approx(text)


def compute_token_savings(original_tokens: int, filtered_tokens: int) -> float:
    """Returns savings as a percentage (0-100)."""
    if original_tokens <= 0:
        return 0.0
    saved = original_tokens - filtered_tokens
    return round((saved / original_tokens) * 100, 2)


def compute_accuracy_exact(reference: str, candidate: str) -> float:
    """Exact match accuracy (0 or 1)."""
    return 1.0 if reference.strip() == candidate.strip() else 0.0


def compute_accuracy_bleu(reference: str, candidate: str) -> float:
    """Simple BLEU-like score based on n-gram overlap."""
    ref_tokens = reference.lower().split()
    cand_tokens = candidate.lower().split()
    if not ref_tokens or not cand_tokens:
        return 0.0

    # Unigram precision
    ref_set = set(ref_tokens)
    matches = sum(1 for t in cand_tokens if t in ref_set)
    precision = matches / len(cand_tokens) if cand_tokens else 0

    # Brevity penalty
    bp = min(1.0, len(cand_tokens) / len(ref_tokens)) if ref_tokens else 0
    return round(bp * precision, 4)


def compute_accuracy(reference: str, candidate: str, method: str = "bleu") -> float:
    if method == "exact":
        return compute_accuracy_exact(reference, candidate)
    elif method == "bleu":
        return compute_accuracy_bleu(reference, candidate)
    return 0.0
