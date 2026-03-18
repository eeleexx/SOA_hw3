from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
import grpc

from database import get_db, Base, engine
from models import Booking
from client import flight_client, circuit_breaker

app = FastAPI(title="Booking Service")

@app.get("/circuit-breaker-status")
def get_cb_status():
    """
    DEBUG ENDPOINT: Observe the state of the Circuit Breaker.
    """
    return {
        "state": circuit_breaker.state,
        "error_count": circuit_breaker.error_count,
        "threshold": 5,
        "timeout_seconds": 10
    }

class BookingCreate(BaseModel):
    user_id: int
    flight_id: int
    passenger_name: str
    passenger_email: str
    seat_count: int

@app.get("/flights")
def get_flights(origin: str, destination: str, date: Optional[str] = ""):
    try:
        flights = flight_client.search_flights(origin, destination, date)
        return flights
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.UNAVAILABLE:
            raise HTTPException(status_code=503, detail="Flight Service Unavailable")
        raise HTTPException(status_code=500, detail=str(e.details()))
    except HTTPException:
        raise

@app.get("/flights/{flight_id}")
def get_flight_by_id(flight_id: int):
    try:
        flight = flight_client.get_flight(flight_id)
        return flight
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Flight not found")
        raise HTTPException(status_code=500, detail=str(e.details()))
    except HTTPException:
        raise

@app.post("/bookings")
def create_booking(booking: BookingCreate, db: Session = Depends(get_db)):
    try:
        # 1. Get Flight logic to check existence and retrieve price
        flight = flight_client.get_flight(booking.flight_id)
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Flight not found")
        raise HTTPException(status_code=500, detail=str(e.details()))
    except HTTPException:
        raise
        
    price = float(flight['price'])
    total_price = price * booking.seat_count
    
    # Pre-create booking object in memory to fetch an id, or create it in db and commit.
    # The requirement: "При ошибке - не создавать бронирование".
    # We will reserve space for id by saving to DB first inside a transaction without committing?
    # No, postgres IDs are generated on insert. We can get next sequence value or insert first then rollback if failure.
    new_booking = Booking(
        user_id=booking.user_id,
        flight_id=booking.flight_id,
        passenger_name=booking.passenger_name,
        passenger_email=booking.passenger_email,
        seat_count=booking.seat_count,
        total_price=total_price,
        status="CONFIRMED"
    )
    db.add(new_booking)
    db.flush() # gets ID but not committed yet
    
    booking_id = new_booking.id
    
    try:
        # 2. Reserve seats in Flight Service
        success = flight_client.reserve_seats(booking.flight_id, booking.seat_count, booking_id)
        if success:
            db.commit()
            db.refresh(new_booking)
            return new_booking
        else:
            db.rollback()
            raise HTTPException(status_code=400, detail="Failed to reserve seats")
    except grpc.RpcError as e:
        db.rollback()
        if e.code() == grpc.StatusCode.RESOURCE_EXHAUSTED:
            raise HTTPException(status_code=400, detail="Not enough seats available")
        raise HTTPException(status_code=500, detail=str(e.details()))
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/bookings/{booking_id}")
def get_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking

@app.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(Booking).filter(Booking.id == booking_id).with_for_update().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
        
    if booking.status == "CANCELLED":
        raise HTTPException(status_code=400, detail="Booking already cancelled")
        
    try:
        # Release reservation in Flight Service
        success = flight_client.release_reservation(booking_id)
        if success:
            booking.status = "CANCELLED"
            db.commit()
            return {"status": "success", "message": "Booking cancelled"}
    except grpc.RpcError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e.details()))
    except HTTPException:
        db.rollback()
        raise

@app.get("/bookings")
def list_bookings(user_id: int, db: Session = Depends(get_db)):
    bookings = db.query(Booking).filter(Booking.user_id == user_id).all()
    return bookings
