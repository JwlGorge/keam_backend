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
    device_id: str

class RankInfo(BaseModel):
    name: str
    score: int
    rank: int

class SubmissionResponse(BaseModel):
    score: int
    rank: int
    percentile: float
    top_10: List[RankInfo]

class GlobalRankResponse(BaseModel):
    average_score: float
    rank: int
    total_users: int
    top_10: List[RankInfo]

# Scheduled Paper Names (Informational)
AVAILABLE_PAPERS = ["random_qp1.json", "random_qp2.json", "random_qp3.json", "random_qp4.json"]

# VERCEL SERVERLESS OPTIMIZATION: 
# Move database initialization to top-level so it runs during "cold starts"
try:
    create_db_and_tables()
except Exception as e:
    print(f"DB initialization already handled or failed: {e}")

@app.get("/")
def read_root():
    return {"message": "Global Exam API is online", "server_time": datetime.now(timezone.utc)}

def get_rankings_logic(session: Session, user_name: str, paper_name: str, current_score: int, device_id: str):
    """
    Optimized ranking calculation:
    1. Fetch Top 10 directly from Top10 table (O(1)).
    2. Check if user (exact device) is in Top 10 for instant rank.
    3. If not, use indexed COUNT(*) on Results table (O(log N)).
    """
    # 1. Fetch current Top 10 for this paper
    top_10_entries = session.exec(
        select(Top10)
        .where(Top10.paper_name == paper_name)
        .order_by(desc(Top10.score), Top10.submitted_at)
    ).all()

    # 2. Determine User Rank
    user_rank = 0
    # Check Top 10 first - match by device_id to be precise
    if device_id:
        for idx, entry in enumerate(top_10_entries):
            if entry.device_id == device_id:
                user_rank = idx + 1
                break
    
    # If not in Top 10, use indexed COUNT
    if user_rank == 0:
        higher_scores_count = session.exec(
            select(func.count(Result.id))
            .where(and_(Result.paper_name == paper_name, Result.score > current_score))
        ).one()
        user_rank = higher_scores_count + 1

    # Ensure competition ranking for Top 10 as well
    # (If multiple people have same score, they should have same rank)
    sorted_top_10 = []
    if top_10_entries:
        for idx, t in enumerate(top_10_entries):
            # Calculate rank for each top 10 entry correctly
            rank_count = session.exec(
                select(func.count(Result.id))
                .where(and_(Result.paper_name == paper_name, Result.score > t.score))
            ).one()
            sorted_top_10.append(RankInfo(name=t.user_name, score=t.score, rank=rank_count + 1))
    
    # 3. Calculate Percentile
    total_participants = session.exec(
        select(func.count(Result.id)).where(Result.paper_name == paper_name)
    ).one()
    
    percentile = 0.0
    if total_participants > 0:
        below_count = session.exec(
            select(func.count(Result.id))
            .where(and_(Result.paper_name == paper_name, Result.score < current_score))
        ).one()
        percentile = (below_count / total_participants * 100)

    return SubmissionResponse(
        score=current_score,
        rank=user_rank if current_score > 0 or total_participants > 0 else 0,
        percentile=round(percentile, 2),
        top_10=sorted_top_10
    )

@app.get("/rankings", response_model=SubmissionResponse)
def get_rankings(paper_name: str, user_name: Optional[str] = None, device_id: Optional[str] = None):
    with Session(engine) as session:
        # If user info is provided, try to find their specific result
        if user_name and device_id:
            result = session.exec(
                select(Result).where(
                    and_(
                        Result.user_name == user_name, 
                        Result.device_id == device_id, 
                        Result.paper_name == paper_name
                    )
                )
            ).first()
            
            if result:
                return get_rankings_logic(session, user_name, paper_name, result.score, result.device_id)
        
        # Fallback: Just return the global leaderboard with 0 as user score
        return get_rankings_logic(session, "Anonymous", paper_name, 0, device_id or "")

@app.get("/global-rankings", response_model=GlobalRankResponse)
def get_global_rankings(device_id: Optional[str] = Query(None)):
    """
    Calculates rankings based on the AVERAGE score across ALL papers attempted by each user.
    """
    with Session(engine) as session:
        # 1. Get all results grouped by device_id to calculate averages
        # Note: We use the latest user_name associated with a device_id
        raw_stats = session.exec(
            select(
                Result.device_id, 
                func.avg(Result.score).label("avg_score"), 
                func.max(Result.user_name).label("user_name")
            ).group_by(Result.device_id)
        ).all()
        
        if not raw_stats:
            return GlobalRankResponse(average_score=0.0, rank=0, total_users=0, top_10=[])

        # 2. Sort users by average score descending
        sorted_stats = sorted(raw_stats, key=lambda x: x[1], reverse=True)
        total_users = len(sorted_stats)
        
        # 3. Find target user rank and stats
        user_avg = 0.0
        user_rank = 0
        if device_id:
            for idx, stat in enumerate(sorted_stats):
                if stat[0] == device_id:
                    user_avg = float(stat[1])
                    user_rank = idx + 1
                    break
        
        # 4. Prepare Top 10
        top_10 = []
        for idx, stat in enumerate(sorted_stats[:10]):
            top_10.append(RankInfo(
                name=stat[2], 
                score=int(round(float(stat[1]))), 
                rank=idx + 1
            ))
            
        return GlobalRankResponse(
            average_score=user_avg,
            rank=user_rank,
            total_users=total_users,
            top_10=top_10
        )

@app.post("/submit", response_model=SubmissionResponse)
def submit_result(submission: SubmissionRequest):
    with Session(engine) as session:
        # 1. Server-side time validation
        now = datetime.now(timezone.utc)
        # Date validation removed to allow attempts at any time

        # 2. Check for existing submission - BLOCK SECOND ATTEMPT BY DEVICE
        existing = session.exec(
            select(Result).where(
                and_(Result.device_id == submission.device_id, Result.paper_name == submission.paper_name)
            )
        ).first()
        
        if existing:
            raise HTTPException(status_code=403, detail="already submitted from this device")

        # Create new result
        result = Result(
            user_name=submission.user_name,
            paper_name=submission.paper_name,
            score=submission.score,
            device_id=submission.device_id,
            submitted_at=now
        )
        session.add(result)
        session.commit()
        session.refresh(result)

        # 3. Handle Top 10 Management
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
                device_id=submission.device_id,
                submitted_at=now
            )
            session.add(new_top)
            session.commit()
            
            # Re-fetch and trim
            updated_top = session.exec(
                select(Top10)
                .where(Top10.paper_name == submission.paper_name)
                .order_by(desc(Top10.score), Top10.submitted_at)
            ).all()
            
            if len(updated_top) > 10:
                for extra in updated_top[10:]:
                    session.delete(extra)
                session.commit()

        # 4. Return updated rankings
        return get_rankings_logic(session, submission.user_name, submission.paper_name, submission.score, submission.device_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
