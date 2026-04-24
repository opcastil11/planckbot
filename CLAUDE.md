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
  cron/        — background scheduler (CronStore, JobRegistry, Daemon, scanner)
  synth/       — Layer D: pattern detector, synthesize_tool, `planckbot-synth` MCP server
  cli.py       — `planckbot` entry point with subcommands (ui, status, train, label, cron, synth …)
  ui/          — NiceGUI workbench (10 pages: dashboard, how-it-works, tools, experiments, training, models, cron, synth, mascots, paper-log)
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
| `.venv/bin/python -m pytest` | Full test suite (241 tests, ~9s) |
| `.venv/bin/planckbot` | Launch workbench UI on port 8080 (bare command, back-compat) |
| `.venv/bin/planckbot status` | One-shot summary of triples / savings / checkpoints / cron |
| `.venv/bin/planckbot cron list|add|rm|enable|disable|run|daemon` | Manage scheduled jobs |
| `.venv/bin/planckbot cron daemon` | Start the blocking scheduler loop (file-locked) |
| `.venv/bin/planckbot synth list|show|activate|deactivate|create|gaps|author` | Manage Layer D synthesized tools (`author` uses Claude API when `ANTHROPIC_API_KEY` set) |
| `.venv/bin/planckbot-synth` | Run the Layer D MCP server (exposes active synthesized tools to Claude Code) |
| `.venv/bin/planckbot doctor [--json]` | 12-point health check; exit code 0/1/2 for ok/warn/fail |
| `.venv/bin/planckbot status [--watch N]` | One-shot or polling summary of DB state + cost estimate |
| `.venv/bin/planckbot bless <ckpt> [--threshold X] [--activate]` | Mark a trained checkpoint safe to serve |
| `.venv/bin/planckbot unbless <ckpt>` | Revoke blessed + deactivate — emergency when adapter regresses |
| `.venv/bin/planckbot demo load|clear` | Synthetic triples for populating an empty dashboard |
| `.venv/bin/planckbot uninstall [--yes] [--purge-data]` | Reverses `init`: strips MCP entries, removes systemd unit, optionally wipes data/ |
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
  Schema v2 added `tool_versions` table + `tool_version_id` columns on `triples` and `model_checkpoints`. The meta-tool `edit_tool(name, patch)` IS implemented in `src/planckbot/tools/meta.py` (Layer C) — 15 tests cover AST whitelist + version bump + adapter invalidation + module hot-reload.
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

## Secret filtering — `.mcpignore` at proxy layer

- `src/planckbot/proxy/ignore.py` enforces a blocklist **before** calls reach the upstream MCP server. Two gates: (1) `read_*` tools on blocked paths return a refusal string without invoking upstream; (2) `list_directory` / `directory_tree` / `search_files` outputs are redacted post-upstream — blocked entries never reach Claude.
- Hard defaults cover dotenv / SSH keys / cloud creds / netrc / service accounts / kube+docker config. See `HARD_DEFAULTS` in the module. These are enforced even without a `.mcpignore`.
- Custom rules live in `.mcpignore` at the served-path root (fnmatch globs, `#` comments, trailing `/` = directory). Full reference in `docs/MCPIGNORE.md`.
- Verified end-to-end: calling `read_text_file` on orquesta's `.env.local` returns "Refused by PlanckBot .mcpignore policy"; `list_directory` of orquesta strips `.env.local` + `.env.local.bak`.

## Blessed-checkpoint safety gate (schema v5)

- `model_checkpoints` has `blessed INTEGER NOT NULL DEFAULT 0` and `tuned_threshold REAL NULL` (v5, commit `6fb41ee`).
- `CheckpointManager.activate()` raises `ValueError("not blessed")` for unblessed rows unless the caller passes `require_blessed=False`. Tests do so explicitly; production paths (CLI, UI, `scripts/train_tool.py --activate`) never pass it — they call `bless()` first.
- Operator flow: `planckbot train → planckbot proxy-demo` (eval honestly) → `planckbot bless <ckpt> [--threshold X] [--activate]`. The training script's `--activate` bless+activates in one step but prints a loud warning that the operator has asserted compression.
- `planckbot unbless <ckpt>` is the emergency revert when a previously-trusted adapter is found to regress in production.

## Pricing (token-savings cost estimate)

`src/planckbot/pricing.py` holds a curated table of per-million input-token prices (Claude Opus/Sonnet/Haiku, GPT-5, GPT-4o mini). `estimate_cost(tokens, model_id)` returns a `PriceQuote` with a pretty-formatted `cost_str` that switches between millicents / cents / USD by magnitude. Override the default with `PLANCKBOT_PRICING_MODEL=<id>`. The dashboard's `savings_widget` exposes a model dropdown that re-renders the dollar figure in place.

## Layer D — tool synthesis from usage (now wired)

End-to-end: triples → pattern detector → gap report → human (or LLM) approves → synthesize_tool writes code + row → activate → `planckbot-synth` MCP server serves it to Claude Code.

