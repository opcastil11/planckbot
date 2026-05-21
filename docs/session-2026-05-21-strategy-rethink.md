# Session 2026-05-21 — Repensar la estrategia de PlanckBot post-reporte Orquesta

Notas de trabajo para continuar luego. Origen: revisión del reporte de feasibility que Orquesta produjo sobre PlanckBot, y lluvia de ideas sobre dónde meter realmente el valor del producto.

Reporte original: `/home/kai/Escritorio/PROGRAMACION/orquesta/docs/planckbot-feasibility-report.md`

---

## 1. Qué dijo el reporte de Orquesta (resumen ejecutivo)

Evaluaron PlanckBot como capa de optimización de tokens para su plataforma. Conclusiones:

- Capa de observación (Layer A): funciona como anuncia. 2,053 triples capturados sin fricción.
- Compresión (Layer B): el 33.6% de ahorro medido se infla por falsos positivos del matcher; estimación honesta 15-22% de tool-output tokens, ~8-12% del bill total.
- Bloqueador arquitectónico: tools nativas de Claude Code (Read, Bash, Edit, Grep, Glob, Write) bypassean el proxy MCP por diseño. PostToolUse hook *no puede reescribir output* (solo añadir `additionalContext` ≤10K chars). PreToolUse modifica input, no output.
- Decisión: no envían PlanckBot como compresor runtime. Absorben la idea de triples + citation tracking como feature interno de "Token Analytics".

Lo que reabriría su decisión:
1. Anthropic añade output-rewrite a PostToolUse.
2. PlanckBot envía un SDK o patch de Claude Code que intercepte tools nativas.
3. Existe un servidor MCP Bash drop-in de alta calidad.

## 2. Verificación técnica

Verifiqué los claims del reporte contra el código y todos cuadran:

- Comentario en `src/planckbot/ingest/claude_code.py:7-11` admite explícitamente que tools nativas bypassean el proxy.
- `_STRUCTURAL_NOISE = {"dir", "file", "http", "https"}` en `reference_tracker.py:23` es lo que dice el reporte.
- Lógica del matcher `token` (líneas 71-80 de `reference_tracker.py`): comparte tokens ≥3 chars con la referencia, exactamente como describen.
- Modo `semantic` ya existe (`reference_tracker.py:82-154`) con fallback graceful a token. CLAUDE.md lo lista en "Not yet built" — **está desactualizado**, está implementado pero no es default.

## 3. El error de framing que el reporte expuso

El reporte ataca una sola encarnación táctica: "intercepto MCP + reescribir output via PostToolUse". Pero esa nunca fue la filosofía de PlanckBot — fue una implementación de ejemplo.

**Filosofía real:** *Un modelo chico que aprende del uso real para evitarle trabajo al modelo grande.* El punto de intervención no está fijado a PostToolUse ni a MCP namespacing. Puede ser cualquier lugar donde la decisión del modelo chico sea actuable.

El reporte confirmó implícitamente que las premisas filosóficas funcionan:
- Capa de observación produce data limpia ✓
- Triples son la primitiva correcta ✓
- Data flywheel señala dónde se queman tokens ✓
- Hay patrones repetitivos explotables en uso real ✓

Lo único que invalidó: una decisión táctica de implementación.

## 4. Direcciones viables identificadas en esta sesión

Ordenadas por mi confianza en viabilidad/impacto. Mecanismos agrupados.

### Plano temporal (predecir / cachear)

**A. Cache-deny via PreToolUse hook.** Cuando Claude va a llamar `Read foo.py`, el hook consulta PlanckBot. Si hay triple reciente del mismo path sin cambios en disco (mtime), deniega con `reason="cache hit, contenido: <X>"`. El modelo lee el reason como contexto y continúa sin pagar la Read. Funciona con tools nativas porque PreToolUse las cubre. El LoRA cambia de rol: decide si el cache hit es safe dado el prompt actual (señal binaria, más fácil que "qué líneas mantener").

**B. Cache de comandos idempotentes (Bash).** `git log`, `ls -la`, `git status`, `cat <file unchanged>`, etc. son safe-to-cache con TTL. Adapter aprende qué comandos están en esa lista. Conservador 10-15% de Bash sin riesgo. Mismo mecanismo PreToolUse que A.

**C. Pre-fetch especulativo.** Tiny model predice el siguiente tool call basado en prompt + recent triples. Si confianza > umbral, ejecuta el tool antes y lo inyecta via `UserPromptSubmit` como contexto pre-cargado. Speculative decoding aplicado a tools. Round-trip eliminado cuando acierta; costo bajo cuando falla.

