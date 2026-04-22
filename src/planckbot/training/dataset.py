"""Build HuggingFace Datasets from triples for training."""

from planckbot.db.models import Triple
from planckbot.models.inference import format_prompt


def build_training_texts(
    triples: list[Triple],
    strategy: str = "filter_output",
) -> list[dict[str, str]]:
    """Convert triples into prompt/completion pairs based on strategy."""
    pairs = []
    for t in triples:
        prompt = format_prompt(t.tool_name, t.input_data, strategy)

        if strategy == "filter_output":
            completion = t.filtered_output or t.output_data
        elif strategy == "compress_input":
            completion = t.filtered_output or t.input_data
        elif strategy == "short_circuit":
            completion = t.output_data
        else:
            completion = t.output_data

        pairs.append({"prompt": prompt, "completion": completion})
    return pairs


def build_hf_dataset(
    triples: list[Triple],
    strategy: str = "filter_output",
    tokenizer=None,
    max_length: int = 512,
    test_size: float = 0.1,
):
    """Build a HuggingFace Dataset from triples, optionally tokenized."""
    from datasets import Dataset

    pairs = build_training_texts(triples, strategy)
    texts = [p["prompt"] + p["completion"] for p in pairs]

    ds = Dataset.from_dict({"text": texts})

    if tokenizer:
        def tokenize(examples):
            return tokenizer(
                examples["text"],
                truncation=True,
                max_length=max_length,
                padding="max_length",
            )
        ds = ds.map(tokenize, batched=True, remove_columns=["text"])

    if test_size > 0 and len(ds) >= 4:
        split = ds.train_test_split(test_size=test_size, seed=42)
        return split["train"], split["test"]

    return ds, None
