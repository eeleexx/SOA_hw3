from sqlalchemy import Column, Integer, String, DateTime, Numeric
from database import Base
import datetime

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    flight_id = Column(Integer, nullable=False)
    passenger_name = Column(String(100), nullable=False)
    passenger_email = Column(String(100), nullable=False)
    seat_count = Column(Integer, nullable=False)
    total_price = Column(Numeric(10, 2), nullable=False)
    status = Column(String(20), nullable=False, default="CONFIRMED")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
