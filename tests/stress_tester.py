import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "booking_service"))

import proto.flight_pb2 as pb2
import proto.flight_pb2_grpc as pb2_grpc

BASE_URL = "http://localhost:8000"
GRPC_URL = "localhost:50051"
FLIGHTS_QUERY = {"origin": "SVO", "destination": "LED"}
MIN_EXPECTED_RETRY_DURATION = 0.25


def print_header(title):
    print(f"\n{'=' * 60}")
    print(f"TEST: {title}")
    print(f"{'=' * 60}")


def _extract_available_seats(payload):
    if "available_seats" in payload:
        return payload["available_seats"]
    if "availableSeats" in payload:
        return payload["availableSeats"]
    # Older API serialization can omit zero-valued scalar fields.
    if "total_seats" in payload or "totalSeats" in payload:
        return 0
    return None


def _get_flight_capacity_with_retry(flight_id, attempts=20, delay=1.0):
    url = f"{BASE_URL}/flights/{flight_id}"
    for attempt in range(1, attempts + 1):
        try:
            res = httpx.get(url, timeout=10)
        except Exception as e:
            print(f"Attempt {attempt}/{attempts}: failed to call Booking Service: {e}")
            time.sleep(delay)
            continue

        if res.status_code == 200:
            payload = res.json()
            seats = _extract_available_seats(payload)
            if seats is None:
                print(
                    f"Attempt {attempt}/{attempts}: response missing seats key. Payload: {payload}"
                )
                time.sleep(delay)
                continue
            return seats

        try:
            payload = res.json()
        except Exception:
            payload = {"raw": res.text}
        print(
            f"Attempt {attempt}/{attempts}: /flights/{flight_id} returned {res.status_code}: {payload}"
        )
        time.sleep(delay)

    return None


def test_grpc_security():
    print_header("gRPC auth check")
    print("Calling Flight Service directly without API key.")

    with grpc.insecure_channel(GRPC_URL) as channel:
        stub = pb2_grpc.FlightServiceStub(channel)
        try:
            req = pb2.GetFlightRequest(flight_id=1)
            stub.GetFlight(req, timeout=3)
            print("FAIL: unauthenticated request was accepted.")
            return False
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                print(f"PASS: unauthenticated request rejected ({e.details()}).")
                return True
            else:
                print(f"FAIL: unexpected gRPC status {e.code()}.")
                return False


def test_race_conditions():
    print_header("Race condition check (SELECT FOR UPDATE)")
    print("Fetching initial capacity for flight 2.")

    initial_seats = _get_flight_capacity_with_retry(flight_id=2)
    if initial_seats is None:
        print("FAIL: could not fetch flight 2 capacity after retries.")
        return False

    if initial_seats <= 0:
        print("SKIP: flight 2 has no available seats.")
        return False

    print(f"Initial capacity: {initial_seats} seats.")

    # We will send MORE requests than available seats
    num_requests = initial_seats + 50
    print(f"Sending {num_requests} concurrent booking requests with 100 workers.")

    success_count = 0
    fail_count = 0
    count_lock = threading.Lock()

    def make_booking():
        nonlocal success_count, fail_count
        payload = {
            "user_id": 999,
            "flight_id": 2,
            "passenger_name": "Stress Tester",
            "passenger_email": "stress@example.com",
            "seat_count": 1,
        }
        r = httpx.post(f"{BASE_URL}/bookings", json=payload)
        with count_lock:
            if r.status_code == 200:
                success_count += 1
            else:
                fail_count += 1

    with ThreadPoolExecutor(max_workers=100) as executor:
        for _ in range(num_requests):
            executor.submit(make_booking)

    print(f"Finished {num_requests} requests.")
    print(f"Successful bookings: {success_count} (expected {initial_seats})")
    print(f"Denied requests: {fail_count} (expected 50)")

    if success_count == initial_seats and fail_count == 50:
        print("PASS: no oversell detected.")
        return True
    else:
        print("FAIL: booking totals did not match expected limits.")
        return False


