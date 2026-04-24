# Contributing to PlanckBot

Thanks for your interest. PlanckBot is a research system with a production-intent implementation — contributions that make it easier to collect empirical evidence are especially welcome.

## Before you start

Skim the `/how-it-works` page in the workbench (`planckbot ui`). The four-layer framework is the load-bearing abstraction; most changes touch one layer, and being explicit about which one keeps review fast.

## Dev setup

```bash
git clone https://github.com/opcastil11/planckbot
cd planckbot
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

python -m pytest -q          # must pass before you open a PR
planckbot ui                 # http://localhost:8080
planckbot status             # sanity check
```

## Code conventions

- **Python 3.10+** (CI runs 3.10, 3.11, 3.12)
- **No torch at import time** outside `training/` and `models/`. Test startup must stay fast (current full-suite run is ~10 s).
- **Absolute imports** (`from planckbot.X import Y`). Prefer module-level clarity over local imports *except* where lazy loading dodges heavy dependencies (torch, mcp).
- **Tests**: colocated under `tests/`, pytest only. Aim for one test per public function and at least one regression test for every bug you fix.
- **UI changes**: use the component library (`page_header`, `empty_state`, `stat_card`, `status_badge`) and the colors from `ui/theme.py :: COLORS`. Don't hardcode hex values in pages.

## Schema changes

The SQLite schema is versioned in `src/planckbot/db/migrations.py`. Additive changes (new tables / new nullable columns) are fine. Destructive changes (rename, drop, non-null backfill) need explicit discussion in the PR — we do not ship down-migrations.

Every change must:
1. Bump `SCHEMA_VERSION`.
2. Add an `_upgrade_to_vN` function for existing databases.
3. Add or update the dataclass in `db/models.py`.
4. Add or update the store in `tools/`, `cron/`, `synth/`, etc.
5. Include a test that exercises the migration on a v(N-1) DB snapshot.

## Cron job types

A new cron job type is a single function of signature `(ctx: JobContext) -> str`. Register it in `src/planckbot/cron/jobs.py :: default_registry()` and add tests that exercise both the happy path and at least one error path. See `_autolabel_precise_job` for a representative example.

## Synthesized tools

Code that runs as a synthesized tool goes through the same AST whitelist used by `edit_tool` (see `src/planckbot/tools/meta.py`). Do **not** weaken the gate casually. If you have a legitimate need for a new import, open an issue first.

## PR checklist

- [ ] `python -m pytest -q` passes locally
- [ ] New code has tests (unit or regression)
- [ ] Any schema change includes a migration
- [ ] Public API changes are reflected in `CLAUDE.md` + `README.md`
- [ ] The PR title follows the existing commit conventions (`feat(area):`, `fix(area):`, `docs:` …)

## Reporting bugs / feature requests

Use the GitHub issue templates. For security concerns (e.g., a way to escape the AST gate), please open a security advisory on GitHub rather than a public issue.

## License

By contributing, you agree that your work will be licensed under the Apache 2.0 License that covers the project (see `LICENSE`).
