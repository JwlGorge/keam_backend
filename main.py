import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select, func, desc, and_
from pydantic import BaseModel

from database import engine, User, Exam, Result, create_db_and_tables

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

@app.on_event("startup")
def on_startup():
    create_db_and_tables()



@app.post("/submit", response_model=SubmissionResponse)
def submit_result(submission: SubmissionRequest):
    with Session(engine) as session:
        # 1. Server-side time validation
        now = datetime.now(timezone.utc)
        exam = session.exec(select(Exam).where(Exam.paper_name == submission.paper_name)).first()
        
        if exam and not (exam.start_time <= now <= exam.end_time):
             # For production, uncomment this:
             # raise HTTPException(status_code=403, detail="Submission closed or not yet open")
             pass

        # 2. Check for existing submission - BLOCK SECOND ATTEMPT
        existing = session.exec(
            select(Result).where(
                and_(Result.user_id == submission.user_id, Result.paper_name == submission.paper_name)
            )
        ).first()
        
        if existing:
            raise HTTPException(status_code=403, detail="already submitted second attempt not allowed")

        # Create user if doesn't exist
        user = session.get(User, submission.user_id)
        if not user:
            user = User(id=submission.user_id, name=submission.user_name, email=f"user_{submission.user_id}@example.com")
            session.add(user)
            session.commit()
            session.refresh(user)

        # Create new result
        result = Result(
            user_id=submission.user_id,
            paper_name=submission.paper_name,
            score=submission.score,
            correct_count=submission.correct_count,
            wrong_count=submission.wrong_count,
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

        is_in_top_10 = False
        if len(top_10_entries) < 10 or submission.score > top_10_entries[-1].score:
            # User qualifies for Top 10
            # Remove existing entry if they were already there (shouldn't happen with blocks, but safe)
            # Add new entry
            new_top = Top10(
                user_id=submission.user_id,
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
            is_in_top_10 = True

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
            if res.user_id == submission.user_id:
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
