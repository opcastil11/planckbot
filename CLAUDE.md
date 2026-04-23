# PlanckBot

**Adaptive tiny-model layer for LLM token optimization.** Sits between a host LLM (Claude, GPT-4, etc.) and its tools. Observes every tool call, trains a per-tool LoRA adapter (<1B params, SmolLM2 base), and at runtime filters / compresses / short-circuits tool I/O to save tokens. Concept doc: `docs/PLANCKBOT_CONCEPT.md` (PDF in the same dir).

Standalone product — not a dependency of Orquesta. Orquesta is only *one* ingest source among several.

## Repo layout

```
src/planckbot/
  db/          — SQLite schema + dataclasses (schema v2)
  ingest/      — TripleSource ABC + manual.py + orquesta.py
  tools/       — registry, triples store, builtin tools
  experiments/ — experiment manager + metrics
  training/    — LoRA trainer (HF Trainer + PEFT)
  models/      — loader, inference (`predict()` with confidence), checkpoints
  proxy/       — intercept layer + MCP stdio server (`planckbot-mcp`)
  ui/          — NiceGUI workbench (7 pages: dashboard, tools, experiments, training, models, mascots, paper-log)
  paper/       — research log + markdown export
scripts/       — train_smoke.py, train_tool.py, proxy_demo.py, mcp_preflight.py, md_to_pdf.py, auto_label.py
static/branding/ — PlanckBots logo + favicon (used by UI + empty states)
tests/         — 94 tests; full suite runs in ~5s (no torch needed for most)
data/          — SQLite DB + LoRA checkpoints (gitignored except data/fixtures/)
```

## Tech stack

- Python 3.12, uv-managed venv at `.venv/`
- SQLite (local, schema in `db/migrations.py`)
- PyTorch CPU + transformers + peft + datasets for training
- NiceGUI (FastAPI) for the workbench UI
- `mcp` SDK for the proxy/stdio server
- No test framework beyond pytest; asyncio mode auto

## Running things

| Command | What it does |
|---|---|
| `.venv/bin/python -m pytest` | Full test suite (94 tests, ~5s) |
| `.venv/bin/python -m planckbot` | Launch workbench UI on port 8080 |
| `.venv/bin/python scripts/train_smoke.py` | Real LoRA training on file_search fixture (~10 min CPU) |
| `.venv/bin/python scripts/train_tool.py --tool X --fixture Y.json [--activate]` | Train a LoRA on any tool/fixture combo |
| `.venv/bin/python scripts/proxy_demo.py` | Exercise intercept path with trained adapter |
| `.venv/bin/python scripts/mcp_preflight.py` | Verify planckbot-mcp wraps filesystem MCP |
| `cat msg.txt \| .venv/bin/python scripts/auto_label.py --tool X --recent N` | Back-fill `filtered_output` on unlabeled triples from a reference text |
| `.venv/bin/python scripts/md_to_pdf.py <in.md> <out.pdf>` | Regenerate concept-doc PDF |
| `.venv/bin/planckbot-mcp --mode observe -- CMD ARGS` | Run the MCP stdio proxy |

Dev deps: `uv pip install -e ".[dev]"` (inside the venv).

## Rules

- **No torch at import time** outside `training/` and `models/`. Keep test startup fast.
- **Schema changes** go through `migrate_to_v2()` style upgrades. Additive only unless explicitly discussed. Every change bumps `SCHEMA_VERSION`.
- **UI layout changes** use the component library: `page_header`, `empty_state`, `stat_card`, `status_badge`. Don't inline style strings for titles/sections — use `theme.heading_style(...)` etc.
- **Dashboard and Tools pages auto-refresh** every 3s via `_page_wrapper(..., live_seconds=3.0)` / a `ui.timer` wrapping `refresh_triples`. Pages with form state (Training, Experiments, Paper Log) must NOT be wrapped in `live_seconds` because the rebuild wipes input/selector values — add surgical `ui.timer`s on just the display section instead.
- **Colors** come from `ui/theme.py :: COLORS`. Don't hardcode hex values in pages.
- **Mascots are procedural.** Do not add image files per tool — the SVG generator in `ui/mascots.py` is deterministic by hash(tool_name). Same name → same bot.
- **Build the PDF only when the concept doc changes.** Chrome headless renders the PDF; don't add weasyprint/pandoc.

## Key design decisions (already made)

- **Brand**: "PlanckBots" (plural) as the product name; "PlanckBot" remains the system/singular. Palette is teal-green + brass + ocean-ink (see `ui/theme.py :: COLORS`).
- **Three adaptation layers** (concept doc §7):
  - A = host LLM picks the tool
  - B = Planck model filters I/O at runtime ← what PlanckBot does
  - C = host LLM edits tool source code → triggers version bump + adapter invalidation
  Schema v2 added `tool_versions` table + `tool_version_id` columns on `triples` and `model_checkpoints` to support this. The meta-tool `edit_tool(name, patch)` is **not yet implemented** — only the data model.
