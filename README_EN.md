<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="site/assets/wordmark-dark.svg">
  <img src="site/assets/wordmark-light.svg" alt="CoAgentia" width="460">
</picture>

<h3>A local-first multi-agent collaboration platform where AI agents work like teammates</h3>

<p>From a single request to a live URL — everything happens inside one IM.</p>

<p>
  <img alt="pytest" src="https://img.shields.io/badge/pytest-1122_passed-46D98A?style=flat-square&labelColor=1A1D18">
  <img alt="vitest" src="https://img.shields.io/badge/vitest-512_passed-46D98A?style=flat-square&labelColor=1A1D18">
  <img alt="pyright" src="https://img.shields.io/badge/pyright-0_errors-46D98A?style=flat-square&labelColor=1A1D18">
  <img alt="milestones" src="https://img.shields.io/badge/MVP-M1–M8_shipped-E8763A?style=flat-square&labelColor=1A1D18">
</p>
<p>
  <img alt="python" src="https://img.shields.io/badge/Python-3.12+-E8763A?style=flat-square&labelColor=1A1D18&logo=python&logoColor=E9E7DC">
  <img alt="fastapi" src="https://img.shields.io/badge/FastAPI-server-E8763A?style=flat-square&labelColor=1A1D18&logo=fastapi&logoColor=E9E7DC">
  <img alt="react" src="https://img.shields.io/badge/React-web-E8763A?style=flat-square&labelColor=1A1D18&logo=react&logoColor=E9E7DC">
  <img alt="sqlite" src="https://img.shields.io/badge/SQLite-≥3.35-E8763A?style=flat-square&labelColor=1A1D18&logo=sqlite&logoColor=E9E7DC">
  <img alt="platform" src="https://img.shields.io/badge/Windows-single--machine_MVP-A5A69A?style=flat-square&labelColor=1A1D18">
</p>

<p>
  <a href="#core-capabilities"><b>Capabilities</b></a> · <a href="#architecture"><b>Architecture</b></a> · <a href="#repository-layout"><b>Layout</b></a> · <a href="#quick-start"><b>Quick start</b></a> · <a href="site/index.html"><b>Landing page</b></a>
</p>

<p>
  <b>English</b> | <a href="README.md">中文</a> | <a href="README_JA.md">日本語</a>
</p>

<code>request → decompose → confirm → parallel delivery → diff/preview review → merge → one-click deploy → cost accounting</code>

</div>

---

**CoAgentia** is a **contract-driven, orchestratable, guardrail-visible** multi-agent collaboration workbench. Humans and AI agents talk in channels like coworkers; an Orchestrator decomposes requests into a task graph, multiple agents deliver code in parallel; you review in diffs and live previews, click once to merge, once more to deploy — and get back a URL plus the token bill for the job.

## Why an IM

Most agent-orchestration tools look like "a workflow canvas plus a log console". CoAgentia's bet: **the natural shape of collaboration is conversation**. Channels, threads, @mentions, a task board — however human teams collaborate, human-agent teams should too. The canvas, the guardrails, and the ledger live next to the conversation, instead of stuffing the conversation into a console.

## Core capabilities

| Domain | What it does |
| --- | --- |
| **IM foundation** | Channels / DMs / threads / @mentions / files / read receipts; WS event-driven, no refresh ever |
| **Tasks** | One-click message-to-task, claim / assign / state machine, board, full-text search, activity stream |
| **Orchestration canvas** | React Flow dependency graph; cycle-proof write transactions; live `blocked` derivation + delivery-layer gating; force-start override |
| **Orchestrator decomposition** | @Orchestrator to decompose: proposal → structural validation (14 rules) → draft-layer confirmation (adjustable per item) → atomic landing; incremental deltas mid-flight |
| **Delivery chain** | `writes_code` tasks auto-derive git worktrees; diff viewer, **long-running dev-server live preview** (side-by-side iframe), DAG-ordered `merge --no-ff`, conflicts auto-filed back as tasks |
| **One-click deploy** | Human click or agent `trigger_deploy`, dual channel; streaming deploy logs, serialized with 409 protection, result card with URL |
| **Guardrails** | Silence-reminder escalation, freshness gate + held-draft three-way controls, summary-round guardrails (anti-spin), quality-signal feedback loop |
| **Cost accounting** | `GET /usage` at workspace / agent / task levels; per-deploy token summaries; honest coverage labeling — never fake currency conversion |
| **Dual runtime** | Claude Code and Codex CLI adapters; per-channel notification policies, cron, skill allowlists, role templates and a three-step team wizard |

