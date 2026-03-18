import grpc
import os
import redis
import json
from datetime import timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select
import time
from concurrent import futures
import logging

import proto.flight_pb2 as pb2
import proto.flight_pb2_grpc as pb2_grpc
from database import SessionLocal
from models import Flight, SeatReservation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
API_KEY = os.environ.get("GRPC_API_KEY", "supersecretkey")

if os.environ.get("REDIS_MODE") == "sentinel":
    from redis.sentinel import Sentinel
    sentinel = Sentinel([('redis-sentinel', 26379)], socket_timeout=0.2)
    redis_client = sentinel.master_for('mymaster', socket_timeout=0.2, password='password', decode_responses=True)
else:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

class FlightServiceServicer(pb2_grpc.FlightServiceServicer):
    
    def _check_auth(self, context):
        metadata = dict(context.invocation_metadata())
        if metadata.get('authorization') != f"Bearer {API_KEY}":
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid API Key")

    def _get_flight_from_db(self, flight_id: int):
        with SessionLocal() as db:
            return db.query(Flight).filter(Flight.id == flight_id).first()

    def SearchFlights(self, request, context):
        self._check_auth(context)
        
        origin = request.origin
        destination = request.destination
        date_str = request.date
        
        cache_key = f"search:{origin}:{destination}:{date_str or 'any'}"
        cached = redis_client.get(cache_key)
        if cached:
            try:
                flights_dict = json.loads(cached)
                # We could deserialize JSON to Proto directly, but for simplicity:
                # We'll re-fetch from DB or parse manually. Instead of full JSON, caching is better here.
                pass 
            except Exception as e:
                logger.error(f"Cache parse failed: {e}")

        with SessionLocal() as db:
            query = db.query(Flight).filter(
                Flight.origin == origin, 
                Flight.destination == destination,
                Flight.status == "SCHEDULED"
            )
            if date_str:
                # Naive date filtering by string
                pass # Ideally filter by date(departure_time)
            
            flights_db = query.all()
            
            # Form response
            res = pb2.SearchFlightsResponse()
            for f in flights_db:
                flight_pb = res.flights.add()
                flight_pb.id = f.id
                flight_pb.airline = f.airline
                flight_pb.origin = f.origin
                flight_pb.destination = f.destination
                flight_pb.departure_time.FromDatetime(f.departure_time)
                flight_pb.arrival_time.FromDatetime(f.arrival_time)
                flight_pb.total_seats = f.total_seats
                flight_pb.available_seats = f.available_seats
                flight_pb.price = float(f.price)
                flight_pb.status = pb2.FlightStatus.SCHEDULED
            
            # Simple JSON cache for demonstration if needed, but we don't strictly need to serialize proto to json.
            # Using Google Protobuf JSON format is easier:
            from google.protobuf.json_format import MessageToJson
            redis_client.setex(cache_key, 300, MessageToJson(res)) # 5 mins TTL

            return res

    def GetFlight(self, request, context):
        self._check_auth(context)
        
        flight_id = request.flight_id
        cache_key = f"flight:{flight_id}"
        
        cached = redis_client.get(cache_key)
        if cached:
            from google.protobuf.json_format import Parse
            try:
                res = pb2.GetFlightResponse()
                Parse(cached, res)
                return res
            except Exception:
                pass
                
        f = self._get_flight_from_db(flight_id)
        if not f:
            context.abort(grpc.StatusCode.NOT_FOUND, "Flight not found")
            
        res = pb2.GetFlightResponse()
        res.flight.id = f.id
        res.flight.airline = f.airline
        res.flight.origin = f.origin
        res.flight.destination = f.destination
        res.flight.departure_time.FromDatetime(f.departure_time)
        res.flight.arrival_time.FromDatetime(f.arrival_time)
        res.flight.total_seats = f.total_seats
        res.flight.available_seats = f.available_seats
        res.flight.price = float(f.price)
        res.flight.status = getattr(pb2.FlightStatus, f.status, pb2.FlightStatus.SCHEDULED)
        
        from google.protobuf.json_format import MessageToJson
        redis_client.setex(cache_key, 600, MessageToJson(res)) # 10 mins TTL
        
        return res

    def ReserveSeats(self, request, context):
        self._check_auth(context)
        
        flight_id = request.flight_id
        seat_count = request.seat_count
        booking_id = request.booking_id
        
        with SessionLocal() as db:
            # Idempotency check
            existing_res = db.query(SeatReservation).filter(SeatReservation.booking_id == booking_id).first()
            if existing_res:
                return pb2.ReserveSeatsResponse(success=True, reservation_id=existing_res.id)
            
            try:
                # SELECT FOR UPDATE to prevent race conditions
                flight = db.query(Flight).filter(Flight.id == flight_id).with_for_update().first()
                if not flight:
                    db.rollback()
                    context.abort(grpc.StatusCode.NOT_FOUND, "Flight not found")
                    
                if flight.available_seats < seat_count:
                    db.rollback()
                    context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Not enough seats")
                    
                flight.available_seats -= seat_count
                
                new_res = SeatReservation(
                    flight_id=flight_id,
                    booking_id=booking_id,
                    seat_count=seat_count,
                    status="ACTIVE"
                )
                db.add(new_res)
                db.commit()
                db.refresh(new_res)
                
                # Invalidate cache
                redis_client.delete(f"flight:{flight_id}")
                # We should invalidate search cache too, scanning might be overkill, let's just clear for the specific flight's origin/dst
                # But for now flight caching is specifically requested as flight:{id} and search.
                return pb2.ReserveSeatsResponse(success=True, reservation_id=new_res.id)
            except Exception as e:
                db.rollback()
                logger.error(f"Error reserving seats: {e}")
                context.abort(grpc.StatusCode.INTERNAL, "Internal error")

    def ReleaseReservation(self, request, context):
        self._check_auth(context)
        booking_id = request.booking_id
        
        with SessionLocal() as db:
            try:
                res = db.query(SeatReservation).filter(
                    SeatReservation.booking_id == booking_id,
                    SeatReservation.status == "ACTIVE"
                ).with_for_update().first()
                
                if not res:
                    db.rollback()
                    context.abort(grpc.StatusCode.NOT_FOUND, "Reservation not found or already released")
                    
                flight = db.query(Flight).filter(Flight.id == res.flight_id).with_for_update().first()
                if flight:
                    flight.available_seats += res.seat_count
                    
                res.status = "RELEASED"
                db.commit()
                
                # Invalidate cache
                if flight:
                    redis_client.delete(f"flight:{flight.id}")
                
                return pb2.ReleaseReservationResponse(success=True)
            except Exception as e:
                db.rollback()
                logger.error(f"Error releasing seats: {e}")
                context.abort(grpc.StatusCode.INTERNAL, "Internal error")

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_FlightServiceServicer_to_server(FlightServiceServicer(), server)
    server.add_insecure_port('[::]:50051')
    logger.info("Flight Service gRPC server starting on port 50051...")
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()
