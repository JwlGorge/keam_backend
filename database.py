import os
from datetime import datetime, timezone
from typing import Optional, List
from sqlmodel import Field, SQLModel, create_engine, Session, select, func
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(
    DATABASE_URL, 
    echo=False, 
    pool_pre_ping=True, 
    connect_args={"sslmode": "require"} # Required for Neon/Serverless DB
)

class Result(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_name: str
    paper_name: str
    score: int
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # SQLite autoincrement for compatibility
        {"sqlite_autoincrement": True},
    )

class Top10(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_name: str
    paper_name: str
    score: int
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # SQLite autoincrement for compatibility
        {"sqlite_autoincrement": True},
    )

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
