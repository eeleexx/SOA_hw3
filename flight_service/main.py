import grpc
import os
import redis
from concurrent import futures
import logging
from datetime import datetime
from sqlalchemy import func

import proto.flight_pb2 as pb2
import proto.flight_pb2_grpc as pb2_grpc
from database import SessionLocal
from models import Flight, SeatReservation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
API_KEY = os.environ.get("GRPC_API_KEY", "supersecretkey")
REDIS_MODE = os.environ.get("REDIS_MODE", "").lower()
REDIS_SENTINEL_HOST = os.environ.get("REDIS_SENTINEL_HOST", "redis-sentinel")
REDIS_SENTINEL_PORT = int(os.environ.get("REDIS_SENTINEL_PORT", 26379))
REDIS_MASTER_NAME = os.environ.get("REDIS_MASTER_NAME", "mymaster")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "password")
REDIS_SENTINEL_PASSWORD = os.environ.get("REDIS_SENTINEL_PASSWORD", REDIS_PASSWORD)

sentinel = None
redis_client = None

def _create_redis_client():
    global sentinel
    if REDIS_MODE == "sentinel":
        from redis.sentinel import Sentinel
        sentinel = Sentinel(
            [(REDIS_SENTINEL_HOST, REDIS_SENTINEL_PORT)],
            sentinel_kwargs={"password": REDIS_SENTINEL_PASSWORD},
            socket_timeout=0.2,
        )
        return sentinel.master_for(
            REDIS_MASTER_NAME,
            socket_timeout=0.2,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)

def _refresh_redis_client():
    global redis_client
    try:
        redis_client = _create_redis_client()
    except Exception as e:
        redis_client = None
        logger.error(f"Failed to refresh Redis client: {e}")

def _redis_get(key: str):
    if redis_client is None:
        _refresh_redis_client()
    if redis_client is None:
        return None
    try:
        return redis_client.get(key)
    except Exception as e:
        logger.warning(f"Redis GET failed for key={key}: {e}. Trying to reconnect.")
        _refresh_redis_client()
        if redis_client is None:
            return None
        try:
            return redis_client.get(key)
        except Exception as reconnect_error:
            logger.error(f"Redis GET failed after reconnect for key={key}: {reconnect_error}")
            return None

def _redis_setex(key: str, ttl: int, value: str):
    if redis_client is None:
        _refresh_redis_client()
    if redis_client is None:
        return
    try:
        redis_client.setex(key, ttl, value)
    except Exception as e:
        logger.warning(f"Redis SETEX failed for key={key}: {e}. Trying to reconnect.")
        _refresh_redis_client()
        if redis_client is None:
            return
        try:
            redis_client.setex(key, ttl, value)
        except Exception as reconnect_error:
            logger.error(f"Redis SETEX failed after reconnect for key={key}: {reconnect_error}")

def _redis_delete(key: str):
    if redis_client is None:
        _refresh_redis_client()
    if redis_client is None:
        return
    try:
        redis_client.delete(key)
    except Exception as e:
        logger.warning(f"Redis DELETE failed for key={key}: {e}")

def _redis_delete_search_cache(origin: str, destination: str):
    if redis_client is None:
        _refresh_redis_client()
    if redis_client is None:
        return
    pattern = f"search:{origin}:{destination}:*"
    try:
        for key in redis_client.scan_iter(pattern):
            redis_client.delete(key)
    except Exception as e:
        logger.warning(f"Redis search cache invalidation failed for pattern={pattern}: {e}")

_refresh_redis_client()

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
        cached = _redis_get(cache_key)
        if cached:
            logger.info(f"Cache hit for key: {cache_key}")
            try:
                from google.protobuf.json_format import Parse
                cached_response = pb2.SearchFlightsResponse()
                Parse(cached, cached_response)
                return cached_response
            except Exception as e:
                logger.error(f"Cache parse failed: {e}")
        else:
            logger.info(f"Cache miss for key: {cache_key}")

        with SessionLocal() as db:
            query = db.query(Flight).filter(
                Flight.origin == origin, 
                Flight.destination == destination,
                Flight.status == "SCHEDULED"
            )
            if date_str:
                try:
                    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid date format, expected YYYY-MM-DD")
                query = query.filter(func.date(Flight.departure_time) == target_date)
            
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
            _redis_setex(cache_key, 300, MessageToJson(res)) # 5 mins TTL

            return res

    def GetFlight(self, request, context):
        self._check_auth(context)
        
        flight_id = request.flight_id
        cache_key = f"flight:{flight_id}"
        
        cached = _redis_get(cache_key)
        if cached:
            logger.info(f"Cache hit for key: {cache_key}")
            from google.protobuf.json_format import Parse
            try:
                res = pb2.GetFlightResponse()
                Parse(cached, res)
                return res
            except Exception:
                pass
        else:
            logger.info(f"Cache miss for key: {cache_key}")
                
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
        _redis_setex(cache_key, 600, MessageToJson(res)) # 10 mins TTL
        
        return res

    def ReserveSeats(self, request, context):
        self._check_auth(context)
        
        flight_id = request.flight_id
        seat_count = request.seat_count
        booking_id = request.booking_id
        if seat_count <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "seat_count must be > 0")
        if booking_id <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "booking_id must be > 0")
        
        with SessionLocal() as db:
            # Idempotency check
            existing_res = db.query(SeatReservation).filter(SeatReservation.booking_id == booking_id).first()
            if existing_res:
                return pb2.ReserveSeatsResponse(success=True, reservation_id=existing_res.id)
            
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
            
            try:
                db.commit()
                db.refresh(new_res)
            except Exception as e:
                db.rollback()
                logger.error(f"Error reserving seats DB commit: {e}")
                context.abort(grpc.StatusCode.INTERNAL, "Internal error")
                
            # Invalidate cache
            _redis_delete(f"flight:{flight_id}")
            _redis_delete_search_cache(flight.origin, flight.destination)
            
            return pb2.ReserveSeatsResponse(success=True, reservation_id=new_res.id)

    def ReleaseReservation(self, request, context):
        self._check_auth(context)
        booking_id = request.booking_id
        
        with SessionLocal() as db:
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
            
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Error releasing seats DB commit: {e}")
                context.abort(grpc.StatusCode.INTERNAL, "Internal error")
            
            # Invalidate cache
            if flight:
                _redis_delete(f"flight:{flight.id}")
                _redis_delete_search_cache(flight.origin, flight.destination)
            
            return pb2.ReleaseReservationResponse(success=True)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_FlightServiceServicer_to_server(FlightServiceServicer(), server)
    server.add_insecure_port('[::]:50051')
    logger.info("Flight Service gRPC server starting on port 50051...")
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()