**D. Diff-based re-reads.** Mismo archivo leído 2+ veces en una sesión → el segundo Read sirve "verifica mi edit". Reemplazar por diff vs primera Read. 800 tokens → 30 tokens.

**E. Retrieval de triples pasados por embedding.** Todos los outputs almacenados con embeddings. Read/Bash semánticamente similar a algo ya visto → inyectar resultado pasado en lugar de ejecutar. Cache cross-session. Riesgo: staleness; mitigar restringiendo a tools que el adapter aprende safe.

### Plano de routing (modelo correcto para el prompt)

**F. Model routing dinámico.** Classifier tiny-model entrenado sobre prompts → decide Haiku vs Sonnet vs Opus por prompt. "Rename this var", "fix typo" no necesitan Opus. PlanckBot ya tiene la data (triples enlazan a sesiones con modelo usado). Wrapper de orquestación hace el routing. **Layer A llevado al nivel macro.**

**G. Predicción de fallo antes de ejecutar.** Detectar patrón "input → siguiente input es corrección del mismo tool" → classifier "este tool_call va a fallar". PreToolUse advierte: "este Bash probablemente falle por X". Ahorra round-trip error + retry. Variante de Layer A: el modelo chico valida, no ejecuta.

### Plano de authoring / contexto

**H. Compresión de prompt input (no output).** Modelo tiny comprime prompt del user antes de llegar a Claude. Señal de entrenamiento: ¿la respuesta de Claude cambió cuando se comprimió? Trade: latencia en cada prompt. Para Orquesta (agent layer en medio) el slot está libre.

**I. Auto-prune de CLAUDE.md y skills.** PlanckBot sabe qué secciones nunca se citan en 1000 triples. Recomienda eliminaciones. "Eliminá líneas 47-62 de CLAUDE.md, ahorra ~340 tokens/turn × 200 turns/día = 68K tokens/día". Compresión en authoring-time, no runtime. Menos sexy pero deployable hoy.

**J. Conversation-level smart compaction.** Auto-compact de Claude Code es por tiempo/tamaño y borra ciegamente. PlanckBot sabe qué triples de turnos previos se citaron en turnos posteriores → compaction selectiva. "Read del turno 12 nunca se volvió a citar, comprimilo; Read del turno 7 se cita aún en turno 22, mantenelo."

### Plano de empaquetado (compound tools)

**K. Layer D síntesis (subexplotado en el reporte original).** Los compound tools sintetizados son MCP por construcción → PlanckBot los intercepta. Patrón "Read X → Edit X → Bash test" detectable en data de Orquesta (339 Read + 326 Edit + 1034 Bash en data de Chubi). Síntesis de `mcp__planckbot__patch_and_test(file, edit, test_cmd)` colapsa 3-5 round-trips a uno. Ahorro en *eliminar* tool_calls y tool_results enteros, no en comprimir ninguno. Truco para que el modelo elija la compound tool sobre las nativas: skill autogenerado + entrada en CLAUDE.md vía hook `UserPromptSubmit`.

**L. Split tools (Layer B disfrazado de Layer D).** Tool nativo devuelve mucho output mayormente irrelevante (Read 5000 líneas para usar 200). Tool sintetizado `mcp__planckbot__read_relevant(file, intent_hint)` hace Read internamente, filtra con LoRA, devuelve útil. **Mismo ahorro que Layer B prometía, arquitectónicamente viable** — el filtrado pasa dentro del MCP, no después.

## 4b. Más técnicas alineadas con la filosofía original

Continuación de §4. Aquí ideas adicionales que mantienen el principio "modelo chico aprende del uso real para evitarle trabajo al grande", explorando planos que la lluvia inicial no cubrió.

### Plano: reducción de errores y reintentos

**M. Auto-corrección de argumentos de tool.** Adapter detecta typos / errores comunes en args (glob patterns mal formados, line numbers off-by-one, paths con typos, regex inválidas) basándose en patrones "Claude emitió X, retry corrigió a Y". `PreToolUse` rewrite-ea el input antes de ejecutar. Distinto de G (predicción de fallo, que solo advierte): aquí *arregla*. Ahorra round-trip de error + retry, mejora UX percibido.

