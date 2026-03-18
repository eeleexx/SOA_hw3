import grpc
import os
import time
from fastapi import HTTPException
from pydantic import BaseModel
from google.protobuf.json_format import MessageToDict

import proto.flight_pb2 as pb2
import proto.flight_pb2_grpc as pb2_grpc
import logging

logger = logging.getLogger(__name__)

FLIGHT_SERVICE_URL = os.environ.get("FLIGHT_SERVICE_URL", "localhost:50051")
API_KEY = os.environ.get("GRPC_API_KEY", "supersecretkey")

# Circuit breaker states
CB_CLOSED = "CLOSED"
CB_OPEN = "OPEN"
CB_HALF_OPEN = "HALF_OPEN"
CB_ERROR_THRESHOLD = 5
CB_TIMEOUT = 10  # seconds

class CircuitBreaker:
    def __init__(self):
        self.state = CB_CLOSED
        self.error_count = 0
        self.last_error_time = 0

    def call(self, f, *args, **kwargs):
        if self.state == CB_OPEN:
            if time.time() - self.last_error_time > CB_TIMEOUT:
                logger.info(f"Circuit Breaker OPEN -> HALF_OPEN")
                self.state = CB_HALF_OPEN
            else:
                raise HTTPException(status_code=503, detail="Service Unavailable (Circuit Breaker OPEN)")

        try:
            res = f(*args, **kwargs)
            if self.state == CB_HALF_OPEN:
                logger.info(f"Circuit Breaker HALF_OPEN -> CLOSED")
                self.state = CB_CLOSED
                self.error_count = 0
            return res
        except grpc.RpcError as e:
            if e.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED]:
                self.error_count += 1
                self.last_error_time = time.time()
                if self.state in [CB_CLOSED, CB_HALF_OPEN] and self.error_count >= CB_ERROR_THRESHOLD:
                    logger.info(f"Circuit Breaker transitioning to OPEN")
                    self.state = CB_OPEN
            raise e

circuit_breaker = CircuitBreaker()

def get_channel():
    return grpc.insecure_channel(FLIGHT_SERVICE_URL)

def rpc_call_with_retry(stub_method, request):
    max_retries = 3
    backoff = [0.1, 0.2, 0.4] # Exponential backoff
    
    metadata = (('authorization', f'Bearer {API_KEY}'),)

    def _make_call():
        for attempt in range(max_retries):
            try:
                return stub_method(request, metadata=metadata, timeout=5)
            except grpc.RpcError as e:
                # Only retry for specific errors
                if e.code() not in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED]:
                    raise e
                    
                if attempt < max_retries - 1:
                    time.sleep(backoff[attempt])
                else:
                    raise e

    # Wrap in circuit breaker
    return circuit_breaker.call(_make_call)

class FlightClient:
    def search_flights(self, origin: str, destination: str, date: str = ""):
        with get_channel() as channel:
            stub = pb2_grpc.FlightServiceStub(channel)
            req = pb2.SearchFlightsRequest(origin=origin, destination=destination, date=date)
            res = rpc_call_with_retry(stub.SearchFlights, req)
            return [MessageToDict(f_msg, preserving_proto_field_name=True) for f_msg in res.flights]

    def get_flight(self, flight_id: int):
        with get_channel() as channel:
            stub = pb2_grpc.FlightServiceStub(channel)
            req = pb2.GetFlightRequest(flight_id=flight_id)
            res = rpc_call_with_retry(stub.GetFlight, req)
            return MessageToDict(res.flight, preserving_proto_field_name=True)

    def reserve_seats(self, flight_id: int, seat_count: int, booking_id: int):
        with get_channel() as channel:
            stub = pb2_grpc.FlightServiceStub(channel)
            req = pb2.ReserveSeatsRequest(flight_id=flight_id, seat_count=seat_count, booking_id=booking_id)
            res = rpc_call_with_retry(stub.ReserveSeats, req)
            return res.success

    def release_reservation(self, booking_id: int):
        with get_channel() as channel:
            stub = pb2_grpc.FlightServiceStub(channel)
            req = pb2.ReleaseReservationRequest(booking_id=booking_id)
            res = rpc_call_with_retry(stub.ReleaseReservation, req)
            return res.success

flight_client = FlightClient()
