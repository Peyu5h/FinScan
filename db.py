import os
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DB_PATH = os.getenv("DATABASE_URL", "sqlite:///data/finscan.db")

engine = create_engine(DB_PATH, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), unique=True, nullable=False, index=True)
    filename = Column(String(256), nullable=False)
    query = Column(Text, nullable=False)
    status = Column(String(32), default="pending")
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    logs = Column(Text, nullable=True)
    duration_sec = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


def init_db():
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_job(db: Session, job_id: str, filename: str, query: str):
    row = AnalysisResult(
        job_id=job_id, filename=filename, query=query, status="pending"
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_job(db: Session, job_id: str):
    return db.query(AnalysisResult).filter(AnalysisResult.job_id == job_id).first()


def update_job(db: Session, job_id: str, **kwargs):
    row = get_job(db, job_id)
    if not row:
        return
    for k, v in kwargs.items():
        setattr(row, k, v)
    db.commit()


def list_jobs(db: Session, limit: int = 50):
    return (
        db.query(AnalysisResult)
        .order_by(AnalysisResult.created_at.desc())
        .limit(limit)
        .all()
    )