**T. Detección de prompts ambiguos / dead-end.** Modelo chico clasifica prompts como "necesita clarificación" antes de pagar tokens en Claude. Responde con "para X necesito Y" sin invocar al grande. Ahorra el round-trip completo del prompt fallido. Señal de entrenamiento: prompts que generaron respuesta tipo "could you clarify".

**V. Tool budget per-prompt y detección de loops.** Adapter predice "este prompt típicamente requiere ~3 tool calls". Si Claude va por la décima sin progreso aparente, hook avisa o aborta. Detecta loops infinitos antes de que consuman 50K tokens. La señal viene de sesiones históricas: tool count por prompt-shape vs completion status.

### Plano temporal (adiciones a §4)

**N. Streaming early-termination en Bash.** Para outputs largos (`find /`, `grep -r`, build logs), adapter decide en línea N si Claude ya tiene suficiente y trunca el stream. Equivalente a un `head -n auto` basado en contexto. Implementable como wrapper de Bash en MCP (no toca tool nativa pero ofrece alternativa MCP que el modelo prefiere via skill).

**Z. Predictive compaction.** Auto-compact tradicional dispara cuando context se llena. Predictive: el adapter predice que en N turnos más el context se va a llenar y compacta proactivamente turnos viejos *mientras el modelo está idle* (entre tool_result y tool_use). Comparte mecanismo con J pero se anticipa en vez de reaccionar.

**AA. Freshness-as-a-tool.** Expose `mcp__planckbot__check_fresh(resource)` que devuelve `{"fresh": true/false, "diff": "..."}`. Mucho más barato que re-Read completo para "ver si X cambió desde mi última Read". Adapter aprende qué resources cambian con qué frecuencia → puede responder sin tocar disco a veces.

### Plano: aprendizaje contextual / memoria

**Q. Tamaño de output adaptativo al contexto.** Adapter no comprime con regla fija — *decide el nivel de detalle por contexto del prompt*. Mismo Read de 800 tokens devuelve 50 tokens en flujo de "verifica" y 600 en flujo de "refactor". Tool output context-aware. Esto es Layer B refinado: en lugar de "una compresión por tool" es "una compresión por (tool, intent)". Combina natural con L (split tools con intent hint).

**R. Scratch pad / memo per-session.** PlanckBot mantiene memo persistente del modelo chico durante la sesión: "file X tiene estructura Y", "este proyecto usa convention Z", "el último Bash de tests falló por W". Inyectable como contexto en prompts futuros vía `UserPromptSubmit` para que Claude no re-deduzca cosas. Distinto de CLAUDE.md (que es estático y curado por humano): el memo se genera del flujo real.

**S. Anti-redundancy en multi-agent / Agent calls.** Cuando Claude spawn-ea sub-agents (subagent_type=general-purpose, Explore, etc.), los sub-agents repiten setup work: leen los mismos files, ejecutan los mismos Bash de orientación. PlanckBot detecta redundancia entre agent padre y sub-agent, short-circuit-ea con resultados ya conocidos del padre. El sub-agent recibe el contexto pre-cargado en su system prompt en lugar de tener que descubrirlo.

### Plano: empaquetado avanzado (adiciones a §4)

**W. Cross-tool result fusion.** Patrones tipo "Read + Grep" se resuelven inline en el MCP server (Read + filter local) en lugar de dos round-trips MCP. Variante de K (Layer D) pero sin sintetizar tool nuevo formal — el adapter fusiona dentro del wrapper de un tool existente.

**X. Cache de embeddings del codebase.** PlanckBot indexa el repo (chunks por archivo). Tools tipo "encontrar archivos similares a X" o "buscar implementación de Y" se resuelven local sin que Claude itere Grep + Read. El adapter no comprime — *reemplaza* el patrón. Infra adicional (vector store) pero plug-in a la SQLite existente vía sqlite-vec.

### Plano: meta-learning (sistema que aprende cómo aprender)

**BB. Threshold de confianza por (tool, contexto).** Hoy el threshold es global (0.9 default; schema v5 tiene `tuned_threshold` per-checkpoint). Más fino: threshold varía dentro de un mismo tool según contexto. Read de `package.json` (idempotente, low-stakes) puede activarse con confidence 0.6; Read de un file durante un flow de Edit (high-stakes, retry costoso) requiere 0.95. Adapter aprende el threshold óptimo en cada slot.