- **MCP integration** — `planckbot-mcp` wraps an upstream MCP server. CLI uses `-- cmd args...` positional for upstream (argparse rejects dash-prefixed values otherwise). Three modes: `observe` (log only), `suggest` (log + predict), `intervene` (apply when confidence ≥ threshold). Side-effect tools (`Write`, `Edit`, `Bash`) never get their output swapped regardless of mode.
- **Training**: SmolLM2-135M-Instruct + LoRA r=8 targeting q_proj/v_proj. ~460K trainable params (0.34%). CPU-feasible but slow (~10 min for 16 triples × 1 epoch).
- **Confidence**: geometric mean of per-token top probability (`exp(mean(log p_i))`) from `models/inference.predict()`. Not calibrated yet — needs the evaluator to tune thresholds.

## Where things are in the DB (at `data/planckbot.db`)

- 1 experiment (`smoke-...`), 1 checkpoint (`file_search_SmolLM2-135M-Instruct`), ~30 triples including a mix of manual fixture data and proxy-recorded calls
- Checkpoint adapter on disk at `data/checkpoints/<uuid>/adapter_model.safetensors` (1.86 MB)

## MCP integration — currently wired into Claude Code

`~/.claude.json` has a `planckbot-fs` MCP server block pointing at the orquesta repo in `observe` mode. After Claude Code restarts it will log every filesystem tool call as a triple. To inspect live data:

```sh
sqlite3 data/planckbot.db \
  "SELECT tool_name, source, datetime(created_at, 'localtime') FROM triples WHERE source LIKE 'proxy:%' ORDER BY created_at DESC LIMIT 20"
```

## Workflow (after every change)

1. Run tests: `.venv/bin/python -m pytest`
2. If UI changed: restart `python -m planckbot` (reload=False, so code changes need a restart)
3. Commit (descriptive message) — repo is `opcastil11/planckbot` (private)
4. Push: `git push origin main`

## GitHub

Repo: **https://github.com/opcastil11/planckbot** (private). Auth via the `store` credential helper (`~/.git-credentials`). For `gh` CLI, pull the token with `git credential fill` and export as `GH_TOKEN=...` — the stored token has `repo` scope but not `read:org`, so `gh auth login` proper will reject it.

## Common issues

- **UI shows stale data** — NiceGUI runs with `reload=False`. Restart `python -m planckbot` after code changes.
- **Proxy CLI rejects dash-prefixed upstream args** — use `-- cmd args...` positional, not `--upstream-args=-y`.
- **Tests fail with TypeError on `tool_version_id`** — the running process pre-dates schema v2. Restart whatever's loading the dataclasses.
- **Port 8080 stuck** — kill: `pkill -f "planckbot$"` or check `ss -lnt '( sport = :8080 )'`.

## Layer C (tool-edit) — now wired

- `src/planckbot/tools/meta.py :: edit_tool(name, patch, registry, versions, checkpoints)` — applies a unified diff OR a full-source replacement to a registered tool's module file, runs an AST whitelist (no new imports, no `eval`/`exec`/`subprocess`/`os.system`/etc.), writes a new `tool_versions` row, deactivates any active checkpoint for that tool, and reloads the module so the registry picks up the new implementation.
- `src/planckbot/tools/versions.py :: ToolVersionStore` — CRUD over the `tool_versions` table (insert, get, latest, by_hash, list_for_tool, count).
- Tests live in `tests/test_meta.py` (15 tests).
- **Still pending:** invoking `edit_tool` from the meta-tool interface (it's currently a python API, not a registered tool that the proxy can dispatch). Also pending: cold-start window forcing observe mode until N new-version triples accumulate.

## Reference-tracking signal — partially wired

- `src/planckbot/ingest/reference_tracker.py :: extract_referenced_lines(output, reference)` returns output lines whose stripped form appears as a substring of the reference (min_line_len filter to skip `{`/`}`/`[` noise).
- `label_triple_from_reference(store, triple_id, reference, force=False)` writes `filtered_output` on a triple; no-op if already labeled unless `force=True`.
- `TriplesStore.list_unlabeled(tool_name, limit)` returns the newest N triples with NULL `filtered_output`.
- `scripts/auto_label.py` is the manual CLI: pipe in a reference text (the host LLM's message that quoted the tool output) and it labels the most recent N triples for a given tool.
- **Still pending:** an automated watcher that reads Claude Code's conversation JSONL files (`~/.claude/projects/<slug>/*.jsonl`) and back-labels triples on a schedule — this is what flips "more usage → better adapter" from manual to automatic.

## Not yet built (next plausible work)

- **Cold-start window** after a tool edit — force observe mode until N new-version triples accumulate.
- **Automated reference-tracking** from Claude Code conversation logs on a cron — so triples self-label as conversations happen.
- **Multi-adapter memory management** — at >20 tools, we can't load every adapter simultaneously. LRU eviction + shared base model with swappable LoRA adapters.
- **Workbench features**: scheduled retrain cadence, A/B comparison of adapter versions, adapter promotion flow (eval beats active → activate).
