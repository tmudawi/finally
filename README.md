# FinAlly — AI Trading Workstation

An AI-powered trading workstation: live streaming prices, a simulated $10k portfolio, and an LLM chat assistant that can analyze positions and execute trades. Built entirely by coding agents as a capstone for an agentic AI coding course.

## Quick Start

```bash
cp .env.example .env          # then add your OPENROUTER_API_KEY
./scripts/start_mac.sh        # macOS/Linux; add --build to rebuild
.\scripts\start_windows.ps1   # Windows; add -Build to rebuild
```

Open http://localhost:8000. Stop with `scripts/stop_mac.sh` or `scripts\stop_windows.ps1` (data is kept in the `finally-data` volume).

Alternatives: `docker compose up --build`, or

```bash
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | For chat | OpenRouter API key for the AI assistant |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key, Stocks Starter plan or above; empty uses the simulator |
| `LLM_MOCK` | No | `true` for deterministic mock LLM responses (testing) |
| `DB_PATH` | No | SQLite path; defaults to `db/finally.db` (`/app/db/finally.db` in Docker) |

## Architecture

One container on port 8000: FastAPI (Python/uv) serves the REST API, SSE price stream and the Next.js static export. SQLite persists in a Docker volume. Market data comes from a built-in GBM simulator or the Massive API. Chat uses LiteLLM via OpenRouter (Cerebras).

## Project Structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project
├── planning/    # Project documentation and agent contracts
├── test/        # Playwright E2E tests
├── db/          # SQLite volume mount (runtime)
└── scripts/     # Start/stop helpers
```

## License

See [LICENSE](LICENSE).