**CC. Counterfactual A/B training.** No entrenar solo sobre "qué se citó" sino sobre "qué hubiera pasado si comprimimos así". En observe mode, el adapter genera la compresión candidata pero Claude ve el output completo. Background batch compara: ¿qué hubiera escrito Claude con la compresión? Si responde parecido → safe label; si responde distinto → unsafe label. Esto resuelve el problema de fondo del matcher actual (que usa "lo que Claude citó" como proxy de "lo que necesitaba"). Costo: 2x inferencia en background, pero asíncrono.

**DD. Conversation graph pruning.** Las sesiones tienen estructura de árbol: algunos turnos abren ramas exploratorias que mueren. Adapter detecta ramas "muertas" (intentos descartados) y las compacta agresivamente conservando solo la rama productiva del trabajo. Más quirúrgico que J (que opera por relevancia por triple).

### Plano: dispatch / orquestación

**EE. Skill dynamic loading basado en uso.** Las skills declaradas en `~/.claude/skills/` se cargan todas siempre. PlanckBot aprende qué skills realmente se invocan post-load para qué tipo de prompts y *pre-carga solo las relevantes* via hook que reescribe el manifest expuesto. Reduce el context budget inicial sin perder capacidad.

**FF. CLAUDE.md modular con carga on-demand.** Hoy CLAUDE.md es monolítico. Splitear en `CLAUDE.md.ui`, `CLAUDE.md.cron`, `CLAUDE.md.synth` etc. Adapter decide qué módulos cargar por sesión basado en primer prompt. "Esta sesión es de UI work → cargar `.ui`, no `.cron`." Variante de I (auto-prune) pero dinámica per-session en vez de permanente.

---

## 5. Mejoras quick-win al matcher actual (constructivas del reporte)

Las 5 sugerencias de §4.5 del reporte son sensatas y baratas:

1. **Expandir ventana de referencia** de "siguiente assistant block" a "siguiente user turn" (concatenar todos los assistant blocks intermedios). Cierra la mayor causa de FP en flujos agentic.
2. **Default a `match_mode='semantic'`** en cron job de autolabel. Código ya existe — cambiar default y validar.
3. **Lista de noise tokens per-project** (top-50 identificadores frecuentes excluidos). Resuelve "every file mentions `market`".
4. **Penalizar matches con referencia corta.** Acknowledgment de 50 chars matcheando 2K chars de output ≈ FP.
5. **Métrica de calibración en dashboard:** "de N triples labeled, K tuvieron edits posteriores usando contenido descartado". Crítico para confianza del usuario.

Y los wins de marketing/docs:
- Cambiar headline "97% reduction on list_directory" por "30-50% on instrumented MCP tools" o por la filosofía completa.
- Añadir nota explícita en README: "PlanckBot intercepta tools MCP; tools nativas de Claude Code se observan vía JSONL pero no se comprimen en runtime todavía."
- Documentar flujo de labeling desde JSONL (`auto_label.py` espera una sola reference, no es obvio cómo hacerlo con JSONL).
- Actualizar CLAUDE.md: "Semantic matcher" está implementado, no en "Not yet built".

## 6. Mis apuestas si tuviera que priorizar

**Tier 1 (bajo costo, alto impacto):**
- A (cache-deny PreToolUse) + B (Bash idempotente) — comparten infra, atacan tools nativas que era el bloqueador del reporte. Esto solo recupera el caso de uso "bajar el bill de Claude Code".
- Mejoras al matcher (5 puntos arriba) — necesarias antes de cualquier nuevo training, baratas.

**Tier 2 (medio costo, alto impacto):**
- K (Layer D síntesis bien empujada) + L (split tools) — combinan el valor del Layer B original con un mecanismo que sortea el bloqueador.
- I (auto-prune CLAUDE.md) — feature monetizable hoy, no requiere LoRA en serving.

**Tier 3 (alta ambición, explorable después):**
- F (model routing) — requiere infra de wrapper que Orquesta ya tiene; podría ser un producto colaborativo.
- C (pre-fetch especulativo) — más riesgo, requiere calibración fina.

## 7. Decisiones de framing pendientes

- ¿Reposicionar README para liderar con la filosofía amplia en vez de Layer B? Probable sí.
- ¿Mercado target sigue siendo "usuarios de Claude Code" o ampliar a "agent builders sobre Anthropic SDK directo" (LangGraph, CrewAI, custom loops)? El segundo elimina el bloqueador arquitectónico pero es mercado más chico.
- ¿La pivot a Token Analytics que Orquesta hizo es complementaria o competitiva? Si es complementaria, vale conversación sobre integración (PlanckBot ofrece la layer de ejecución sobre la analytics que ellos hacen).

