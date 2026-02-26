import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from crewai import Crew, Process
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from agents import financial_analyst, investment_advisor, risk_assessor, verifier
from db import SessionLocal, create_job, get_job, init_db, list_jobs, update_job
from task import (
    analyze_financial_document,
    investment_analysis,
    risk_assessment,
    verify_document,
)

load_dotenv()

BASE_DIR = Path(__file__).parent

# in-memory log buffer for streaming
_job_logs: dict[str, list[str]] = {}
_logs_lock = threading.Lock()


class LogCapture:
    """tees stdout into both the terminal and the job log buffer"""

    def __init__(self, job_id: str, original):
        self.job_id = job_id
        self.original = original

    def write(self, s):
        if s and s.strip():
            with _logs_lock:
                _job_logs.setdefault(self.job_id, []).append(s.rstrip())
        return self.original.write(s)

    def flush(self):
        self.original.flush()

    def get_logs(self) -> str:
        with _logs_lock:
            return "\n".join(_job_logs.get(self.job_id, []))


# runs the full 4-agent crew pipeline in a background thread
def _run_pipeline(job_id: str, query: str, file_path: str):
    db = SessionLocal()
    capture = LogCapture(job_id, sys.stdout)
    sys.stdout = capture

    try:
        update_job(db, job_id, status="running")
        print(f"[pipeline] starting analysis on {file_path}")
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

        update_job(
            db,
            job_id,
            status="done",
            result=str(result),
            logs=capture.get_logs(),
            duration_sec=round(time.time() - t0, 2),
            finished_at=datetime.utcnow(),
        )

    except Exception as e:
        update_job(
            db,
            job_id,
            status="failed",
            error=str(e),
            logs=capture.get_logs(),
            finished_at=datetime.utcnow(),
        )

    finally:
        sys.stdout = capture.original
        if os.path.exists(file_path) and file_path.startswith("data/upload_"):
            try:
                os.remove(file_path)
            except OSError:
                pass
        db.close()


app = FastAPI(
    title="FinScan",
    description="Financial document analyzer powered by CrewAI",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# init db and data dir on startup
init_db()
os.makedirs("data", exist_ok=True)


@app.get("/")
async def health():
    return {"status": "ok", "service": "finscan"}


@app.get("/ui", response_class=HTMLResponse)
async def ui():
    path = BASE_DIR / "static" / "index.html"
    if not path.exists():
        raise HTTPException(404, f"ui not found at {path}")
    return path.read_text(encoding="utf-8")


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    query: str = Form(
        default="Analyze this financial document for investment insights"
    ),
):
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only pdf files are supported")

    job_id = str(uuid.uuid4())
    file_path = f"data/upload_{job_id[:8]}.pdf"

    with open(file_path, "wb") as f:
        f.write(await file.read())

    query = query.strip() or "Analyze this financial document for investment insights"

    db = SessionLocal()
    try:
        create_job(db, job_id, filename, query)
    finally:
        db.close()

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
):
    sample = "data/TSLA-Q2-2025-Update.pdf"
    if not os.path.exists(sample):
        raise HTTPException(
            404, "sample pdf not found — place TSLA-Q2-2025-Update.pdf in data/"
        )

    job_id = str(uuid.uuid4())
    query = query.strip() or "Analyze this financial document for investment insights"

    db = SessionLocal()
    try:
        create_job(db, job_id, "TSLA-Q2-2025-Update.pdf", query)
    finally:
        db.close()

    threading.Thread(
        target=_run_pipeline, args=(job_id, query, sample), daemon=True
    ).start()

    return {
        "job_id": job_id,
        "status": "pending",
        "message": f"poll /status/{job_id} for results",
    }


@app.get("/status/{job_id}")
async def status(job_id: str):
    db = SessionLocal()
    try:
        job = get_job(db, job_id)
        if not job:
            raise HTTPException(404, "job not found")

        is_live = str(job.status) in ("pending", "running")

        out = {
            "job_id": job.job_id,
            "status": job.status,
            "filename": job.filename,
            "query": job.query,
            "created_at": str(job.created_at),
            "logs": _job_logs.get(job_id, []) if is_live else (job.logs or ""),
        }

        if str(job.status) == "done":
            out["result"] = job.result
            out["duration_sec"] = job.duration_sec
            out["finished_at"] = str(job.finished_at)
        elif str(job.status) == "failed":
            out["error"] = job.error

        return out
    finally:
        db.close()


@app.get("/history")
async def history(limit: int = 20):
    db = SessionLocal()
    try:
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
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
