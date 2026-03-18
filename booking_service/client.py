import grpc
import os
import time
import threading
from collections import deque
from fastapi import HTTPException
from google.protobuf.json_format import MessageToDict

import proto.flight_pb2 as pb2
import proto.flight_pb2_grpc as pb2_grpc
import logging

logger = logging.getLogger(__name__)

FLIGHT_SERVICE_URL = os.environ.get("FLIGHT_SERVICE_URL", "localhost:50051")
API_KEY = os.environ.get("GRPC_API_KEY", "supersecretkey")
RPC_TIMEOUT_SECONDS = float(os.environ.get("GRPC_TIMEOUT_SECONDS", 5))
MAX_RETRIES = min(int(os.environ.get("GRPC_MAX_RETRIES", 3)), 3)
RETRYABLE_GRPC_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
}

# Circuit breaker states
CB_CLOSED = "CLOSED"
CB_OPEN = "OPEN"
CB_HALF_OPEN = "HALF_OPEN"
CB_ERROR_THRESHOLD = int(os.environ.get("CB_ERROR_THRESHOLD", 5))
CB_TIMEOUT = int(os.environ.get("CB_TIMEOUT", 10))  # seconds
CB_WINDOW_SECONDS = int(os.environ.get("CB_WINDOW_SECONDS", 30))

class CircuitBreaker:
    def __init__(self):
        self._lock = threading.Lock()
        self.state = CB_CLOSED
        self.error_timestamps = deque()
        self.opened_at = 0.0
        self.half_open_probe_in_flight = False

    def _transition(self, new_state: str):
        old_state = self.state
        if old_state != new_state:
            logger.info(f"Circuit Breaker {old_state} -> {new_state}")
            self.state = new_state

    def _prune_window(self, now: float):
        cutoff = now - CB_WINDOW_SECONDS
        while self.error_timestamps and self.error_timestamps[0] < cutoff:
            self.error_timestamps.popleft()

    def before_call(self):
        now = time.time()
        with self._lock:
            if self.state == CB_OPEN:
                if now - self.opened_at >= CB_TIMEOUT:
                    self._transition(CB_HALF_OPEN)
                    self.half_open_probe_in_flight = False
                else:
                    raise HTTPException(status_code=503, detail="Service Unavailable (Circuit Breaker OPEN)")

            if self.state == CB_HALF_OPEN:
                if self.half_open_probe_in_flight:
                    raise HTTPException(status_code=503, detail="Service Unavailable (Circuit Breaker HALF_OPEN)")
                self.half_open_probe_in_flight = True

    def record_success(self):
        with self._lock:
            if self.state == CB_HALF_OPEN:
                self.half_open_probe_in_flight = False
                self.error_timestamps.clear()
                self._transition(CB_CLOSED)

    def record_failure(self, code):
        now = time.time()
        with self._lock:
            if self.state == CB_HALF_OPEN:
                self.half_open_probe_in_flight = False
                self.opened_at = now
                self._transition(CB_OPEN)
                return

            if code not in RETRYABLE_GRPC_CODES:
                return

            self._prune_window(now)
            self.error_timestamps.append(now)
            if self.state == CB_CLOSED and len(self.error_timestamps) >= CB_ERROR_THRESHOLD:
                self.opened_at = now
                self._transition(CB_OPEN)

    def snapshot(self):
        now = time.time()
        with self._lock:
            self._prune_window(now)
            return {
                "state": self.state,
                "error_count": len(self.error_timestamps),
                "threshold": CB_ERROR_THRESHOLD,
                "timeout_seconds": CB_TIMEOUT,
                "window_seconds": CB_WINDOW_SECONDS,
            }

circuit_breaker = CircuitBreaker()

def get_channel():
    return grpc.insecure_channel(FLIGHT_SERVICE_URL)

def rpc_call_with_retry(stub_method, request):
    metadata = (('authorization', f'Bearer {API_KEY}'),)

    def _make_call():
        for attempt in range(MAX_RETRIES):
            try:
                return stub_method(request, metadata=metadata, timeout=RPC_TIMEOUT_SECONDS)
            except grpc.RpcError as e:
                # Only retry for specific errors
                if e.code() not in RETRYABLE_GRPC_CODES:
                    raise e

                if attempt == MAX_RETRIES - 1:
                    raise e

                # Exponential backoff: 0.1, 0.2, 0.4...
                delay = 0.1 * (2 ** attempt)
                time.sleep(delay)

    circuit_breaker.before_call()
    try:
        res = _make_call()
        circuit_breaker.record_success()
        return res
    except grpc.RpcError as e:
        circuit_breaker.record_failure(e.code())
        raise
    except Exception:
        circuit_breaker.record_failure(None)
        raise

class FlightClient:
    @staticmethod
    def _proto_to_dict(message):
        return MessageToDict(
            message,
            preserving_proto_field_name=True,
            always_print_fields_with_no_presence=True,
        )

    def search_flights(self, origin: str, destination: str, date: str = ""):
        with get_channel() as channel:
            stub = pb2_grpc.FlightServiceStub(channel)
            req = pb2.SearchFlightsRequest(origin=origin, destination=destination, date=date)
            res = rpc_call_with_retry(stub.SearchFlights, req)
            return [self._proto_to_dict(f_msg) for f_msg in res.flights]

    def get_flight(self, flight_id: int):
        with get_channel() as channel:
            stub = pb2_grpc.FlightServiceStub(channel)
            req = pb2.GetFlightRequest(flight_id=flight_id)
            res = rpc_call_with_retry(stub.GetFlight, req)
            return self._proto_to_dict(res.flight)

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