## 9. Resultados del Tier-1 bench (2026-05-21)

Ejecutado sobre 16,337 triples / 136 sesiones / 16 proyectos. Scope total: **4,390,921 tokens de output**. Comando: `scripts/run_bench_tier1.py --full`. Outputs persistidos en `data/bench/`.

### Ranking final

| # | Técnica | Ceiling | Cobertura | Risk | Tier |
|---|---|---|---|---|---|
| 1 | J.uncited_compaction | 98.5% | 96.3% | 100% | B (inflado) |
| 2 | L.split_tools_local | 57.8% | 13.3% | 50% | B (inflado) |
| 3 | **G.retry_detection** | **45.1%** | 37.3% | 0% | **A** |
| 4 | **K.ngram_synthesis** | **41.2%** | 69.2% | 0% | **A** |
| 5 | **A.cache_deny_read** | **13.3%** | 5.4% | 33% | **A** |
| 6 | **L.split_tools_jsonl** | **6.0%** | 1.8% | 40% | A' (honesta) |
| 7 | V.tool_budget | 5.9% | 0.1% | 100% | C |
| 8 | D.diff_reads | 3.1% | 2.3% | 0% | C |
| 9 | M.arg_autocorrect | 2.5% | 1.1% | 0% | C (subset de G) |
| 10 | N.bash_early_term | 2.3% | 2.1% | 20% | C |
| 11 | B.bash_exact_dedup | 0.4% | 1.4% | 0% | C |
| 12 | T.dead_end_prompts | 0.0% | 0.0% | 0% | C (descartar) |

### Interpretación clave

**Lo que el bench DEMOLIÓ:** la idea original de Layer B "comprimir output de tools" — cuando se mide con la señal honesta (JSONL assistant text, no in-session triple inputs), cae a **6%** de ahorro. Las cifras altas (98.5%, 58%) son artifacts del matcher triples-only que repite el bug del reporte de Orquesta.

**Lo que el bench VALIDÓ:** la filosofía amplia de PlanckBot, no la encarnación específica. Las wins reales son **eliminación**, no compresión:
- G (retry predict) → 45% techo, 0% risk
- K (Layer D síntesis) → 41% techo, 0% risk
- A (cache-deny Reads) → 13% techo, 33% risk (mitigable)

Las tres comparten un mecanismo: el modelo chico decide que **no hace falta la llamada al modelo grande**. Eso es lo que siempre fue PlanckBot — no "filtrar lo que vuelve" sino "evitar el round-trip".

### Estrategia post-bench

| Acción | Por qué | Esfuerzo |
|---|---|---|
| **Implementar A primero** | Más barato (PreToolUse + mtime + cache local). 13% ceiling con risk mitigable. Demo deployable en días. | bajo |
| **Construir G como adapter MVP** | 45% es upper-bound del clasificador ideal. Dataset positivo masivo (37% labels). Si llega a 40% precision × 60% recall → 10% ahorro real. | medio |
| **Empujar K con skill autogen** | Infra ya existe (`synth/detector.py`). Falta el bucle de adopción (skill o CLAUDE.md autogen que oriente a Claude). | medio |
| **L.jsonl → Tier-3 oracle** | Solo si A+G+K no alcanzan. 6% material pero requiere validación con llamadas Claude reales. | alto |
| **Drop J, V, T, B, N** | Marginal o artifact. No vale el esfuerzo. | n/a |

### Caveats del bench

- Mide sobre data de Claude Code only. Otros agent loops podrían tener distribución distinta.
- Output tokens dominan el scope; assistant text es 2x según el reporte de Orquesta. Cualquier "% de ahorro" del bench es sobre tool_output, no sobre bill total.
- Las cifras de G y K son ceilings del predictor/sintetizador ideal. La adopción real depende de la implementación (en G) o de Claude (en K).
- Triples-only signal subestima compresión (no ve assistant text) y sobreestima cuando triple posteriores no usan el contenido aunque Claude lo haya internalizado.

## 10. Próximos pasos concretos (cuando volvamos)

1. Bench rápido del matcher con `match_mode='semantic'` sobre las triples existentes para confirmar el reporte (esperamos menos FP y citation rate más baja pero más honesta).
2. Diseño de PreToolUse cache-deny: dónde vive el cache (mismo SQLite), qué key usa (path + mtime para Read; comando + cwd para Bash), cómo el LoRA decide safe-to-serve.
3. Prototipo de skill autogenerado que empuja a Claude hacia compound tools de Layer D.
4. Revisar si actualizar README + CLAUDE.md antes de cualquier feature work, para que el framing nuevo no se contradiga con docs viejos.

