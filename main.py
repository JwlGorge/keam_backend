import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select, func, desc, and_
from pydantic import BaseModel

from database import engine, Result, Top10, create_db_and_tables

app = FastAPI(title="KEAM Prep Global Exam API")

# Enable CORS for mobile and web apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic schemas for request/response
class SubmissionRequest(BaseModel):
    user_name: str
    paper_name: str
    score: int

class RankInfo(BaseModel):
    name: str
    score: int
    rank: int

class SubmissionResponse(BaseModel):
    score: int
    rank: int
    percentile: float
    top_10: List[RankInfo]

# Hardcoded Exam Schedule for Malayalam/Kerala Exams (UTC - Entire Day)
# Format: { filename: (start_time, end_time) }
EXAM_SCHEDULE = {
    "random_qp1.json": (
        datetime(2026, 4, 6, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 6, 23, 59, 59, tzinfo=timezone.utc)
    ),
    "random_qp2.json": (
        datetime(2026, 4, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 12, 23, 59, 59, tzinfo=timezone.utc)
    ),
    "random_qp3.json": (
        datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 13, 23, 59, 59, tzinfo=timezone.utc)
    ),
    "random_qp4.json": (
        datetime(2026, 4, 14, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 14, 23, 59, 59, tzinfo=timezone.utc)
    ),
}

# VERCEL SERVERLESS OPTIMIZATION: 
# Move database initialization to top-level so it runs during "cold starts"
try:
    create_db_and_tables()
except Exception as e:
    print(f"DB initialization already handled or failed: {e}")

@app.get("/")
def read_root():
    return {"message": "Global Exam API is online", "server_time": datetime.now(timezone.utc)}

@app.post("/submit", response_model=SubmissionResponse)
def submit_result(submission: SubmissionRequest):
    with Session(engine) as session:
        # 1. Server-side time validation
        now = datetime.now(timezone.utc)
        window = EXAM_SCHEDULE.get(submission.paper_name)
        
        if window:
            start_time, end_time = window
            if not (start_time <= now <= end_time):
                 # REJECT if outside the hardcoded window
                 raise HTTPException(status_code=403, detail="Submission closed or not yet open")

        # 2. Check for existing submission - BLOCK SECOND ATTEMPT
        existing = session.exec(
            select(Result).where(
                and_(Result.user_name == submission.user_name, Result.paper_name == submission.paper_name)
            )
        ).first()
        
        if existing:
            raise HTTPException(status_code=403, detail="already submitted second attempt not allowed")

        # Create new result
        result = Result(
            user_name=submission.user_name,
            paper_name=submission.paper_name,
            score=submission.score,
            submitted_at=now
        )
        session.add(result)
        session.commit()
        session.refresh(result)

        # 3. Handle Top 10 Management
        # Fetch current Top 10 for this paper
        top_10_entries = session.exec(
            select(Top10)
            .where(Top10.paper_name == submission.paper_name)
            .order_by(desc(Top10.score), Top10.submitted_at)
        ).all()

        if len(top_10_entries) < 10 or submission.score > top_10_entries[-1].score:
            # User qualifies for Top 10
            new_top = Top10(
                user_name=submission.user_name,
                paper_name=submission.paper_name,
                score=submission.score,
                submitted_at=now
            )
            session.add(new_top)
            session.commit()
            
            # Re-fetch, sort, and trim to 10
            updated_top = session.exec(
                select(Top10)
                .where(Top10.paper_name == submission.paper_name)
                .order_by(desc(Top10.score), Top10.submitted_at)
            ).all()
            
            if len(updated_top) > 10:
                # Delete the 11th and beyond
                for extra in updated_top[10:]:
                    session.delete(extra)
                session.commit()
                updated_top = updated_top[:10]
            
            top_10_entries = updated_top

        # 4. Calculate Rank and Percentile from the full Result table
        all_results = session.exec(
            select(Result)
            .where(Result.paper_name == submission.paper_name)
            .order_by(desc(Result.score), Result.submitted_at)
        ).all()

        total_participants = len(all_results)
        user_rank = 0
        below_count = 0
        
        for i, res in enumerate(all_results):
            rank = i + 1
            if res.user_name == submission.user_name:
                user_rank = rank
            if res.score < submission.score:
                below_count += 1

        percentile = (below_count / total_participants * 100) if total_participants > 0 else 100.0

        # Prepare Top 10 response
        response_top_10 = [
            RankInfo(name=t.user_name, score=t.score, rank=idx + 1)
            for idx, t in enumerate(top_10_entries)
        ]

        return SubmissionResponse(
            score=result.score,
            rank=user_rank,
            percentile=round(percentile, 2),
            top_10=response_top_10
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
