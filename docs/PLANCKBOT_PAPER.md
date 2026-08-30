# PlanckBot: An Adaptive Tiny-Model Layer for Tool-Use Token Optimization

**Oscar Castillo**<sup>1</sup>

<sup>1</sup> Independent researcher · https://github.com/opcastil11/planckbot

**Preprint · Draft v3 · 2026-08-29**

---

## Abstract

LLM agents burn a disproportionate share of context on raw tool output — directory listings, search dumps, JSON envelopes — of which only a fraction is ever referenced in the model's next step. We introduce **PlanckBot**, a system that observes tool traffic, learns from it, and intervenes to reduce that cost. The system is organized as four composable adaptation layers: tool **(A) selection**, **(B) runtime output filtering** by per-tool LoRA adapters on SmolLM2-135M, **(C) AST-guarded source editing** of existing tools, and **(D) synthesis of new tools** from repeated usage patterns. A single data unit — the triple `(input, output_raw, filtered_output)` — unifies all four and is populated automatically by linking each tool call to the assistant message that immediately follows it, requiring no human labeling.

**This revision retracts the central quantitative claim of v2.** Draft v2 reported **96.5 %** output-token reduction on a live `list_directory` call and presented it as evidence for Layer B. We have since (i) scaled the corpus from ~60 to **17,604 triples across 16 projects and 4.86 M output tokens**, (ii) received an independent third-party feasibility evaluation that rejected the runtime-compression deployment, and (iii) built a replay benchmark that evaluates **12 candidate techniques** against the corpus under two supervision signals. Measured with the honest signal, Layer-B compression yields **6.0 %**, not 96.5 %. The gap is a *citation-matcher artifact*: when the supervision reference is drawn from subsequent tool-call inputs rather than the assistant's actual text, almost any output line finds a spurious match. We characterize this artifact, show that two independent evaluations reproduced it, and argue it is a general hazard for citation-supervised tool-output compression.

The benchmark also relocates the value. Ranked by honest ceiling at zero content risk, the winners are **not compression but elimination**: predicting tool-call failure before execution (**45.1 %**), collapsing recurring tool N-grams into synthesized compound tools (**41.2 %**), and session-scoped caching of repeated reads (**13.3 %**). A fourth finding reframes the architecture: filesystem tools reachable through our MCP proxy account for **1.20 %** of real token volume, while native host tools that bypass the proxy account for **95.45 %**. We report the corpus, the benchmark, the retraction, an analysis of platform drift between April and August 2026 that closes part of the original roadmap and reopens the blocker that motivated the pivot, and a concrete three-track plan. Reference implementation (18.9 k LOC, 432 passing tests, schema v8) is released under Apache 2.0.

**Keywords**: LLM agents, tool use, Model Context Protocol, parameter-efficient fine-tuning, prompt compression, self-supervision, negative results, measurement artifacts.

---

## Revision note (v2 → v3)

This draft is a substantial revision, not an incremental update. Readers of v2 should note:

| v2 claim (2026-04-24) | v3 status |
|---|---|
| 96.5 % output-token reduction (§ 5.1) | **Retracted.** Single-call anecdote; the measurement instrument that produced it is shown in § 5.5 to be systematically inflating. Honest corpus-wide figure: 6.0 % (§ 5.4). |
| Per-triple linking beats batched matching (§ 5.2) | **Upheld and strengthened** — but reframed. Per-triple linking removes one artifact; § 5.5 shows a second, larger one remained. |
| LoRA adapters regress at ≤ 20 triples (§ 5.3, negative result) | **Upheld.** Untouched by the new evidence. |
| Break-even estimated at 200–500 labeled triples/tool (§ 5.4) | **Superseded.** The question is now moot for Layer B, whose ceiling is too low to justify the data collection. It remains live for the Layer-A-style classifiers of § 9. |
| Layer B is the system's primary value | **Reversed.** § 5.4 and § 5.6 relocate the value to elimination-class techniques. |
| Evaluation on filesystem MCP tools is representative | **Rejected.** Those tools are 1.20 % of measured token volume (§ 5.6). |

Sections 1–4 are revised but structurally continuous with v2. Sections 5, 6 and 7 are new. Section 9 replaces v2's Future Work entirely.

---

## 1. Introduction

### 1.1 Motivation

A single agentic session running `list_directory` on a 60-entry folder, `read_file` on three configs, and `search_files` for a pattern can easily burn 3,000 context tokens before the model writes a line of response. In the typical case the model cites three directory names, reads one dependency from `package.json`, and references one file path from the search — the remaining ≈ 90 % is noise the agent's future steps were never going to touch.

This waste has three compounding costs:

1. **Direct cost** against commercial APIs (price per 1 M input tokens).
2. **Context-window pressure** that forces premature compaction, paging, or session reset.
3. **Latency**, because every token in the context is re-attended at every subsequent generation step.

As agents run longer and tool catalogs grow, this cost grows super-linearly in the number of tool calls.

Existing work attacks either the *prompt* (context compression, LLMLingua and successors [1, 2]) or the *response* (post-hoc summarization, paging). Neither intervenes at the tool-I/O boundary, which is both (a) where most of the waste originates and (b) where the supervision signal needed to train the right filter is most abundant — because the host LLM's immediate reaction to a tool result *is* a natural label for which pieces of that result mattered.

That premise is the origin of PlanckBot, and this draft is in large part the record of testing it honestly and finding it insufficient on its own.

### 1.2 Contributions

This revision contributes:

- **A four-layer framework** (§ 3) decomposing LLM-tool adaptation into selection (A), runtime I/O filtering (B), tool-source editing (C), and tool synthesis (D), unified by the **triple**.
- **A production-scale observation corpus** (§ 5.1): 17,604 triples over 29 distinct tools, 16 projects and 4.86 M output tokens, harvested passively from real developer work, with automatic secret redaction at ingest.
- **A 12-technique replay benchmark** (§ 5.2–5.3) that evaluates candidate optimizations against that corpus under two supervision signals, reporting for each a token ceiling, a coverage figure, and an explicit *at-risk* fraction.
- **A characterized measurement artifact** (§ 5.5): the *citation-matcher artifact*, which inflates tool-output compression figures by more than an order of magnitude. We show it was independently reproduced by a third-party evaluation of our system (§ 6) and by our own benchmark, and we give the diagnostic that separates it from a real saving.
- **A retraction** (§ 5.4) of this paper's own v2 headline claim, with the corrected figure.
- **An empirical relocation of the value** (§ 5.6): elimination-class techniques dominate compression-class ones, and the tools our architecture could actually reach are ~1 % of the traffic that matters.
- **An analysis of platform drift** (§ 7) over four months of host-platform evolution, showing that three roadmap items were absorbed by the platform while the architectural blocker that motivated our pivot was simultaneously removed.
- **A reference implementation** (§ 4): MCP stdio proxy, SQLite storage (schema v8), JSONL mass-ingest, a NiceGUI workbench, a file-locked cron daemon, PreToolUse/PostToolUse hooks, a blessed-checkpoint safety gate, and the benchmark harness — 18.9 k LOC, 432 passing tests, Apache 2.0.

### 1.3 Scope

We describe a **systems contribution with a negative headline result**. We do not show PlanckBot saves dollars at production scale; v3 shows more precisely than v2 *why not yet*, and where the remaining value is. Readers looking for a benchmark-leading empirical claim will not find one. Readers interested in how a citation-supervised compression pipeline can measure itself into a 16× overestimate — and how to detect that — will find § 5.5 the load-bearing section.

