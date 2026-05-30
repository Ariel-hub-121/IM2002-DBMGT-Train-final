# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TransitFlow** is an educational AI-powered chatbot for a dual-network transit operator (metro + national rail). The project demonstrates a three-database architecture working together under a single LLM orchestration layer. Students design schemas, implement query functions, and seed data; the agent/UI scaffolding is pre-built.

## Key Commands

### First-Time Setup
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
docker compose up -d
ollama pull llama3.2:1b        # if using Ollama (default)
ollama pull nomic-embed-text   # embedding model
```

### Seeding Data
```powershell
python skeleton/seed_postgres.py   # relational tables
python skeleton/seed_vectors.py    # policy RAG embeddings
python skeleton/seed_neo4j.py      # graph nodes/edges
```

### Run the App
```powershell
python skeleton/ui.py
# Opens at http://localhost:7860
```

### Reset After Schema Changes
```powershell
docker compose down -v && docker compose up -d
# Then re-run all seed scripts
```

### Database UIs
- **pgAdmin** (PostgreSQL): http://localhost:5051 — `admin@admin.com` / `admin`
- **Neo4j Browser**: http://localhost:7475 — `neo4j` / `transitflow`

## Architecture

### Query Pipeline
```
User message → Gradio UI (skeleton/ui.py)
             → Agent (skeleton/agent.py)  [LLM picks tools]
             → databases/relational/queries.py  → PostgreSQL :5433
             → databases/graph/queries.py       → Neo4j :7688
             → query_policy_vector_search()     → pgvector (in PostgreSQL)
             → normalizer → LLM → final answer → UI
```

The agent reads the user's question, selects zero or more query tools, executes them, normalizes the results to key-value text, then passes everything to the LLM for a final response. The "debug panel" in the UI sidebar shows tool selections and raw results.

### Three Databases

| Database | Port | Purpose |
|---|---|---|
| PostgreSQL 16 + pgvector | 5433 | Relational data (stations, schedules, bookings, users, payments) + vector RAG (policy docs) |
| Neo4j 5 | 7688 | Graph network (route finding, delay ripple, interchange paths) |
| pgAdmin 4 | 5051 | PostgreSQL browser UI |

### Student-Editable Files
```
databases/
  relational/
    schema.sql      ← DDL; design tables here
    queries.py      ← SQL read (query_*) and write (execute_*) functions
  graph/
    seed.cypher     ← Cypher setup (or implement via seed_neo4j.py)
    queries.py      ← Cypher read functions
skeleton/
  seed_postgres.py  ← load train-mock-data JSON into PostgreSQL
  seed_neo4j.py     ← load station/link data into Neo4j
```

Everything else under `skeleton/` is pre-built scaffolding; modify only to extend.

### Tool Categories (skeleton/agent.py)
- **Relational:** `query_national_rail_availability`, `query_metro_schedules`, `query_available_seats`, `query_user_bookings`, `execute_booking`, `execute_cancellation`
- **Graph:** `query_shortest_route`, `query_cheapest_route`, `query_alternative_routes`, `query_interchange_path`, `query_delay_ripple`
- **Vector:** `query_policy_vector_search` (RAG over refund/ticket policy documents)

## Schema Conventions

- **Function prefixes:** `query_` = read-only → `list[dict]`; `execute_` = write → `tuple[bool, dict|str]`
- **SQL parameters:** always `%s` placeholders; never f-string user input into queries
- **Connections:** use `_connect()` helper (relational) and `_driver()` helper (graph) — never open raw connections
- **Naming:** `snake_case` for all tables and columns; VARCHAR business IDs (e.g., `"NR01"`, `"BK-ABC123"`) for external-facing keys; `BIGSERIAL` surrogate keys for internal FK references
- **Soft deletes:** use `is_active` flag; never hard-delete user records (audit trail)
- **Temporal columns:** `created_at TIMESTAMPTZ DEFAULT now()`, `cancelled_at`, `travelled_at` for audit history
- **FK delete strategies:** explicit `ON DELETE RESTRICT / CASCADE / SET NULL` — no reliance on defaults

## LLM Provider

Configured via `.env` (copy from `.env.example`). Two options:

- **Ollama** (default, local): `llama3.2:1b` + `nomic-embed-text` — embeddings are 768-dim
- **Gemini** (faster, free API key required): embeddings are 3072-dim

**Team must agree on one provider before seeding vectors.** Embedding dimensions differ and are incompatible at query time.

## Source Data

All mock data lives in `train-mock-data/` as JSON files. Study these before designing schemas — they define the shape and relationships of stations, schedules, seat layouts, users, bookings, and policy documents.

## Git & GitHub

- **Target repository for all PRs:** `Ariel-hub-121/IM2002-DBMGT-Train-final` (not the upstream `NCUIM-Lab710-Teaching` org)
- Always use `--repo Ariel-hub-121/IM2002-DBMGT-Train-final` when running `gh pr create`

## Team Coordination Rules

- **Schema-first:** agree on and commit `databases/relational/schema.sql` before any teammate implements query functions
- **Docker volumes are local:** after schema changes, every teammate must `docker compose down -v && docker compose up -d` and re-seed
- **Never commit:** `.env`, `.venv/`, or Docker volume data
- **AI session context:** paste `AI_SESSION_CONTEXT.md` at the start of any AI session to maintain consistent schema/design decisions across team members
