"""Generate a large varied fixture for `list_directory` training.

We synthesize realistic directory listings by sampling from:
  - a large pool of "noise" entries (gitignored / generated / vendored)
  - a smaller pool of "signal" entries (source, docs, config, tests)

The `filtered_output` target keeps every signal entry and drops every
noise entry in the order the underlying `list_directory` would have
produced. This gives the adapter a clean line-selection task it has
any chance of learning at LoRA-rank-8.

Writes `data/fixtures/list_directory_triples.json`. Reversible (just
regenerate).
"""

from __future__ import annotations

import json
import random
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / "fixtures" / "list_directory_triples.json"

# Entries the adapter should learn to DROP.
NOISE_DIRS = [
    ".git", ".venv", ".pytest_cache", "__pycache__", ".mypy_cache",
    ".ruff_cache", "node_modules", "dist", "build", ".next", ".turbo",
    ".nx", "coverage", "tmp", "logs", ".cache", ".ipynb_checkpoints",
    "wandb", "mlruns", "target", "vendor", "bin", "Pods",
    ".terraform", ".terragrunt-cache", "htmlcov", ".tox", ".nyc_output",
    ".parcel-cache", ".svelte-kit", ".angular", ".vite", ".eslintcache",
    ".gradle", ".idea", ".vscode",
]
NOISE_FILES = [
    ".gitignore", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "uv.lock", "Pipfile.lock", "poetry.lock", "Cargo.lock",
    "go.sum", "composer.lock", "Gemfile.lock", ".DS_Store",
    "terraform.lock.hcl", ".env", ".env.local", ".env.local.bak",
]

# Entries the adapter should KEEP.
SIGNAL_DIRS = [
    "src", "app", "apps", "pkg", "packages", "tests", "test", "tests",
    "docs", "doc", "public", "static", "content", "layouts", "pages",
    "components", "lib", "libs", "modules", "ios", "android", "cmd",
    "internal", "services", "models", "api", "routes", "controllers",
    "views", "templates", "migrations", "dags", "plugins", "notebooks",
    "scripts", "terraform", "ansible", "k8s", "infra", "envs",
    "examples", "demos", "tools",
]
SIGNAL_FILES = [
    "README.md", "README.rst", "pyproject.toml", "setup.cfg", "setup.py",
    "package.json", "tsconfig.json", "next.config.js", "vite.config.ts",
    "Dockerfile", "docker-compose.yml", "Makefile", "Cargo.toml",
    "go.mod", "requirements.txt", "gatsby-config.js", "turbo.json",
    "metro.config.js", "airflow.cfg", "CLAUDE.md", "CONTRIBUTING.md",
    "LICENSE", "CHANGELOG.md", "CODEOWNERS", ".prettierrc", ".eslintrc.json",
    "vercel.json", "netlify.toml", "Procfile",
]

# Plausible "paths" for synthetic inputs.
PATHS = [
    "/home/dev/work/{name}",
    "/opt/{name}",
    "/workspace/{name}",
    "/repo/{name}",
    "/code/{name}",
    "/srv/{name}",
    "/src/{name}",
]
NAMES = [
    "api", "web", "frontend", "backend", "mobile", "admin", "cli",
    "worker", "ingest", "service", "dashboard", "monorepo", "lib",
    "data-pipeline", "ml-pipeline", "etl", "auth-svc", "billing",
    "payments", "analytics", "reporting", "crm", "chat", "search",
    "cdn", "portal", "marketing-site", "docs-site", "extension",
]


def _render_listing(dirs: list[str], files: list[str]) -> str:
    lines = [f"[DIR] {d}" for d in dirs] + [f"[FILE] {f}" for f in files]
    return "\n".join(lines)


def _pick(pool: list[str], n: int, rng: random.Random) -> list[str]:
    """Pick n distinct items, preserving pool order (so the listing
    stays 'alphabetical' enough to look real)."""
    if n > len(pool):
        n = len(pool)
    picked = rng.sample(pool, n)
    # Re-sort by pool position to keep a listing-like order.
    order = {item: i for i, item in enumerate(pool)}
    picked.sort(key=lambda x: order[x])
    return picked


def generate(count: int = 200, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    triples = []
    # Keep 16 seed hand-curated examples from the previous fixture intact
    # at the start so git diff shows what got added.
    for i in range(count):
        name = rng.choice(NAMES) + (f"-{i}" if i >= len(NAMES) else "")
        path = rng.choice(PATHS).format(name=name)

        # Each listing has 2-6 signal dirs + 1-4 signal files plus
        # 2-7 noise dirs + 0-4 noise files. Exact counts vary per row
        # so the adapter can't memorize a fixed length.
        signal_d = _pick(SIGNAL_DIRS, rng.randint(2, 6), rng)
        signal_f = _pick(SIGNAL_FILES, rng.randint(1, 4), rng)
        noise_d = _pick(NOISE_DIRS, rng.randint(2, 7), rng)
        noise_f = _pick(NOISE_FILES, rng.randint(0, 4), rng)

        # Merge in a roughly-sorted order.
        all_dirs = sorted(signal_d + noise_d)
        all_files = sorted(signal_f + noise_f)
        raw = _render_listing(all_dirs, all_files)
        kept = _render_listing(signal_d, signal_f)

        triples.append({
            "tool_name": "list_directory",
            "input_data": {"path": path},
            "output_data": raw,
            "filtered_output": kept,
        })

    return triples


if __name__ == "__main__":
    import sys
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    triples = generate(count)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(triples, indent=2))
    print(f"[fixture] wrote {len(triples)} triples to {OUT_PATH}")
    print(f"[fixture] size: {OUT_PATH.stat().st_size // 1024} KB")