### 1.4 A note on method

The corpus this paper analyzes was collected by the system the paper describes, during the development of the paper's own subject. Two consequences are worth stating up front. First, it is a convenience sample of one developer's workflow (§ 8.5). Second, it proved unexpectedly robust: when the v2 manuscript was deleted from the repository and from disk during a public-release history scrub, it was recovered in full from the `triples` table, reconstructed from the `Write` call that created it plus the six subsequent `Edit` calls. The observation layer was the only surviving backup. We note this less as a result than as an illustration of what a complete tool-traffic log is: an artifact of substantial and somewhat uncomfortable fidelity (§ 8.3).

---

## 2. Background and Related Work

### 2.1 Prompt and Context Compression

LLMLingua [1] and LongLLMLingua [2] use a small compressor model to prune tokens from long *prompts* before they reach the big model. Reported reductions of 5×–20× with preserved task performance are compelling but apply to a different locus than ours.

| | LLMLingua | PlanckBot |
|---|---|---|
| **Locus** | compress *prompt* before send | compress *tool output* before return |
| **Specialization** | one general compressor | one LoRA adapter per tool |
| **Supervision** | general corpora | host LLM's own next-message behavior |
| **Integration point** | client-side wrapper | MCP proxy / host hooks |

Tool outputs have strong structural regularities *per tool* — a `list_directory` listing looks nothing like an API response — that a general compressor cannot exploit. The supervision we harvest is essentially *free*: it already exists on disk as the host writes its conversation logs. Section 5.5 examines the price of that freeness.

### 2.2 Tool-Augmented LLMs

Toolformer [5], ToolLLM [6], Gorilla [7], and the Berkeley Function-Calling literature optimize the host LLM's ability to *select and invoke* tools correctly. PlanckBot began as an orthogonal effort operating *downstream* of selection. One of this revision's findings (§ 5.6) is that the highest-value interventions we measured are in fact *upstream* — deciding whether a call should happen at all — which moves our work closer to this literature than v2 anticipated.

### 2.3 Skill Libraries and Tool Synthesis

Voyager [8] builds a skill library by exploration; new skills are Python functions the LLM writes during play. PlanckBot's Layer D (§ 3.4) shares the form factor but differs in data source: we harvest candidate tools from *observed usage patterns* in real workflow, which is denser than sparse-reward exploration. Section 5.3 gives the first quantitative estimate of that mechanism's ceiling on real traffic (41.2 %), and § 9.2 identifies the adoption problem that stands between the ceiling and a realized saving.

### 2.4 MCP Proxies and LLM Observability

Existing MCP proxies (e.g., `mcp-proxy` [11]) are *transport bridges* that do not inspect or rewrite payloads. Observability platforms such as LangSmith, Langfuse and OpenLLMetry [12–14] record calls for debugging and evaluation but do not feed captured data back into training or runtime routing. To our knowledge PlanckBot remains the first MCP-layer proxy that trains models on observed traffic and serves them within the same protocol round-trip — though § 5.6 shows that the proxy vantage point sees far less of the traffic than we assumed.

### 2.5 Parameter-Efficient Fine-Tuning

LoRA [15] adds low-rank updates to a frozen base model's attention projections. Sub-billion-parameter base models (SmolLM2 [4], Phi-mini [16]) can produce useful task-specific adapters at rank 8 with under 500 k trainable parameters — a regime that fits on a CPU-only laptop. Industry reports on MCP token optimization [19, 20] demonstrate large reductions (96 %–100×) on specific MCP workloads with hand-engineered pruning. In v2 we cited these as evidence that a learned, per-tool version of the same reduction was attainable. In light of § 5.5 we now read them more carefully: reductions of that magnitude are routinely reported for *tool-definition* pruning and *catalog* pruning, where the saving is structural and verifiable, rather than for *tool-output* content filtering, where it is not. The distinction is easy to lose and we lost it.

### 2.6 Negative Results and Measurement Artifacts in Self-Supervised Pipelines

The failure mode documented in § 5.5 belongs to a known family: a pipeline whose supervision signal is derived from the same distribution it is evaluated on will report optimistic numbers, and the optimism scales with how loosely the signal is matched. Analogues include lexical-overlap shortcuts in NLI, retrieval evaluations contaminated by near-duplicate passages, and reward-model overestimation under distributional proximity. Our contribution here is not the general phenomenon but a concrete instance with a clean diagnostic: the same corpus, the same technique, and two supervision references differing only in provenance, producing 57.8 % and 6.0 % respectively (§ 5.3).

---

## 3. The Four-Layer Framework

PlanckBot decomposes LLM-tool adaptation into four layers. Each transforms a different quantity, operates at a different timescale, and can be deployed independently.

**Figure 1.** Adaptation layers and their timescales.

```
       host LLM
          │
    tool_use emission
          │
 ┌────────┴────────┐
 │                 │
Layer A:          Layer B:
SELECTION         RUNTIME FILTER
(which tool,      (per-tool tiny LLM
 and whether       filters output —
 to call at all)   ms per call)
          │
 ┌────────┼────────┐
 │                 │
Layer C:          Layer D:
EDIT              SYNTHESIS
(rewrite an       (propose new tools
 existing tool     from repeated
 under AST gate)   patterns)
```

A framing change in this revision: in v2, Layer A was defined as "the host LLM chooses the tool; we do not intervene." The benchmark's two strongest results (§ 5.3) — failure prediction and call elimination — are both *interventions on whether a call happens*, which is Layer A territory. We therefore widen Layer A from "tool selection, treated as given" to **"tool selection and call admission,"** and record that PlanckBot's centre of gravity has moved from B to A.

### 3.1 Layer A — Tool Selection and Call Admission

The host LLM decides which tool to call. PlanckBot may now intervene in two non-destructive ways: predicting that a proposed call will fail and saying so before it runs (§ 9.1), and answering a proposed call from cache when the underlying resource is provably unchanged (§ 9.3). Both are *eliminations*: they remove a round-trip rather than shrinking its payload. Neither discards content the model would otherwise have seen — which is why both carry 0 % or bounded content risk in Table 3.

### 3.2 Layer B — Runtime I/O Filtering

When the host LLM invokes a proxied tool, PlanckBot receives `(tool_name, input, output_raw)`. A per-tool LoRA adapter emits a candidate `filtered_output`, returned to the host iff **(i)** confidence exceeds a threshold (§ 4.4), **(ii)** the tool is not flagged side-effectful (`Write`, `Edit`, `Bash`-equivalents are never filtered), and **(iii)** the proxy is in `intervene` mode.

This is the layer v2 led with and the layer this revision demotes. Section 5.4 gives its honest ceiling (6.0 %); § 5.6 explains why that number, though small, is not zero and under what conditions it might still be worth collecting.

### 3.3 Layer C — Tool Source Editing

Host LLMs can write Python. When a tool produces persistently noisy output that filtering cannot solve, the host can propose a patch to the tool's source. PlanckBot exposes this as `edit_tool(name, patch)`:

