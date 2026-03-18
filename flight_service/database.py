import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://flight_user:flight_password@localhost:5432/flight_db")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# To use within a session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
