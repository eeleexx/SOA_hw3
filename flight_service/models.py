from sqlalchemy import Column, Integer, String, DateTime, Numeric, ForeignKey
from database import Base
import datetime

class Flight(Base):
    __tablename__ = "flights"

    id = Column(Integer, primary_key=True, index=True)
    flight_number = Column(String(50), nullable=False)
    airline = Column(String(100), nullable=False)
    origin = Column(String(3), nullable=False)
    destination = Column(String(3), nullable=False)
    departure_time = Column(DateTime, nullable=False)
    arrival_time = Column(DateTime, nullable=False)
    total_seats = Column(Integer, nullable=False)
    available_seats = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    status = Column(String(20), nullable=False, default="SCHEDULED")

class SeatReservation(Base):
    __tablename__ = "seat_reservations"

    id = Column(Integer, primary_key=True, index=True)
    flight_id = Column(Integer, ForeignKey("flights.id", ondelete="CASCADE"), nullable=False)
    booking_id = Column(Integer, unique=True, nullable=False)
    seat_count = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="ACTIVE")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