1. Apply the patch to the tool's Python source file.
2. Run an AST whitelist: reject any module in `{subprocess, socket, pickle, ctypes}`; reject any bare `Name` in `{eval, exec, compile, __import__}`; reject attribute pairs in `{(os, system), (os, popen), (os, execv), (shutil, rmtree), (pickle, load), …}`.
3. For Layer-C edits, additionally reject any *new* imports — a patch may not silently expand the tool's dependency surface. Layer-D synthesis relaxes this, since a new tool has no prior surface.
4. Compute the new source's SHA-256, insert an immutable `tool_versions` row keyed by hash, and deactivate any `model_checkpoints` row referencing the prior version.

The **invariant**: an adapter is only applied to calls against the exact tool version it was trained on.

### 3.4 Layer D — Tool Synthesis

A pattern detector (Algorithm 2, § 4.5) scans the triples log for N-grams of tool names repeating within a time window. Candidates are persisted as `gap_reports`; accepted gaps become new tools via `synthesize_tool(...)`, validated by the same AST gate, inserted as `status='draft'`, and hot-deployed on activation via SIGHUP to the `planckbot-synth` MCP server.

Layer D was the most speculative layer in v2 and is the second-highest-ranked technique in v3's benchmark (§ 5.3). Its saving is structural — it removes whole tool calls and their results — rather than lossy, which is exactly why it survives honest measurement.

---

## 4. System Architecture

### 4.1 Components

**Figure 2.** Runtime components (schema v8).

```
 host LLM (Claude Code / other MCP host)
     │                         │
     │ MCP JSON-RPC (stdio)    │ PreToolUse / PostToolUse hooks
     ▼                         ▼
 ┌──────────────────┐   ┌──────────────────────────┐
 │ planckbot-fs     │   │ planckbot-hook           │
 │  wraps upstream  │   │  cache lookup / deny     │
 │  MCP server      │   │  dirty-marking on write  │
 │  mode ∈ {observe,│   │  (reaches NATIVE tools)  │
 │   suggest,       │   └────────────┬─────────────┘
 │   intervene}     │                │
 └────────┬─────────┘                │
          │        ┌─────────────────┘
          ▼        ▼
 ┌────────────────────────────────────────────────┐
 │ SQLite — schema v8                              │
 │  triples · projects · experiments               │
 │  model_checkpoints · tool_versions · cron_jobs  │
 │  gap_reports · synthesized_tools                │
 │  activity_events · tool_cache                   │
 └────────┬───────────────────────────────────────┘
          │
   ┌──────┼────────┬──────────────┬───────────────┐
   ▼      ▼        ▼              ▼               ▼
 cron   NiceGUI  planckbot-   JSONL mass-     bench harness
 daemon workbench  synth        ingest        (replay, offline)
```

Three components are new since v2. **JSONL mass-ingest** (`scripts/bulk_ingest_jsonl.py`) reads the host's own conversation transcripts and reconstructs triples for *every* tool call, including native tools the MCP proxy never sees — this is what made § 5 possible. **`planckbot-hook`** implements PreToolUse/PostToolUse handlers, the only interception point that reaches native tools. **`bench/`** is an offline replay harness, described in § 5.2.

All components are Python 3.12; inter-component communication is exclusively through SQLite. No message bus, no container, no external service.

### 4.2 The Triple

```sql
-- Schema 1. The triples table (abridged, v8).
CREATE TABLE triples (
    id              TEXT PRIMARY KEY,
    tool_name       TEXT NOT NULL,
    input_data      TEXT NOT NULL,          -- JSON-serialized args
    output_data     TEXT NOT NULL,          -- raw tool output
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    filtered_output TEXT,                   -- NULL until auto-labeled
    filtered_tokens INTEGER,
    source          TEXT,                   -- manual | proxy:* | claude_code:jsonl
    context_data    TEXT,                   -- JSON; carries redaction audit
    tool_version_id TEXT,                   -- FK → tool_versions(id)
    project_id      TEXT,                   -- FK → projects(id)
    experiment_id   TEXT,                   -- FK → experiments(id)
    created_at      TEXT
);
```

A triple is an *observation* until `filtered_output` is populated, at which point it becomes a *training example*. The entire Layer-B supervision signal flows through that one column — which is precisely why an error in how it is populated propagates to every downstream number (§ 5.5).

### 4.3 Per-Triple Auto-Labeling

Claude Code writes append-only JSONL logs interleaving `assistant` messages containing `tool_use` blocks, `user` messages containing `tool_result` blocks, and the host's subsequent synthesis. We claim: *the portion of a tool's output that is useful is precisely the portion cited in the host LLM's immediately following reply.* Algorithm 1 formalizes this.

```
╭───────────────────────────────────────────────────────────────────╮
│ Algorithm 1 — Per-triple auto-label                                │
├───────────────────────────────────────────────────────────────────┤
│ Input:  triple t (tool_name, input_data, output_data, created_at,  │
│         filtered_output=NULL); host project dir of JSONL logs.     │
│ Output: filtered_output assigned to t (or NULL if no match).       │
│                                                                    │
│ 1  entries   ← chronologically-parsed JSONL lines in project dir   │
│ 2  best_idx  ← None; best_drift ← ∞                                │
│ 3  for each entry e at index i in entries do                       │
│ 4      if e.type ≠ "assistant" then continue                       │
│ 5      let t_e ← parse_iso8601(e.timestamp)                        │
│ 6      if |t_e − t.created_at| > Δ_max then continue               │
│ 7      for each content block b ∈ e.message.content do             │
│ 8          if b.type ≠ "tool_use" then continue                    │
│ 9          if not tool_name_matches(b.name, t.tool_name) then      │
│10              continue                                            │
│11          if ∃ k : b.input[k] ≠ t.input_data[k] then continue     │
│12          if |t_e − t.created_at| < best_drift then               │
│13              best_drift ← |t_e − t.created_at|; best_idx ← i     │
│14  if best_idx is None then return NULL                            │
│                                                                    │
│15  for j ← best_idx + 1 .. len(entries) − 1 do                     │
│16      if entries[j].type ≠ "assistant" then continue              │
│17      texts ← text content of entries[j].message.content          │
│18      if texts non-empty then reference ← join(texts); break      │
│19  else return NULL                                                │
│                                                                    │
│20  ref_tokens ← word_tokenize(reference, min_len=3)                │
│21  kept ← [ ℓ ∈ split_lines(t.output_data) :                       │
│              (word_tokenize(ℓ) − STRUCTURAL) ∩ ref_tokens ≠ ∅ ]    │
│22  if kept is empty then return NULL                               │
│23  t.filtered_output ← join(kept, "\n"); persist; return it        │
╰───────────────────────────────────────────────────────────────────╯
```

Two subtleties. **Line 9** accepts the host-visible namespaced name (`mcp__planckbot-fs__list_directory`) as a match for the bare recorded name. **Lines 20–21** use word-level rather than substring matching, because assistant replies paraphrase (`[DIR] app` does not appear verbatim in "*the important ones are **app** and **packages***" but shares the word `app`). A `STRUCTURAL` stoplist excludes tokens like `dir`, `file`, `http` that would otherwise fire on every line.

In v2 we described this as "a *weak* supervision signal … self-correcting: more data dilutes the noise." Section 5.5 shows the second half of that sentence is false. More data does not dilute this noise, because the noise is correlated with the data.

### 4.4 Proxy Modes and Confidence Gating