def test_redis_sentinel_failover():
    print_header("Redis Sentinel failover")
    print("Priming cache with /flights/2 request.")
    res = httpx.get(f"{BASE_URL}/flights/2")

    print("Stopping redis-master container.")
    subprocess.run(["docker-compose", "stop", "redis-master"], capture_output=True)

    print("Waiting 5 seconds for failover.")
    for i in range(5, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    print("Calling /flights/2 after failover.")
    res = httpx.get(f"{BASE_URL}/flights/2")

    print("Starting redis-master container.")
    subprocess.run(["docker-compose", "start", "redis-master"], capture_output=True)

    if res.status_code == 200:
        print("PASS: service remained available during Redis failover.")
        return True
    else:
        print(f"FAIL: /flights/2 returned {res.status_code} during failover.")
        return False


def test_circuit_breaker():
    print_header("Circuit breaker and retry")
    print("Stopping flight_service container.")
    subprocess.run(["docker-compose", "stop", "flight_service"], capture_output=True)
    overall_ok = True

    try:
        print("\nPhase 1: retry latency check")
        start_time = time.time()
        res = httpx.get(f"{BASE_URL}/flights", params=FLIGHTS_QUERY)
        end_time = time.time()
        duration = end_time - start_time

        # Homework retry policy is max 3 attempts (backoff ~0.1 + 0.2 = 0.3s).
        if duration >= MIN_EXPECTED_RETRY_DURATION and res.status_code in [500, 503]:
            print(
                f"PASS: retry observed (status={res.status_code}, duration={duration:.2f}s)."
            )
        else:
            print(
                f"FAIL: retry not observed (status={res.status_code}, duration={duration:.2f}s, expected>={MIN_EXPECTED_RETRY_DURATION:.2f}s)."
            )
            overall_ok = False

        print("\nPhase 2: open breaker")
        print("Sending 5 requests to trip threshold.")
        for _ in range(5):
            httpx.get(f"{BASE_URL}/flights", params=FLIGHTS_QUERY)

        cb_status = httpx.get(f"{BASE_URL}/circuit-breaker-status").json()
        print(
            f"Circuit breaker state: {cb_status['state']} (errors={cb_status['error_count']})"
        )

        if cb_status["state"] != "OPEN":
            print("FAIL: breaker did not open.")
            overall_ok = False

        print("\nPhase 3: fast-fail check")
        start_time = time.time()
        res = httpx.get(f"{BASE_URL}/flights", params=FLIGHTS_QUERY)
        end_time = time.time()
        fduration = end_time - start_time

        if fduration < 0.2:
            print(f"PASS: fast-fail observed (duration={fduration:.2f}s).")
        else:
            print(f"FAIL: fast-fail not observed (duration={fduration:.2f}s).")
            overall_ok = False
    finally:
        print("\nPhase 4: recovery check")
        print("Starting flight_service container and waiting 10 seconds.")
        subprocess.run(["docker-compose", "start", "flight_service"], capture_output=True)

        for _ in range(11):
            time.sleep(1)

    res = httpx.get(f"{BASE_URL}/flights", params=FLIGHTS_QUERY)
    cb_status_final = httpx.get(f"{BASE_URL}/circuit-breaker-status").json()

    if res.status_code == 200 and cb_status_final["state"] == "CLOSED":
        print(f"PASS: breaker recovered (state={cb_status_final['state']}).")
    else:
        print(
            f"FAIL: recovery check failed (status={res.status_code}, state={cb_status_final['state']})."
        )
        overall_ok = False

    return overall_ok


def run_all():
    print(f"\n{'=' * 80}")
    print("STRESS TEST RUN")
    print(f"{'=' * 80}")

    t1 = test_grpc_security()
    t2 = test_race_conditions()
    t3 = test_redis_sentinel_failover()
    t4 = test_circuit_breaker()

    print(f"\n{'=' * 80}")
    if t1 and t2 and t3 and t4:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    run_all()