## Architecture

```
┌──────────────┐    REST + WebSocket    ┌───────────────────┐
│   apps/web   │ <====================> │    apps/server    │
│ React + Vite │                        │ FastAPI + SQLite  │
└──────────────┘                        └─────────┬─────────┘
                                                  │  WS frames (Contract D)
                                        ┌─────────┴─────────┐
                                        │    apps/daemon    │
                                        │   executor only   │
                                        └─────────┬─────────┘
                                                  │  stdio / JSON-RPC
                                        ┌─────────┴─────────┐
                                        │ Claude Code/Codex │
                                        │   CLI, MCP x16    │
                                        └───────────────────┘
```

- **The server is the sole adjudicator**: gating, DAG ordering, conflict handling, and trigger decisions all live in the server; the daemon only executes.
- **Contracts first**: entities / REST / WS events / daemon frames / constant catalogs are versioned in contract documents before code — the code fills in the contract. `packages/contracts` (Pydantic v2) is the single type source in the repo; TS types are generated via `pnpm gen` and must never be hand-edited.
- **Isomorphic kernels, single source**: three deterministic kernels (graph derivation / fingerprint / decomposition validation) = authoritative Python + mirrored TypeScript + golden fixtures run on both sides, compared byte-for-byte — semantic drift turns the build red.

## Repository layout

| Path | Contents |
| --- | --- |
| `packages/contracts` | [Single source] Pydantic v2 models + deterministic `kernel/` |
| `packages/contracts-ts` | [Generated] TS types, regenerate via `pnpm gen` |
| `packages/fixtures` | Sample data + `golden/` cross-language fixtures |
| `apps/server` | FastAPI main service (REST / WS / orchestration / guardrails / deploy) |
| `apps/daemon` | Agent executor (CLI adapters / preview / deploy runners) |
| `apps/web` | React frontend (Afterglow design language) |
| `apps/mock-server` | Contract-driven mock (fixtures over REST + WS) |
| `site/` | Project landing page (static, single file) |

## Quick start

**Requirements**: Windows (current single-machine MVP target) · Python ≥ 3.12 + [uv](https://docs.astral.sh/uv/) · Node.js + pnpm 10 · SQLite ≥ 3.35 · a logged-in [Claude Code](https://claude.com/claude-code) CLI (needed to run real agents)

```bash
# Install
uv sync
pnpm install

# Terminal 1: backend
uv run coagentia-server            # http://127.0.0.1:8787

# Terminal 2: frontend
pnpm --filter @coagentia/web dev   # http://127.0.0.1:5173 (proxies /api -> 8787)
```

Single-process same-origin mode: run `pnpm --filter @coagentia/web build`, then `uv run coagentia-server` and open `http://127.0.0.1:8787`.

## Development gates (all green or it doesn't count)

```bash
uv run pytest -q                    # backend + contract tests (currently 1122 passed)
pnpm -F @coagentia/web test         # frontend vitest (currently 512)
pnpm typecheck                      # pyright 0 errors + double tsc
uv run ruff check .
pnpm gen                            # git diff must be empty afterwards (deterministic codegen)
pnpm -F @coagentia/web build
```

## Status

All planned MVP milestones (M1–M8) have shipped: IM foundation → tasks → contracts & canvas → guardrails → dual runtime & templates → delivery chain → decomposition chain → preview / deploy / orchestration quality line. Every milestone closed with isolated-environment live verification (real server + real daemon + real git subprocesses) plus adversarial code review.

Known boundaries: single machine, single workspace, single-user trust model; multi-user / multi-tenant / multi-machine are future roadmap.
