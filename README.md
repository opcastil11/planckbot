# PlanckBot

**An adaptive tiny-model layer for LLM token optimization.**

PlanckBot sits between a host LLM (Claude, GPT-4, any MCP-capable agent) and its tools. It observes every tool call, trains a per-tool LoRA adapter on the observed I/O, and at runtime filters tool output down to the fragments the LLM actually cites — cutting context tokens by up to two orders of magnitude on verbose tools like `list_directory`, `read_file`, and `search_files`.

**Paper**: [docs/PLANCKBOT_PAPER.pdf](docs/PLANCKBOT_PAPER.pdf) · [markdown](docs/PLANCKBOT_PAPER.md)
**Concept doc**: [docs/PLANCKBOT_CONCEPT.md](docs/PLANCKBOT_CONCEPT.md)

---

## What makes it different

PlanckBot is organized as four adaptation layers:

| Layer | What it does | Implementation |
|---|---|---|
| **A** — tool selection | The host LLM picks the tool. PlanckBot does not intervene here. | (host LLM's own policy) |
| **B** — runtime I/O filtering | A per-tool tiny LLM (SmolLM2-135M + LoRA rank 8) compresses tool output before it reaches the host LLM. | `src/planckbot/proxy/`, `src/planckbot/models/` |
| **C** — tool source editing | The host LLM can propose patches to a registered tool's source. AST whitelist guards against unsafe code. New version invalidates stale adapters. | `src/planckbot/tools/meta.py` |
| **D** — tool synthesis | A pattern detector watches for repeated tool sequences and proposes new merged tools. Accepted tools are hot-loaded via a dedicated MCP server. | `src/planckbot/synth/` |

All four layers share one data unit: a **triple** `(input, output, filtered_output)`. The `filtered_output` field is populated automatically by reading Claude Code's conversation logs and matching lines from the tool's output against the assistant's next reply — no manual labeling required.

## Architecture at a glance

```
  host LLM (Claude / GPT-4 / ...)
       │ MCP JSON-RPC
       ▼
  planckbot-fs ──→ upstream MCP server  (Layer B: observe / filter)
       │
       ▼
   SQLite (triples, adapters, tool_versions, cron_jobs, synthesized_tools)
       │
       ├──→ cron daemon (autolabel, detect_gaps, scanner, retrain)
       ├──→ NiceGUI workbench (10 pages: dashboard, tools, training, cron, synth, ...)
       └──→ planckbot-synth MCP server (Layer D: serves synthesized tools)
```

Everything runs locally. No external services. No GPU required — a trained adapter is ~10 MB and inference is <50 ms on CPU.

## Quick start

```bash
git clone https://github.com/opcastil11/planckbot
cd planckbot
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# run the test suite (should pass in ~10s)
python -m pytest

# see what's in the box
planckbot status
planckbot ui           # workbench at http://localhost:8080
```

## Wiring to Claude Code

Add the MCP servers to `~/.claude.json`:

```json
{
  "mcpServers": {
    "planckbot-fs": {
      "command": "/abs/path/.venv/bin/planckbot-mcp",
      "args": [
        "--mode", "observe",
        "--name", "planckbot-fs",
        "--",
        "npx", "-y", "@modelcontextprotocol/server-filesystem",
        "/abs/path/to/your/project"
      ]
    },
    "planckbot-synth": {
      "command": "/abs/path/.venv/bin/planckbot-synth"
    }
  }
}
```

Restart Claude Code. The filesystem tools now appear as `mcp__planckbot-fs__*` and every call is recorded in the local SQLite DB.

## The self-supervising loop

1. Claude calls a tool via `mcp__planckbot-fs__*` → proxy records a triple.
2. `conversation_scanner` cron job reads Claude Code's JSONL log for recent assistant text.
3. `autolabel_precise` cron job matches each unlabeled triple to the reply immediately following its tool call and writes `filtered_output`.
4. When enough labeled triples accumulate, the adapter for that tool is retrained.
5. `detect_tool_gaps` cron job flags repeated N-gram tool sequences as candidates for Layer D synthesis.

Start the loop with:

```bash
# systemd user unit, keeps running across reboots
systemctl --user enable --now planckbot-cron.service

# or ad-hoc
planckbot cron daemon
```

## CLI reference

```
planckbot                     → launches the UI (back-compat default)
planckbot status              → one-shot summary
planckbot ui                  → launch NiceGUI workbench

planckbot train --tool X --fixture Y.json [--activate]
planckbot label --tool X --recent N [--reference file.txt] [--dry-run]

planckbot cron list|add|rm|enable|disable|run|daemon
planckbot synth list|show|activate|deactivate|create|gaps

planckbot proxy-demo          → exercise the intercept path with a trained adapter
planckbot preflight           → sanity-check the MCP wrapper + upstream
```

## What works today

- [x] MCP proxy observing every filesystem tool call
- [x] Per-triple auto-labeling from Claude Code JSONL logs
- [x] LoRA adapter training on consumer CPU (~10 min per tool, 16–100 triples)
- [x] Layer C edit_tool with AST whitelist and version tracking
- [x] Layer D pattern detector + synthesize_tool primitive + hot-reload MCP server
- [x] NiceGUI workbench with live dashboard, tool browser, training chart, cron manager, and a `/how-it-works` onboarding page
- [x] systemd user unit for persistent scheduler
- [x] 195 tests, <10s full run

## What's honest work-in-progress

- Preliminary adapters underfit at 16 training triples — expect ~200–500 per tool for net savings.
- Token-savings accounting is currently negative (–55% across 6 historical intervene calls) because the early adapters regressed. See the paper §5.2.
- Layer D currently requires a human (or external LLM call) to write the synthesized tool's code body. The framework, AST gate, MCP hot-serve, and gap detector are all live.

## Project layout

```
src/planckbot/
  db/          — SQLite schema + dataclasses (v4)
  ingest/      — TripleSource ABC, manual loader, reference_tracker
  tools/       — ToolRegistry, TriplesStore, meta.edit_tool, versions
  experiments/ — ExperimentManager + metrics
  training/    — LoRA trainer (HF Trainer + PEFT)
  models/      — loader, inference (predict() with confidence), CheckpointManager
  proxy/       — intercept layer + planckbot-mcp stdio server
  synth/       — Layer D: detector, meta.synthesize_tool, planckbot-synth MCP server
  cron/        — CronStore, JobRegistry, Daemon, scanner
  cli.py       — unified planckbot CLI
  ui/          — NiceGUI workbench (10 pages)
scripts/       — train_tool, auto_label, proxy_demo, mcp_preflight, md_to_pdf
tests/         — 195 tests, ~10s
docs/          — concept doc + paper (markdown + PDF)
data/          — SQLite DB + LoRA checkpoints (gitignored except fixtures/)
static/        — branding + UI assets
```

## Dev workflow

```bash
python -m pytest         # test suite
planckbot ui             # restart UI after code changes (reload=False)
python scripts/md_to_pdf.py docs/X.md docs/X.pdf   # regen PDFs when concept/paper change
```

## License

Apache 2.0. See [LICENSE](LICENSE).

## Citation

If you use PlanckBot or build on its framework, please cite:

```
@misc{castillo2026planckbot,
  title  = {PlanckBot: An Adaptive Tiny-Model Layer for Tool-Use Token Optimization},
  author = {Castillo, Oscar},
  year   = {2026},
  url    = {https://github.com/opcastil11/planckbot}
}
```

## Acknowledgments

Implementation co-authored with Claude Opus 4.7. Claude is also the first inadvertent supervision source for PlanckBot's adapters.