## 11. Cierre de sesión 2026-05-21 — entregables

Síntesis para retomarlo limpio en otra sesión.

### Conclusiones estratégicas (las que cambian el roadmap)

1. **El reporte de Orquesta no mata PlanckBot — mata una sola encarnación táctica.** "Intercepto MCP + reescribo output via PostToolUse en Claude Code" está cerrada por arquitectura. La filosofía amplia ("modelo chico aprende del uso real para evitarle trabajo al grande") sigue intacta y el bench la validó con data propia.

2. **Pivot del bench: elimination > compression.** Las 3 técnicas top tienen risk 0% porque no descartan contenido, eliminan llamadas: G (retry_detection, 45%), K (Layer D ngrams, 41%), A (cache-deny, 13%).

3. **El "97%" del marketing es falso a la luz de la señal honesta.** L.split_tools_jsonl mide 6% real cuando se usa el assistant_text del JSONL en vez del input de triples posteriores. El "98%" de J.uncited_compaction es artifact del mismo bug que el reporte de Orquesta diagnosticó en su matcher — lo replicamos al medir solo con triples.

4. **El matcher actual no es safe para entrenar adapters.** Casos individuales muestran outputs grandes droppeados al 99% por un solo token compartido con un ack genérico. Antes de cualquier LoRA serio van las 5 sugerencias del reporte de Orquesta §4.5.

5. **La capa de observación es lo más maduro y vendible.** 16,395 triples / 16 proyectos / parse 100% / match 99.98% / redacción de secretos funcionando. Orquesta lo absorbió como "Token Analytics" por algo.

### Infra construida en esta sesión (en repo, sin commit todavía)

| Pieza | Path | Estado |
|---|---|---|
| Ingest masivo | `scripts/bulk_ingest_jsonl.py` | Corrido — 14,058 triples nuevas en 16 proyectos |
| Redacción de secretos | `src/planckbot/ingest/redact.py` + wire en `claude_code.py` | 58 triples flagged, 9 patrones detectados |
| Bench harness | `src/planckbot/bench/` (harness + metrics + datasets + references) | Tests 20/20 |
| 12 Techniques | `src/planckbot/bench/techniques.py` | Tests 17/17 |
| Runner | `scripts/run_bench_tier1.py` | Corrido; outputs en `data/bench/` |
| Schema v8 | `db/migrations.py` + `tool_cache` table | Migración additive aplicada |
| ToolCacheStore | `src/planckbot/tools/cache.py` | Tests 17/17 |
| Cache hook handler | `src/planckbot/hooks/cache_hook.py` | Tests 20/20 |
| Hook entrypoint | `src/planckbot/hooks/entrypoint.py` + `planckbot-hook` console_script | Instalado en `.venv/bin/`, smoke OK |
| Suite total | — | **432/432 verde** |

### Lo que sigue sin probar (incógnita empírica clave)

Cómo trata Claude el `additionalContext` cuando viene con `permissionDecision: deny`. Los docs son silenciosos. Resoluble en 5 minutos registrando el hook en `~/.claude/settings.json` + un Read repetido. Si el path documentado no funciona, fallback al campo undocumented `updatedInput` (redirigir Read a un temp file con el contenido cacheado).

### Próxima sesión — orden sugerido

1. Decidir si activar el hook en `~/.claude/settings.json` (snippet en docstring de `scripts/planckbot-hook.py`). Probar live + leer log de debug.
2. Si funciona deny+additionalContext → marcar A como deployable y mover a G (entrenar classifier de retry sobre los triples ya labeleadas por el bench).
3. Si no funciona → switch a `updatedInput` redirect (cambio de ~10 líneas en `cache_hook.py`).
4. K (Layer D adoption) queda como tercer track — más experimental porque depende de cómo el modelo elige tools.
5. Update de README + framing público antes del próximo cliente externo. CLAUDE.md ya quedó actualizado en esta sesión.

### Memoria

- `~/.claude/projects/-home-kai-Escritorio-PROGRAMACION-planckbot/memory/project_planckbot.md` actualizado con el pivot, los winners reales, y la ubicación de la infra.
- `~/.claude/projects/-home-kai-Escritorio-PROGRAMACION-planckbot/memory/MEMORY.md` ya apuntaba ahí; no requiere cambio.