Three modes, set per MCP server: **`observe`** (log only, zero risk, the default), **`suggest`** (log plus record the adapter's prediction as metadata without swapping), and **`intervene`** (return `filtered_output` when confidence exceeds θ, default 0.9). Confidence is the geometric mean of per-step top-token probability, `conf = exp(mean_i log p_i)`; it is uncalibrated (§ 8.2). Side-effect tools are never intervened on regardless of mode — a hard-coded invariant.

Schema v5 added a `blessed` column on `model_checkpoints`: `CheckpointManager.activate()` refuses to serve an unblessed checkpoint, so an operator must explicitly evaluate and `bless` before an adapter can enter the serve path, and `unbless` revokes and deactivates atomically.

### 4.5 Tool Synthesis Pipeline (Layer D)

```
╭───────────────────────────────────────────────────────────────────╮
│ Algorithm 2 — N-gram gap detection                                 │
├───────────────────────────────────────────────────────────────────┤
│ Input:  triples T, window w, ngram range [n_lo, n_hi], min occ m.  │
│ Output: gap report rows.                                           │
│                                                                    │
│ 1  sort T by created_at ascending; buckets ← {}                    │
│ 2  for each i ∈ [0, |T|) do                                        │
│ 3      for each len ∈ [n_lo, n_hi] do                              │
│ 4          if i + len > |T| then break                             │
│ 5          if T[i+len−1].created_at − T[i].created_at > w then     │
│ 6              continue                                            │
│ 7          seq ← (T[i+k].tool_name for k ∈ [0,len))                │
│ 8          if |set(seq)| < 2 then continue  // skip "same tool ×N" │
│ 9          buckets[seq].append(T[i].id)                            │
│10  for (seq, ids) in buckets where |ids| ≥ m do                    │
│11      upsert gap_report(tool_sequence=seq, occurrences=|ids|,     │
│                          example_triple_ids=first K of ids)        │
╰───────────────────────────────────────────────────────────────────╯
```

The upsert is keyed on the sequence, so repeated hourly runs bump `occurrences` rather than inserting duplicates.

### 4.6 The Scheduler

A file-locked cron daemon polls `cron_jobs` every two seconds and dispatches rows whose `enabled=1 AND next_run_at ≤ now`. Job types: `noop`, `conversation_scanner`, `autolabel` (coarse batch, legacy), `autolabel_precise` (Algorithm 1), `detect_tool_gaps` (Algorithm 2), and `retrain` (emits a ready-to-retrain signal rather than blocking the scheduler for ~25 minutes of CPU training).

### 4.7 Secret Redaction at Ingest

Mass-ingesting real conversation transcripts means ingesting whatever secrets passed through them. `ingest/redact.py` scrubs common patterns (provider API keys, JWTs, private-key blocks, `password`/`api_key` JSON fields) from both `input_data` and `output_data` before a triple is persisted, and records the hit in `context_data.had_secrets` with the matched pattern classes for auditing. On the 16-project ingest, **58 triples were flagged across 9 distinct pattern types**. This is a prerequisite for the corpus in § 5 being safe to keep, not an optional nicety (§ 8.3).

---

## 5. Evaluation at Scale

Hardware: Intel i7-12650H, 32 GB RAM, CPU-only PyTorch, Python 3.12. Host LLM: Claude Code. Test suite: **432 tests, 64 s**, deterministic.

### 5.1 The Corpus

Between April and July 2026 the observation layer accumulated, passively and without any change to the developer's workflow:

**Table 1.** Corpus composition (as of 2026-08-29).

| | |
|---|---:|
| Triples | **17,604** |
| Distinct tools | 29 |
| Projects | 16 |
| Output tokens (scope) | **4,857,728** |
| Input tokens | 3,561,456 |
| Triples with `filtered_output` | 283 (1.6 %) |
| Triples flagged as containing secrets | 58 |
| Sources | `claude_code:jsonl` 17,257 · `manual` 256 · `proxy:*` 91 |

Two features of Table 1 deserve emphasis before any technique is evaluated.

First, **the labeled fraction is 1.6 %**. The auto-label loop that v2 described as capable of reaching "200–500 labeled triples per tool within a week of organic usage" produced 283 labels total, across all tools, in three months. The loop closes, but it does not close fast.

Second, **the source distribution is 98 % JSONL ingest, not proxy**. The MCP proxy — the architectural centrepiece of v2 — contributed 91 triples out of 17,604.

**Table 2.** Where the tokens actually are (top tools by output volume).

| Tool | Calls | Output tokens | % of scope | Reachable by MCP proxy? |
|---|---:|---:|---:|:--:|
| `Read` | 2,313 | 2,811,568 | 57.9 % | no |
| `Bash` | 8,056 | 1,616,651 | 33.3 % | no |
| `Edit` | 3,437 | 135,955 | 2.8 % | no |
| `Agent` | 44 | 75,828 | 1.6 % | no |
| `Grep` | 102 | 37,100 | 0.8 % | no |
| `Write` | 908 | 34,847 | 0.7 % | no |
| `directory_tree` | 14 | 31,056 | 0.6 % | **yes** |
| `list_directory` | 264 | 14,971 | 0.3 % | **yes** |
| … | | | | |
| **Native host tools** (total) | 14,832 | **4,636,565** | **95.45 %** | **no** |
| **Filesystem MCP tools** (total) | 346 | **58,491** | **1.20 %** | **yes** |

Table 2 is the single most consequential measurement in this paper, and it required no model, no training, and no benchmark — only a corpus large enough to be representative. **Every quantitative claim in v2 was measured on the 1.20 %.** `Read` and `Bash` alone are 91.2 % of output tokens, and neither is reachable from the MCP vantage point the system was built around.

### 5.2 Benchmark Methodology

To avoid repeating v2's error of generalizing from a single call, we built an offline replay harness (`src/planckbot/bench/`) with four properties:

1. **Session grouping.** Triples are grouped into sessions by project and temporal contiguity, so that techniques with session-scoped state (caches, N-gram windows) are evaluated under realistic locality. The corpus yields **132 sessions**.
2. **A held-out split.** `datasets.split_sessions(seed=42, holdout_frac=0.2)` stratifies per project: **104 train / 28 holdout** sessions, persisted to `data/bench/split.json` so runs are comparable.
3. **A `Technique` interface.** Each candidate implements a `replay()` contract returning, per triple, tokens saved and tokens *at risk* — content that would have been withheld from the model and might have been needed. Techniques that eliminate a call rather than filter its content report zero at-risk tokens by construction.
4. **Two supervision references.** This is the methodological core. A technique that needs to know "what did the model actually use from this output" can be given either:
   - **local signal** — the reference is reconstructed from the corpus itself, i.e. from the inputs of subsequent tool calls in the same session; or
   - **JSONL signal** — the reference is the actual assistant text that followed the call, extracted in bulk by `bench/references.py` (`tool_use_id → next_assistant_text`).

   The same technique run under both signals gives a direct measurement of the artifact (§ 5.5).

Secret-flagged triples are excluded by default, leaving **16,337 triples** and a scope of **4,390,921 output tokens** for the benchmark run reported below. Twelve techniques were implemented, drawn from a design space of ~30 candidates enumerated during the strategy review; the twelve are those with a plausible mechanism *and* a measurable proxy in the corpus.

### 5.3 Results

**Table 3.** Tier-1 benchmark, all 12 techniques, ranked by ceiling. `Coverage` = share of triples the technique touches at all. `At-risk` = share of the saved tokens that constitute withheld content rather than eliminated calls.

| # | Technique | Mechanism | Ceiling | Coverage | At-risk | Class |
|---:|---|---|---:|---:|---:|---|
| 1 | `J.uncited_compaction` | drop never-cited history | 98.52 % | 96.3 % | 100 % | artifact |
| 2 | `L.split_tools_local` | filter output, local signal | 57.79 % | 13.3 % | 50.0 % | artifact |
| 3 | **`G.retry_detection`** | predict call failure pre-execution | **45.07 %** | 37.3 % | **0 %** | elimination |
| 4 | **`K.ngram_synthesis`** | collapse repeated tool N-grams | **41.21 %** | 69.2 % | **0 %** | elimination |
| 5 | **`A.cache_deny_read`** | serve repeated reads from cache | **13.31 %** | 5.4 % | 33.7 % | elimination |
| 6 | `L.split_tools_jsonl` | filter output, **honest signal** | **6.02 %** | 1.8 % | 40.0 % | compression |
| 7 | `V.tool_budget` | abort runaway tool loops | 5.91 % | 0.1 % | 100 % | speculative |
| 8 | `D.diff_reads` | serve re-reads as diffs | 3.13 % | 2.3 % | 0 % | elimination |
| 9 | `M.arg_autocorrect` | fix malformed args pre-execution | 2.55 % | 1.1 % | 0 % | elimination |
| 10 | `N.bash_early_term` | truncate long streams adaptively | 2.28 % | 2.1 % | 19.9 % | compression |
| 11 | `B.bash_exact_dedup` | dedup identical Bash calls | 0.41 % | 1.4 % | 0 % | elimination |
| 12 | `T.dead_end_prompts` | answer ambiguous prompts locally | 0.00 % | 0.0 % | 0 % | discard |

Three readings follow.

**The top two rows are not results.** `J` reports that 96.3 % of tool output is never cited again and could therefore be compacted — with 100 % of the saving at risk, which is the harness telling us that the technique's "saving" is entirely composed of content it cannot prove was unnecessary. `L.split_tools_local` is the same technique as row 6 differing *only* in supervision provenance, and it reports 9.6× more saving. We treat both as instrument readings, not measurements (§ 5.5).

**Rows 3–5 are the real findings, and they share a mechanism.** `G`, `K` and `A` do not decide *what to keep* from a tool result; they decide *whether the call needs to happen*. That is why their at-risk fractions are 0 %, 0 % and 33.7 % rather than 100 %: eliminating a redundant call withholds nothing, because the content was already in context or was never going to be read.

**Row 6 is the honest version of the system v2 described.** Layer-B compression, measured against what the model actually wrote rather than against a proxy correlated with the output itself, is worth 6.02 % of tool-output tokens at 1.8 % coverage and 40 % at-risk.

### 5.4 Retraction of the 96.5 % claim

Draft v2 § 5.1 reported a live `list_directory` call whose 230-token output was reduced to 8 tokens — 96.5 % — with 3/3 true positives and 0 false positives, and presented this as the paper's headline evidence for Layer B.

We retract the claim as evidence. We do not retract the observation: the call happened, the numbers are arithmetically correct, and the labeler did behave as described on that call. What was wrong was the inference. Specifically:

1. **n = 1.** A single call, hand-inspected, chosen because it worked.
2. **Selection on the favourable case.** `list_directory` is the *most* citable tool shape in the catalogue — a list of short identifiers that a model naturally reproduces verbatim. Table 2 shows it is 0.3 % of token volume.
3. **The metric flatters the tool.** A 96.5 % reduction on a 230-token output saves 222 tokens. The same percentage on `Read`, at 1,215 tokens per call average, would be the number that matters — and § 5.3 row 6 says the achievable figure there is not 96.5 %.
4. **The estimator was not validated against a held-out signal.** The reference came from the same conversational neighbourhood as the output.

The corrected corpus-wide figure for the mechanism v2 was demonstrating is **6.02 %** (Table 3, row 6). Every derived claim in v2 that depended on the 96.5 % figure — including the abstract, § 1.2, and the README headline that was published alongside it — should be read as superseded.

### 5.5 The citation-matcher artifact

Why do rows 2 and 6 of Table 3 disagree by 9.6× when they are the same code?

The technique asks, for each line of a tool output, "was this line used?" and needs a reference text to answer against. Under the **JSONL signal**, the reference is what the assistant actually wrote next — a few hundred tokens of prose, in which the model mentions the handful of things it cared about. Under the **local signal**, the reference is assembled from the *inputs of subsequent tool calls in the same session* — file paths, command strings, search patterns.

The second reference is contaminated by construction. In a coding session, the paths and identifiers appearing in later tool calls are drawn from the same small vocabulary as the lines of the earlier tool outputs — because the agent is working on one codebase. A `Read` of a 400-line file will find that dozens of its lines share a ≥3-character token with *some* later `Edit`, `Grep` or `Bash` argument. The matcher then reports those lines as "used," and the technique claims credit for keeping them and dropping the rest. The measured saving is therefore a function of vocabulary overlap within a project, not of what the model needed.

This is the **citation-matcher artifact**, and it has three properties that make it dangerous:

- **It does not shrink with more data.** v2 asserted the signal was "self-correcting: more data dilutes the noise." The opposite holds: more triples from the same project raise the vocabulary overlap and *increase* the false-positive rate. Our corpus grew 290× between drafts and the artifact grew with it.
- **It produces plausible numbers.** 57.8 % is high but not absurd; it sits comfortably inside the range the prompt-compression literature reports (§ 2.1), which is precisely why it survived review in v2.
- **It is invisible without a second signal.** No amount of inspecting the pipeline reveals it. It becomes visible only when the same technique is run against an independently-sourced reference — which is why the harness's dual-signal design (§ 5.2, property 4) is the paper's main methodological contribution.

The diagnostic we propose is simple and cheap: **run the compression technique twice, with two references of different provenance, and report both.** If the numbers diverge by more than a small factor, the lower one is the measurement and the higher one is the vocabulary of your project. Any citation-supervised tool-output compressor can apply this test in an afternoon, and we suggest that reported figures in this area be accompanied by it.

We note without schadenfreude that we ran this diagnostic only after an external evaluation (§ 6) independently flagged the same failure in our shipped matcher. We had two chances to catch it ourselves — the "self-correcting" sentence in v2 § 4.3 is where the reasoning went wrong — and took neither.

### 5.6 What survives, and what the value actually is

**Upheld from v2.** The negative result of v2 § 5.3 stands unchanged: LoRA-rank-8 adapters on SmolLM2-135M trained on ≤ 20 supervised triples *expand* rather than compress outputs (+53 % to +274 %), one collapsing into degenerate repetition. Nothing in the new evidence rehabilitates low-data adapter training. Given § 5.4, the practical implication has changed: the fix is no longer "collect 500 triples per tool," because the ceiling being trained toward is 6 %.

**Upheld and reframed.** Per-triple linking still beats batched matching (v2 § 5.2) — that comparison was between two *degrees* of contamination, and removing the coarser one was correct. It simply did not remove the finer one.

**Reversed.** Layer B is not the system's primary value. The ranking in Table 3 is unambiguous: at zero content risk, failure prediction (45.1 %) and compound-tool synthesis (41.2 %) each offer roughly 7× what honest compression offers, and cache-based elimination (13.3 %) offers 2×.

**The unifying principle.** All three winners implement the same idea, and it is the idea PlanckBot was always articulating without measuring: *a small model that learns from real usage in order to spare the large model work.* v2 instantiated "spare work" as **filter what comes back**. The corpus says the instantiation should be **avoid the round-trip**. The philosophy survives its first honest test; the tactic does not.

### 5.7 Operational footprint

**Table 4.** Resources consumed by the reference implementation.

| | |
|---|---|
| Source | 18,928 LOC Python |
| Test suite | **432 tests, 64 s** |
| Python runtime deps | 20 (torch, transformers, peft, nicegui, mcp, tiktoken, …) |
| Base model on disk | 270 MB (SmolLM2-135M-Instruct) |
| LoRA adapter on disk | ~10 MB per tool (rank 8, q_proj + v_proj) |
| Proxy overhead per call | < 5 ms round-trip (cached adapter) |
| Training (16 triples, 3 ep, CPU) | ~25 min |
| DB after 17,604 triples | 95 MB |
| Full benchmark run (12 techniques) | ~90 s |

The system remains a laptop-scale artifact. No GPU is required for inference, and none was used for training at this scale.

---

## 6. External Evaluation

Between drafts, an external platform team evaluated PlanckBot for adoption as a token-optimization layer in their agent product, and produced a written feasibility report. We summarize it here because a third party attempting to deploy the system found things we had not, and because their rejection is a result.

**What they confirmed.** The observation layer worked as advertised: 2,053 triples captured from their own workload without friction or workflow change. They characterized triples as the correct primitive and the usage-derived flywheel as a genuine source of signal about where tokens are spent.

**What they found wrong.** Two findings, both of which we subsequently reproduced:

1. **The compression figure was inflated by matcher false positives.** Their measured 33.6 % saving fell, on inspection of individual labels, to an honest estimate of 15–22 % of tool-output tokens (≈ 8–12 % of a full bill). They identified the cause precisely: the token matcher of § 4.3 keeps a line if it shares any ≥3-character non-structural token with the reference, so a short generic acknowledgement can "justify" retaining — or, symmetrically, discarding — a large output. This is the citation-matcher artifact of § 5.5, diagnosed independently and three weeks before our own benchmark found it at larger scale.
2. **An architectural blocker.** Native host tools (`Read`, `Bash`, `Edit`, `Grep`, `Glob`, `Write`) bypass the MCP proxy by design. The available interception points were, at the time of their evaluation, insufficient: `PreToolUse` can modify a call's *input* but not its output, and `PostToolUse` could add bounded `additionalContext` but not *replace* what the model sees. There was therefore no supported path by which a learned filter could act on the tools that carry the traffic. Table 2 of this paper quantifies the consequence they identified qualitatively: 95.45 % of token volume is out of reach.

**Their decision.** They did not ship PlanckBot as a runtime compressor. They did absorb the triples-plus-citation-tracking idea as an internal analytics feature.

**The conditions they set for reconsidering**, quoted in substance: (i) the host platform adds output rewriting to `PostToolUse`; (ii) PlanckBot ships an SDK or patch that intercepts native tools; (iii) a high-quality drop-in MCP server for `Bash` exists. Section 7 reports that condition (i) was satisfied — and that, unknown to either party, it had already been satisfied *before* the report was written.

**How we read this.** The report closed one tactical instantiation — "intercept MCP, rewrite output via `PostToolUse`" — and left the framework's premises intact. It is also the second independent confirmation, alongside § 5.3, that a system can measure its own headline metric wrong for months when the metric's supervision is drawn from the neighbourhood of the thing being measured. We consider the external evaluation the highest-value input the project received in this period, and note that it cost us nothing to obtain and would not have been produced by any amount of internal testing.

---

## 7. Platform Drift (April → August 2026)

A system that inserts itself between a host LLM and its tools is exposed to a risk that most research artifacts are not: the host may implement, obviate, or unblock parts of the design while the paper is being written. Over four months we observed all three. We report this because the effect is large enough to change a roadmap and because it is, we suspect, underdiscussed in agent-infrastructure work generally.

**Absorbed by the platform.** Three items from v2's future work and the intervening design space are now host features:

- *Dynamic tool-catalogue loading.* Our planned technique for loading only contextually relevant tool definitions is now `defer_loading` plus a host-side tool-search mechanism: when a catalogue exceeds a token threshold, definitions are withheld and the model retrieves the few it needs on demand. This directly implements the mechanism we had scoped.
- *Selective conversation compaction.* Automatic clearing of stale tool results as a context window fills is now a platform capability. This is technique `J` of Table 3, which our own benchmark had already discarded as an artifact — the platform arrived at the same place from the opposite direction.
- *Prefix caching maturity.* Aggressive prompt caching reduces the marginal value of shaving tokens off a stable prefix, narrowing the economic case for compression-class techniques generally.

**Unblocked by the platform.** Simultaneously, the blocker of § 6 was removed. Inspecting the hook output schema shipped in Claude Code **2.1.251** (the version in use on our development machine as of 2026-08-29), `PostToolUse` accepts:

```
updatedToolOutput      "Replaces the tool output before it is sent to the model"
updatedMCPToolOutput   "Replaces the output for MCP tools only.
                        Prefer updatedToolOutput, which works for all tools"
```

with the host applying the returned string in place of the tool's actual output, and with explicit handling for parallel hooks competing over the same rewrite. Third-party reporting dates the generalization from MCP-only to all-tools to release 2.1.121, **2026-04-28** — three weeks *before* the strategy review that pivoted the project away from Layer B on the grounds that this path was closed. The public hooks documentation page we retrieved on 2026-08-29 did not describe the field; we rely on the shipped schema, which we consider authoritative for the version actually executing.

**What this changes, and what it does not.** Condition (i) of § 6 is satisfied: a learned filter can now act on `Read` and `Bash` output. It is tempting to read this as the pivot being unnecessary. It is not, for a reason that only the benchmark can supply: **the newly-unblocked capability is the one worth 6 %.** Table 3 row 6 is now deployable and remains row 6. The techniques worth 45 % and 41 % never depended on this blocker, and no platform feature has appeared that implements them.

**The lesson we draw** is a scheduling one rather than a technical one. Two of our three months of design work were spent routing around a constraint that had been lifted before the work started, and three roadmap items were made redundant by features shipping on a cadence far faster than ours. For a project positioned this close to a fast-moving host, verifying the current capability surface is not a preliminary to the work — it is part of the work, and it belongs at the start of every planning cycle. We have added it to ours.

---

## 8. Discussion and Limitations

### 8.1 The labeling signal remains the weakest component

Algorithm 1 assumes the host cites useful fragments in its reply. This holds for list-shaped tools and weakly for tools whose output the model *summarizes* — long code files, prose documents — where the word matcher recovers surface tokens and misses semantics. A semantic matcher (sentence-embedding cosine similarity at label time) is implemented behind an opt-in flag but is not the default, and switching the default requires the dual-signal validation of § 5.5 to be run against it. Given § 5.4, we now regard improving this matcher as a *prerequisite for honest measurement* rather than a route to better compression.

### 8.2 Confidence remains uncalibrated

Layer B's intervention decision uses geometric-mean top-token probability. An adapter producing fluent-but-wrong output — the degenerate `__pycache__` × 16 case — reports high confidence, because repeated tokens are easy predictions. The blessed-checkpoint gate (§ 4.4) is defence-in-depth, not calibration: it ensures a regressive adapter cannot silently enter the serve path, but it does not tell an operator where to set θ. Schema v5 carries a per-checkpoint `tuned_threshold` column; the command that would tune it does not exist.

### 8.3 Threat model, and the corpus as a liability

Three trust surfaces persist from v2 — a malicious upstream MCP server, adapter poisoning via prompt injection, and conversation-log sensitivity — and the third has grown materially with scale. The corpus is now 95 MB of one developer's verbatim work across 16 projects, including 58 triples that contained credentials before redaction. Section 1.4 notes that a deleted manuscript was fully recoverable from it; the same property means anything else that passed through a tool call is equally recoverable. Redaction at ingest (§ 4.7) is necessary and not sufficient: it catches patterned secrets, not secrets in prose. Any deployment of this system beyond a single machine should treat the triples database as at least as confidential as the conversation logs it derives from, and we do not currently recommend such a deployment.

### 8.4 Code safety of Layers C and D

The AST gate is a **denylist**, not a proof. A determined attacker controlling patch content could likely evade it (`getattr(__builtins__, 'ex' + 'ec')`). We claim substantially reduced attack surface versus accepting arbitrary diffs, not sandboxing. Adversarial deployments require a Firecracker-style microVM or a seccomp-filtered subprocess.

### 8.5 The corpus is a convenience sample

All 17,604 triples come from one developer, one host LLM, and one style of work (mostly Python and TypeScript application development across 16 projects). The tool mix in Table 2 — `Read` and `Bash` at 91 % of volume — is plausibly characteristic of coding agents but not of agents doing retrieval, browser automation, or data analysis, where the distribution and the citability of outputs both differ. Every percentage in Table 3 should be read as *conditional on this workload*. Replicating the benchmark against a second developer's corpus is the cheapest available test of external validity and has not been done.

### 8.6 Ceilings are not systems

Rows 3–5 of Table 3 report what an *oracle* implementation of each technique would have saved on replayed traffic. `G` assumes a perfect failure predictor; `K` assumes the model adopts every synthesized compound tool; `A` assumes every cache hit is safe to serve. Realized savings will be lower by the product of precision and adoption, and § 9 states our estimates. A ceiling is a decision aid for where to spend effort, not a claim about a deployed system, and we have been burned once already by treating a favourable measurement as a result.

---

## 9. Future Work

The benchmark supplies a ranking; this section commits to a plan. Three tracks, in order.

### 9.1 Track G — a failure predictor (ceiling 45.1 %, risk 0 %)

**Mechanism.** Detect, before a tool call executes, that it will fail — a `Bash` command with a bad path, a `Grep` with an invalid pattern, an `Edit` whose `old_string` no longer matches — and say so in `PreToolUse` rather than paying for the error round-trip plus the model's retry.

**Why it is tractable now.** The benchmark's retry detector already labels the corpus: 37.3 % of triples participate in a detected failure-and-correction pair, which is a large positive class by any supervised-learning standard. The task is binary classification over `(tool_name, input_args, recent session state)`, which is a far easier target than the sequence-to-sequence line selection that Layer B adapters failed at with 16 examples (§ 5.6).

**Realistic yield.** At 40 % precision and 60 % recall against the 45.1 % ceiling, this is ≈ 10 % of tool-output tokens — larger than the honest Layer-B figure by a factor of two, at zero content risk. **This is the project's primary track.**

**First milestone.** Export the retry-labeled dataset from the bench, train a classifier, and report a precision-recall curve on the 28 held-out sessions. No LoRA and no host integration are required to produce that number, which makes it the cheapest decisive experiment available.

### 9.2 Track K — compound tools and the adoption gap (ceiling 41.2 %, risk 0 %)

**Mechanism.** Layer D already detects recurring N-grams (`Read X → Edit X → Bash test` and similar) and can synthesize and hot-deploy a compound tool that collapses them into one round-trip. The infrastructure exists and is tested.

**What is missing is not synthesis but adoption.** A synthesized tool the host never chooses saves nothing, and Table 3's 41.2 % assumes universal adoption. The open problem is orientation: generating a skill entry or project-instruction fragment that makes the compound tool the obvious choice at the moment the pattern begins. This is a prompt-engineering problem wearing a systems problem's clothes, and we do not know its success rate.

**First milestone.** Take the single highest-occurrence gap report, synthesize the tool, autogenerate the orienting skill, and measure adoption over a week of real usage: what fraction of occurrences of the pattern actually route through the compound tool. That ratio, multiplied by 41.2 %, is the technique's real value, and it is currently unknown to within an order of magnitude.

### 9.3 Track A — the cache hook, and one pending experiment (ceiling 13.3 %, risk 33.7 %)

**Status.** Fully implemented and untested in production. Schema v8 carries a `tool_cache` table; `ToolCacheStore` handles put / mtime-verified lookup / dirty-marking / session purge; `planckbot-hook` dispatches `PreToolUse` (deny a `Read` whose cached content is provably fresh, supplying the content) and `PostToolUse` (populate the cache), with `Edit`/`Write` marking entries dirty. 37 tests cover it. It has never been registered in the host's settings, and the cache table contains zero rows.

**The pending experiment.** Registering the hook and issuing two `Read` calls on one unchanged file answers the only open question: whether the host surfaces cached content to the model when a `PreToolUse` deny carries `additionalContext`. This is a 30-minute experiment that has been outstanding for three months, and it gates the track.

**What § 7 changes here.** If the deny-plus-context path does not pass content through, the fallback is no longer the undocumented input-redirect hack v2's successor notes proposed. `PostToolUse` `updatedToolOutput` supplies a documented, supported path to substitute cached content directly. The track is therefore de-risked regardless of the experiment's outcome.

**On the 33.7 % at-risk figure.** Unlike `G` and `K`, cache elimination *can* be wrong — if a file changed by a route the dirty-tracking missed, the model reads stale content. Mitigation is mtime verification plus conservative session scoping, and the residual risk is why this track ranks third despite being the most nearly finished.

### 9.4 Layer B, conditionally reopened

Section 7 makes Layer B architecturally deployable for the first time, on the tools that matter. Section 5.3 says it is worth 6.02 % at 1.8 % coverage. We are not building it, and we state the condition under which that reverses: if the semantic matcher (§ 8.1), validated under the dual-signal diagnostic, moves the honest figure materially above 10 % on `Read`-shaped outputs, the effort is justified — because `Read` alone is 57.9 % of scope, and a real 10 % of that is worth more than either remaining track. Measuring this costs one benchmark run against an improved matcher, not an implementation.

### 9.5 What we are explicitly not doing

Stating the negative half of a plan is the part most often omitted, so: we are dropping techniques `J`, `V`, `T`, `B` and `N` from Table 3 as artifact or marginal; we are not collecting the 200–500 labeled triples per tool that v2 § 5.4 proposed, because the ceiling it serves does not justify it; we are not training further low-data LoRA adapters until a task with a validated ceiling above 10 % exists; and we are not extending the workbench UI, which is the most complete component of the system and the least connected to any measured saving. Roughly 80 % of the implementation effort to date sits behind Layer B and its supporting infrastructure, and the three tracks above need very little of it.

### 9.6 Restart the corpus, and report both signals

Two housekeeping commitments with research consequences. First, ingest has been dormant since July 2026 (989 triples in June, 2 in July); the flywheel that makes every subsequent claim possible is stopped, and restarting it is a prerequisite to any held-out evaluation on fresh data. Second, we commit to reporting the dual-signal diagnostic of § 5.5 alongside every future compression figure this project publishes, and we encourage the same of related work. The single number is the artifact; the pair is the measurement.

---

## 10. Reproducibility Statement

Source code, schema, and benchmark harness are at https://github.com/opcastil11/planckbot under Apache 2.0.

```bash
# 1. Install
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. Full test suite (must print "432 passed")
python -m pytest -q

# 3. Ingest a corpus from your own host transcripts
python scripts/bulk_ingest_jsonl.py --dry-run     # inspect first
python scripts/bulk_ingest_jsonl.py

# 4. Reproduce Table 3 (all 12 techniques, both signals)
python scripts/run_bench_tier1.py --full
#    → data/bench/{tier1_events,tier1_aggregate,tier1_ranking}.csv + split.json

# 5. Reproduce the § 5.5 artifact directly: compare rows 2 and 6
python scripts/run_bench_tier1.py --full --no-jsonl   # local signal only
#    L.split_tools_local ≈ 57.8 %  vs  L.split_tools_jsonl ≈ 6.0 %

# 6. Reproduce the § 5.6 negative result on low-data LoRA
python scripts/train_tool.py --tool list_directory \
    --fixture data/fixtures/list_directory_triples.json --epochs 3 --activate
python scripts/proxy_demo.py    # reports held-out deltas
```

The test suite is deterministic. The benchmark is deterministic given a fixed `split.json` (seed 42). Absolute token figures depend on the ingested corpus and will differ; the *ratio* between steps 4 and 5 is the reproducible claim, and it is the one § 5.5 rests on. Adapter training is deterministic modulo PyTorch CPU numerics (set `PYTHONHASHSEED=0`).

Table 1 and Table 2 are regenerated by `planckbot status` against a populated database. The corpus itself is not distributable: it is verbatim developer conversation content (§ 8.3).

---

## 11. Conclusion

PlanckBot observes every tool call an LLM agent makes, learns from that traffic, and intervenes to reduce its cost. Draft v2 presented the interception-and-compression instantiation of that idea with a 96.5 % headline. This revision retracts the headline, reports the honest figure of 6.0 %, explains the measurement artifact that produced the gap, and shows that the value lies elsewhere.

The load-bearing findings are three. **The corpus is not where we were looking**: 95.45 % of tool-output tokens flow through native host tools our MCP proxy never saw, and the entire v2 evaluation ran on 1.20 % of the traffic. **The metric was measuring the project's own vocabulary**: a citation matcher supervised by text drawn from the same session as the output it scores will find agreement everywhere, and the effect grows rather than shrinks with data — a hazard we believe applies to citation-supervised compression generally, and for which we give a cheap two-line diagnostic. **Elimination dominates compression**: predicting a failing call (45.1 %), collapsing a recurring sequence (41.2 %) and serving a repeated read from cache (13.3 %) each avoid a round-trip entirely, carry near-zero content risk, and together outweigh honest filtering by an order of magnitude.

What survives is the premise. A small model that learns from real usage in order to spare a large model work is a sound idea, and this corpus is evidence for it — just not for the version of it we built first. The instantiation was wrong in a specific and instructive way: we optimized what came back instead of asking whether the call needed to happen, and we validated that choice with an instrument that could not have told us otherwise. Both errors were caught by the same thing, which was more data measured two ways.

We also record a fact about working this close to a moving platform. In the four months between drafts, three of our planned mechanisms became host features and the architectural blocker that reshaped our roadmap was quietly removed — three weeks before we reshaped it. The capability surface is not context for the work; it is an input to be re-measured every cycle.

The path from here is empirical and narrow: one classifier, one adoption experiment, and one thirty-minute test that has been pending for three months. We would rather report those honestly than report another 96.5 %.

---

## Acknowledgments

Claude (Anthropic) served as implementation collaborator, as the supervision source for PlanckBot's adapters, and — through its conversation logs — as the corpus this paper analyzes. The external evaluation summarized in § 6 was the single most useful input the project received; we thank the team that took the time to try to deploy the system and to write down precisely why they did not.

This draft was prepared in partnership with Claude Opus 5. The v2 manuscript it revises was recovered from PlanckBot's own triples database after being deleted from version control (§ 1.4).

---

## References

[1] H. Jiang et al., "LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models," *EMNLP*, 2023.

[2] H. Jiang et al., "LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression," *ACL*, 2024.

[3] Anthropic, "Model Context Protocol Specification," 2024. https://modelcontextprotocol.io

[4] L. Ben Allal et al., "SmolLM2: The Open Small Language Model," HuggingFace, 2024.

[5] T. Schick et al., "Toolformer: Language Models Can Teach Themselves to Use Tools," *NeurIPS*, 2023.

[6] Y. Qin et al., "ToolLLM: Facilitating Large Language Models to Master 16000+ Real-world APIs," *ICLR*, 2024.

[7] S. G. Patil et al., "Gorilla: Large Language Model Connected with Massive APIs," *NeurIPS*, 2023.

[8] G. Wang et al., "Voyager: An Open-Ended Embodied Agent with Large Language Models," *arXiv:2305.16291*, 2023.

[9] Anthropic, "Claude Code Documentation," 2026. https://code.claude.com/docs

[10] MCP Community, "Awesome MCP Servers," 2024. https://github.com/punkpeye/awesome-mcp-servers

[11] S. Parfenyuk, "mcp-proxy: stdio ↔ SSE bridge for MCP," 2024. https://github.com/sparfenyuk/mcp-proxy

[12] LangSmith, "LangSmith Observability Platform," 2024. https://docs.smith.langchain.com

[13] Langfuse, "Open-Source LLM Engineering Platform," 2024. https://langfuse.com

[14] OpenLLMetry, "Open-source observability for LLM applications," 2024. https://www.traceloop.com/openllmetry

[15] E. Hu et al., "LoRA: Low-Rank Adaptation of Large Language Models," *ICLR*, 2022.

[16] M. Abdin et al., "Phi-3 Technical Report," Microsoft Research, 2024.

[17] HuggingFace, "PEFT: Parameter-Efficient Fine-Tuning Library," 2023. https://github.com/huggingface/peft

[18] MCP Community, "server-filesystem: reference MCP server," 2024. https://github.com/modelcontextprotocol/servers

[19] Speakeasy Team, "How We Reduced MCP Token Usage by 100x with Dynamic Toolsets," 2024.

[20] J. Wook, "mcp2cli: Token Cost Optimization for MCP Servers," 2024.

[21] Anthropic, "Claude Code Hooks Reference," 2026. https://code.claude.com/docs/en/hooks — and the hook output schema as shipped in Claude Code 2.1.251, which is the authority relied on in § 7.

[22] Anthropic, "Tool Search and Deferred Tool Loading," 2026. Platform documentation for `defer_loading` and host-side tool search.

[23] O. Castillo, "PlanckBot Tier-1 Benchmark," `src/planckbot/bench/` and `scripts/run_bench_tier1.py`, 2026. Results in `data/bench/tier1_ranking.csv`.

[24] O. Castillo, "Session 2026-05-21 — Strategy Rethink," `docs/session-2026-05-21-strategy-rethink.md`, 2026. Working notes containing the full 30-technique design space from which Table 3's twelve were drawn.

---

**Availability.** Source code and reference implementation at https://github.com/opcastil11/planckbot under Apache 2.0. The triples corpus is not distributable (§ 8.3).

**Contact.** `opcastil11` on GitHub.