- `src/planckbot/synth/detector.py` — `find_tool_sequences(triples, window_seconds, min_occurrences)` returns N-grams of tool names that repeat inside a time window. `build_gap_reports(conn, matches)` upserts them into the `gap_reports` table (dedups by sequence). The cron job type `detect_tool_gaps` wraps this.
- `src/planckbot/synth/meta.py` — `synthesize_tool(name, description, input_schema, code, conn, ...)` AST-whitelists the code (same forbidden imports/names/attrs as `edit_tool` but *allows* new imports because a new tool has no prior surface), writes it to `data/synthesized_tools/<name>.py`, inserts `synthesized_tools` row with `status='draft'`. `activate_tool(name)` flips to `status='active'` and SIGHUPs the running MCP server (pid at `/tmp/planckbot-synth.pid`) so the new catalog is picked up without restart.
- `src/planckbot/synth/mcp_server.py` — stdio MCP server. On startup loads every `status='active'` row; on SIGHUP re-reads the table. Advertised tools wrap the stored `input_schema` into a JSON Schema object; dispatches by exec-ing the stored code in a fresh namespace and calling the top-level function.
- Register it in `~/.claude.json` as `"planckbot-synth": {"command": "/abs/path/.venv/bin/planckbot-synth"}` alongside `planckbot-fs`.
- Schema v4 added `synthesized_tools` + `gap_reports` tables. Purely additive.

## Cron / job scheduler — now wired

- `src/planckbot/cron/` has `CronStore` (CRUD on `cron_jobs`, schema v3), a `JobRegistry` with built-in types `noop` / `autolabel` / `retrain` / `conversation_scanner`, and `Daemon` (blocking polling loop, file-locked at `/tmp/planckbot-cron.lock` so only one instance runs).
- Dashboard → `/cron` lists all jobs, lets you toggle/run/delete them, and ships a "create job" form. Live-refreshes every 3s.
- Run the scheduler with `.venv/bin/planckbot cron daemon` (systemd user unit is a good next step; not included yet).
- `retrain` job is a "ready to retrain" signal, not a trigger — it prints the command to run. Firing a full LoRA retrain inside the daemon would block the scheduler for ~10 min and isn't worth the complexity right now. Wire it to a worker if/when that changes.
- `conversation_scanner` reads `~/.claude/projects/<slug>/*.jsonl` and extracts recent assistant text blocks into a reference file. Pipe it through `autolabel` (via its own cron job with the same output path) and the loop "use Claude → triples get filtered_output" becomes fully automatic. Params: `output_path` (required), `project_slug` (default: slugified cwd), `max_messages` (default 30), `lookback_hours` (default 24).

## Per-triple auto-labeling — now wired (Layer B loop closed)

- `src/planckbot/cron/scanner.py :: find_response_after_tool_call(jsonl_path, tool_name, input_data, near_ts)` locates the exact `tool_use` block in a Claude Code JSONL matching a given triple (namespaced names like `mcp__planckbot-fs__list_directory` accepted) and returns the text of the NEXT assistant message that followed its `tool_result`. Disambiguates by input key equality when the same tool was called multiple times.
- Cron job type `autolabel_precise` wraps this: for each unlabeled triple of a tool, finds the exact follow-up message and labels using ONLY that text. Eliminates the batch-reference false positives the older `autolabel` suffers from.
- `ingest.reference_tracker.extract_referenced_lines` gained a `match_mode` kwarg. The new default is `"token"` — splits line and reference on non-word chars and keeps the line if they share any non-structural word. `"substring"` is kept for back-compat. Real-world smoke test with this matcher labeled a `list_directory` triple at 97% savings (230 → 8 tokens) with zero false positives.

## Reference-tracking signal — partially wired

- `src/planckbot/ingest/reference_tracker.py :: extract_referenced_lines(output, reference)` returns output lines whose stripped form appears as a substring of the reference (min_line_len filter to skip `{`/`}`/`[` noise).
- `label_triple_from_reference(store, triple_id, reference, force=False)` writes `filtered_output` on a triple; no-op if already labeled unless `force=True`.
- `TriplesStore.list_unlabeled(tool_name, limit)` returns the newest N triples with NULL `filtered_output`.
- `scripts/auto_label.py` is the manual CLI: pipe in a reference text (the host LLM's message that quoted the tool output) and it labels the most recent N triples for a given tool.
- **Still pending:** an automated watcher that reads Claude Code's conversation JSONL files (`~/.claude/projects/<slug>/*.jsonl`) and back-labels triples on a schedule — this is what flips "more usage → better adapter" from manual to automatic.

## Not yet built (next plausible work)

- **Cold-start window** after a tool edit — force observe mode until N new-version triples accumulate (Layer C hand-off gap).
- **Semantic matcher** for auto-label — replace the word-level matcher in `reference_tracker` with a sentence-embedding cosine similarity (paper §6.1).
- **LLM-authored Layer D** — `synthesize_tool --from-gap <report_id>` that calls Claude API with the example triple IDs as context and gets a Python body back (paper §7).
- **Confidence calibration** per tool — tune the intervene threshold against a held-out eval set instead of using the global default of 0.9 (paper §6.2). Schema v5 already has `tuned_threshold` per-checkpoint; the tuning command is not built.
- **Multi-adapter LRU** — shared base model with swappable LoRA adapters, needed above ~20 tools (paper §7).
- **Sandboxed execution of synthesized tools** — Firecracker microVM or seccomp-filtered subprocess (paper §6.4).
- **Multi-adapter memory management** — at >20 tools, we can't load every adapter simultaneously. LRU eviction + shared base model with swappable LoRA adapters.
- **Workbench features**: scheduled retrain cadence, A/B comparison of adapter versions, adapter promotion flow (eval beats active → activate).
