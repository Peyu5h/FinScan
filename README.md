# FinScan — Financial Document Analyzer

A CrewAI-based system that analyzes financial documents (earnings reports, 10-Ks, balance sheets) using a pipeline of four AI agents. Upload a PDF, get back a structured breakdown with key metrics, investment outlook, and risk assessment.

Live:

---

## Bugs Found & Fixed

### Deterministic Bugs

**`requirements.txt`**

- `numpy==1.26.4` doesn't build on Python 3.13 — removed the pin, let crewai resolve it.
- `crewai-tools==0.47.1` pulls `embedchain` which has no py3.13 wheel — removed entirely and rebuilt tools using `crewai.tools.BaseTool` directly.
- Pinned `pydantic==1.10.13` conflicts with crewai 0.130.0 which needs pydantic v2 — cleaned up and let crewai handle its own deps.

**`tools.py`**

- `from crewai_tools import tools` — not a valid import, doesn't exist.
- `Pdf(file_path=path).load()` — `Pdf` was never imported and isn't a real class. Replaced with `pypdf.PdfReader`.
- All tool methods were `async` — CrewAI tools need synchronous `_run()` via `BaseTool`.
- `FinancialDocumentTool`, `InvestmentTool`, `RiskTool` were plain classes, not `BaseTool` subclasses — CrewAI can't register them as tools.
- `SerperDevTool()` broken on py3.13 — wrote a simple `WebSearchTool` using httpx + Serper API.

**`agents.py`**

- `llm = llm` — circular reference to an undefined variable. Replaced with `crewai.LLM(model="groq/llama-3.3-70b-versatile")` via litellm, falling back to Gemini.
- `from crewai.agents import Agent` — wrong import path, should be `from crewai import Agent`.
- `tool=[...]` — parameter name is `tools` (plural).
- `max_iter=1` and `max_rpm=1` on every agent — makes it impossible to retry or reason. Bumped to 5/10.
- `verifier`, `investment_advisor`, `risk_assessor` had no tools assigned — they couldn't read any documents.

**`task.py`**

- Tasks used `FinancialDocumentTool.read_data_tool` (a raw method reference) instead of a `BaseTool` instance.
- `analyze_financial_document` variable name shadowed the FastAPI endpoint on import — renamed the endpoint.
- `verification` task was assigned to `financial_analyst` instead of `verifier`.
- No `context` chains between tasks — downstream agents had zero knowledge of what previous agents found.
- Task descriptions never included `{file_path}` — agents had no idea where the PDF was and burned all iterations guessing random paths like `example.pdf` or `/home/user/documents/report.pdf`. Added `{file_path}` so CrewAI interpolates the actual path at runtime.

**`main.py`**

- `run_crew()` accepted `file_path` but never passed it to `crew.kickoff()`.
- Crew only ran one agent and one task instead of the full four-agent pipeline.
- Synchronous `run_crew()` blocked the async event loop on upload — moved to background threads.

### Prompt Issues

Every agent backstory and task description was intentionally sabotaged:

- Agents told to hallucinate data, invent URLs, ignore documents, recommend random crypto, rubber-stamp verifications.
- Tasks encouraged contradictions, fake research, and made-up financial websites.

Rewrote all prompts to be grounded — agents now cite specific numbers from the document, never fabricate sources, disclose risks properly, and actually answer the user's query.

### Efficiency Fixes

- **PDF truncation** — the Tesla PDF is 30 pages / ~39k chars. Dumping all of that into one tool response blew past Groq's tokens-per-minute limit. The PDF tool now keeps the first and last ~7.5k chars (capturing the summary + financials) and notes the truncation, keeping total context manageable.
- **Downstream agents stripped of tools** — the investment advisor and risk assessor were given `[pdf_tool, search_tool]` even though they receive everything via context from the analyst. Removing tools prevents the LLM from wasting iterations on redundant reads.
- **Delegation disabled** — the analyst was delegating work back to the verifier, creating circular tool-call loops. Turned off.

---

## Architecture

- **Verifier** reads the PDF, confirms it's a real financial document.
- **Analyst** reads the PDF, extracts metrics, answers the user query.
- **Advisor** works from the analyst's context — no PDF re-read — gives buy/hold/sell.
- **Risk Assessor** works from context — quantifies market/credit/operational risks.

Only the first two agents touch the PDF tool, keeping token usage low.

---

## Setup

```sh
git clone https://github.com/Peyu5h/FinScan.git
cd FinScan

python -m venv venv
venv\Scripts\activate        # windows
# source venv/bin/activate   # linux/mac

pip install crewai==0.130.0
pip install -r requirements.txt

cp .env.example .env
# add at least one LLM key
```

**Get your keys:**

- [Groq](https://console.groq.com/keys) - preferred, generous free tier, llama-3.3-70b
- [Cloudflare Workers AI](https://dash.cloudflare.com/) — fallback, llama-3.3-70b-fp8-fast (needs account ID + API token)
- [Gemini](https://aistudio.google.com/apikey) — second fallback
- [Serper](https://serper.dev) — optional, enables web search

The LLM fallback chain is: **Groq => Cloudflare Workers AI => Gemini**. You only need one.

**Run:**

```sh
python main.py
```

- API - http://localhost:8000
- Test UI - http://localhost:8000/ui
- API Docs — http://localhost:8000/docs

---

## API

| Method | Endpoint            | Description                                   |
| ------ | ------------------- | --------------------------------------------- |
| GET    | `/`                 | Health check                                  |
| POST   | `/analyze`          | Upload a PDF + optional query, returns job_id |
| POST   | `/analyze/sample`   | Run on bundled Tesla PDF                      |
| GET    | `/status/{job_id}`  | Poll job status and results                   |
| GET    | `/history?limit=20` | List past analyses                            |
| GET    | `/ui`               | Testing interface                             |
| GET    | `/docs`             | Interactive API docs (Scalar)                 |

**Upload a PDF:**

```sh
curl -X POST http://localhost:8000/analyze \
  -F "file=@report.pdf" \
  -F "query=What are the key revenue drivers?"
```

**Poll for results:**

```sh
curl http://localhost:8000/status/{job_id}
```

**Use the sample PDF:**

```sh
curl -X POST http://localhost:8000/analyze/sample
```

Response statuses: `pending` => `running` => `done` / `failed`

## Database Integration

All analysis jobs persist to `data/finscan.db` (SQLite via SQLAlchemy). Stores job ID, filename, query, status, full result, error, agent logs, processing duration, and timestamps. Query past results via `GET /history`.

## Concurrent Request Handling

Each crew pipeline runs in a background thread — the API never blocks. Clients submit a job and poll `/status/{job_id}`. Multiple analyses can run in parallel.

## Live Agent Logs

The test UI at `/ui` has a "Show agent logs" toggle that streams the agent thinking process in real time while analysis runs. Stdout from the crew thread is captured into an in-memory buffer, served live via the `logs` field in `/status/{job_id}`, and persisted to the DB on completion.

---

## Tech Stack

- **CrewAI 0.130.0** - agent orchestration
- **Groq / Cloudflare Workers AI / Gemini** - LLM providers
- **FastAPI** + Uvicorn
- **pypdf** - PDF text extraction with smart truncation
- **SQLAlchemy** + SQLite - job + logs persistence
- **Scalar** - interactive API docs at `/docs`
- **httpx** - web search via Serper.dev
- **threading** - background pipeline with stdout log capture
