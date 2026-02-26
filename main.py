import io
import os
import sys
import threading
import time
import uuid
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from crewai import Crew, Process
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from scalar_fastapi import get_scalar_api_reference
from sqlalchemy.orm import Session

from agents import financial_analyst, investment_advisor, risk_assessor, verifier
from db import SessionLocal, create_job, get_db, get_job, init_db, list_jobs, update_job
from task import (
    analyze_financial_document,
    investment_analysis,
    risk_assessment,
    verify_document,
)

app = FastAPI(
    title="FinScan",
    description="Financial document analyzer powered by CrewAI",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# in-memory log buffer per job, cleared when job finishes
_job_logs: dict[str, list[str]] = {}
_logs_lock = threading.Lock()


def _append_log(job_id: str, line: str):
    with _logs_lock:
        if job_id not in _job_logs:
            _job_logs[job_id] = []
        _job_logs[job_id].append(line)


def _get_logs(job_id: str) -> str:
    with _logs_lock:
        return "\n".join(_job_logs.get(job_id, []))


def _clear_logs(job_id: str):
    with _logs_lock:
        _job_logs.pop(job_id, None)


@app.get("/docs", include_in_schema=False)
async def scalar_docs():
    return get_scalar_api_reference(openapi_url="/openapi.json", title="FinScan")


@app.on_event("startup")
def startup():
    init_db()
    os.makedirs("data", exist_ok=True)


# captures stdout from crew into the job log buffer
class _LogCapture(io.TextIOBase):
    def __init__(self, job_id: str, original_stdout):
        self.job_id = job_id
        self.original = original_stdout

    def write(self, s):
        if s and s.strip():
            _append_log(self.job_id, s.rstrip())
        return self.original.write(s)

    def flush(self):
        self.original.flush()


# runs the full crew pipeline in a background thread
def _run_pipeline(job_id: str, query: str, file_path: str):
    db = SessionLocal()
    old_stdout = sys.stdout
    sys.stdout = _LogCapture(job_id, old_stdout)
    try:
        update_job(db, job_id, status="running")
        _append_log(job_id, f"[pipeline] starting analysis on {file_path}")
        t0 = time.time()

        crew = Crew(
            agents=[verifier, financial_analyst, investment_advisor, risk_assessor],
            tasks=[
                verify_document,
                analyze_financial_document,
                investment_analysis,
                risk_assessment,
            ],
            process=Process.sequential,
            verbose=True,
        )

        result = crew.kickoff({"query": query, "file_path": file_path})
        elapsed = round(time.time() - t0, 2)

        # store final logs alongside result
        final_logs = _get_logs(job_id)
        update_job(
            db,
            job_id,
            status="done",
            result=str(result),
            logs=final_logs,
            duration_sec=elapsed,
            finished_at=datetime.utcnow(),
        )

    except Exception as e:
        final_logs = _get_logs(job_id)
        update_job(
            db,
            job_id,
            status="failed",
            error=str(e),
            logs=final_logs,
            finished_at=datetime.utcnow(),
        )
    finally:
        sys.stdout = old_stdout
        # keep logs around for a bit so the UI can fetch final state,
        # they'll get cleaned up on next job or server restart
        if os.path.exists(file_path) and file_path.startswith("data/upload_"):
            try:
                os.remove(file_path)
            except OSError:
                pass
        db.close()


@app.get("/")
async def health():
    return {"status": "ok", "service": "finscan"}


@app.get("/ui", response_class=HTMLResponse)
async def ui():
    path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if not os.path.exists(path):
        raise HTTPException(404, "ui not found")
    with open(path, encoding="utf-8") as f:
        return f.read()


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    query: str = Form(
        default="Analyze this financial document for investment insights"
    ),
    db: Session = Depends(get_db),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only pdf files are supported")

    job_id = str(uuid.uuid4())
    file_path = f"data/upload_{job_id[:8]}.pdf"

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    query = (
        query or ""
    ).strip() or "Analyze this financial document for investment insights"
    create_job(db, job_id, file.filename, query)

    threading.Thread(
        target=_run_pipeline, args=(job_id, query, file_path), daemon=True
    ).start()

    return {
        "job_id": job_id,
        "status": "pending",
        "message": f"poll /status/{job_id} for results",
    }


@app.post("/analyze/sample")
async def analyze_sample(
    query: str = Form(
        default="Analyze this financial document for investment insights"
    ),
    db: Session = Depends(get_db),
):
    sample = "data/TSLA-Q2-2025-Update.pdf"
    if not os.path.exists(sample):
        raise HTTPException(
            404, "sample pdf not found — place TSLA-Q2-2025-Update.pdf in data/"
        )

    job_id = str(uuid.uuid4())
    query = (
        query or ""
    ).strip() or "Analyze this financial document for investment insights"
    create_job(db, job_id, "TSLA-Q2-2025-Update.pdf", query)

    threading.Thread(
        target=_run_pipeline, args=(job_id, query, sample), daemon=True
    ).start()

    return {
        "job_id": job_id,
        "status": "pending",
        "message": f"poll /status/{job_id} for results",
    }


@app.get("/status/{job_id}")
async def status(job_id: str, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")

    out = {
        "job_id": job.job_id,
        "status": job.status,
        "filename": job.filename,
        "query": job.query,
        "created_at": str(job.created_at),
    }

    # live logs while running, stored logs after completion
    if job.status in ("pending", "running"):
        out["logs"] = _get_logs(job_id)
    else:
        out["logs"] = job.logs or ""

    if job.status == "done":
        out["result"] = job.result
        out["duration_sec"] = job.duration_sec
        out["finished_at"] = str(job.finished_at)
    elif job.status == "failed":
        out["error"] = job.error

    return out


@app.get("/history")
async def history(limit: int = 20, db: Session = Depends(get_db)):
    return [
        {
            "job_id": j.job_id,
            "filename": j.filename,
            "status": j.status,
            "query": j.query,
            "duration_sec": j.duration_sec,
            "created_at": str(j.created_at),
        }
        for j in list_jobs(db, limit)
    ]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
