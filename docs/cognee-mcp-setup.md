# Cognee MCP Server Setup Runbook

Persistent shared memory for Claude across Code, Desktop, and claude.ai (web).

## Prerequisites

| Requirement | Install (macOS) | Notes |
|---|---|---|
| Python 3.11+ | `brew install python@3.11` | |
| `uv` | `brew install uv` | Cognee uses uv for dependency management |
| Git | `brew install git` | |
| Ollama (optional) | `brew install ollama` | Only if using local embeddings to avoid API costs |

You do **not** need PostgreSQL for basic usage. Cognee defaults to SQLite + LanceDB locally.

## 1. Install Cognee MCP

```bash
git clone https://github.com/topoteretes/cognee.git
cd cognee

# Install both core + MCP into one venv (avoids ModuleNotFoundError)
python -m venv .venv
source .venv/bin/activate
uv pip install -e . -e ./cognee-mcp
```

> **Do not** run `uv sync` inside `cognee-mcp/` alone — it misses core cognee dependencies (known issue #1223).

## 2. Configure Environment

Create `cognee-mcp/.env`:

### Option A: OpenAI embeddings (simplest, ~$0.01/day for personal use)

```env
LLM_API_KEY="sk-your-openai-api-key"
```

### Option B: Fully local with Ollama (zero cost)

```bash
ollama pull llama3.1:8b
ollama pull avr/sfr-embedding-mistral:latest
```

```env
LLM_API_KEY="ollama"
LLM_PROVIDER="ollama"
LLM_MODEL="llama3.1:8b"
LLM_ENDPOINT="http://localhost:11434/v1"
EMBEDDING_PROVIDER="ollama"
EMBEDDING_MODEL="avr/sfr-embedding-mistral:latest"
EMBEDDING_ENDPOINT="http://localhost:11434/api/embeddings"
EMBEDDING_DIMENSIONS=4096
HUGGINGFACE_TOKENIZER="Salesforce/SFR-Embedding-Mistral"
```

> Cognee recommends 32B+ models for quality knowledge graphs. 7B/8B models work but produce lower-quality output.

### Shared memory across clients

By default, each MCP client gets its own dataset namespace. To share memory between Claude Code, Desktop, and Web:

```env
COGNEE_MCP_AGENT_SCOPED=false
```

## 3. Run the MCP Server

**For Claude Code + Desktop (stdio):** No separate server needed — Claude launches it automatically via the config below.

**For claude.ai web (HTTP):** Requires a running server reachable from the internet:

```bash
cd /path/to/cognee/cognee-mcp
source ../.venv/bin/activate
python src/server.py --transport http --host 0.0.0.0 --port 8001 --path /mcp
```

> Use port 8001 to avoid conflicts with BlueLiner's FastAPI dev server on 8000.

For local dev with claude.ai, tunnel with ngrok:

```bash
ngrok http 8001
# Use the ngrok HTTPS URL as your connector URL
```

## 4. Configure Claude Code

```bash
claude mcp add cognee -s user -- uv --directory /absolute/path/to/cognee/cognee-mcp run cognee
```

Or manually edit `~/.claude.json`:

```json
{
  "mcpServers": {
    "cognee": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/cognee/cognee-mcp",
        "run",
        "cognee"
      ],
      "env": {
        "ENV": "local",
        "TOKENIZERS_PARALLELISM": "false",
        "LLM_API_KEY": "sk-your-key-here",
        "COGNEE_MCP_AGENT_SCOPED": "false"
      }
    }
  }
}
```

Verify: `claude mcp list` — should show `cognee` connected with tools: `remember`, `recall`, `forget`.

## 5. Configure Claude Desktop

Edit config (Settings > Developer > Edit Config):

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "cognee": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/cognee/cognee-mcp",
        "run",
        "cognee"
      ],
      "env": {
        "ENV": "local",
        "TOKENIZERS_PARALLELISM": "false",
        "LLM_API_KEY": "sk-your-key-here",
        "COGNEE_MCP_AGENT_SCOPED": "false"
      }
    }
  }
}
```

> If `uv` is not found, use the full path: replace `"command": "uv"` with `"command": "/opt/homebrew/bin/uv"` (run `which uv` to find it).

Restart Claude Desktop completely after saving.

## 6. Configure claude.ai (Web) — Max Plan

claude.ai connects from Anthropic's servers, so the MCP server must be publicly accessible (not localhost).

1. Start the HTTP server on a public host or use ngrok (see step 3)
2. In claude.ai: **Profile/Settings > Connectors > Add Connector**
3. Enter your public URL: `https://your-server.example.com/mcp`
4. Toggle the connector on in any conversation via the **"+" > Connectors** menu

## 7. Test the Memory Cycle

In any Claude client:

1. **Store:** "Remember in Cognee that BlueLiner uses a three-tier caching strategy: in-process LruTtl, Postgres snapshots, and Service Worker."
2. **Retrieve:** "Recall from Cognee what you know about BlueLiner's caching."
3. **Cross-client test:** Open a different Claude client and ask: "Search Cognee for BlueLiner."
4. **Clean up (optional):** "Forget everything in Cognee about BlueLiner."

Debug tool: `cd cognee-mcp && bash mcp dev src/server.py` opens a web inspector at `http://localhost:5173`.

## Common Gotchas

1. **`ModuleNotFoundError`** — Install from repo root with `uv pip install -e . -e ./cognee-mcp`, not from `cognee-mcp/` alone
2. **Port 8000 conflict** — BlueLiner's FastAPI uses 8000; use `--port 8001` for Cognee
3. **Ollama unresponsive** — Reduce `EMBEDDING_BATCH_SIZE` to 1-10 in `.env` (default 36 overwhelms GPU memory)
4. **`TOKENIZERS_PARALLELISM` warnings** — Set `TOKENIZERS_PARALLELISM=false` in env
5. **SSE 405 errors** — Use HTTP transport (`/mcp`) instead of SSE (`/sse`); known bug in some versions
6. **Memory not shared across clients** — Set `COGNEE_MCP_AGENT_SCOPED=false`; default scopes memory per-client
7. **claude.ai can't reach localhost** — The web client connects from Anthropic's cloud; use ngrok or a VPS
8. **Config changes not taking effect** — Full restart required for both Claude Desktop and Claude Code
