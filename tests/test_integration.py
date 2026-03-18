import pytest
import httpx
import time

BASE_URL = "http://localhost:8000"

def wait_for_service():
    """Wait until Booking Service is up."""
    for _ in range(30):
        try:
            res = httpx.get(f"{BASE_URL}/docs")
            if res.status_code == 200:
                print("Booking service is up!")
                return
        except httpx.RequestError:
            pass
        time.sleep(1)
    raise Exception("Service did not start in time. Ensure docker-compose is running.")

@pytest.fixture(scope="session", autouse=True)
def setup():
    wait_for_service()

def test_search_flights():
    res = httpx.get(f"{BASE_URL}/flights?origin=SVO&destination=LED")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0]["origin"] == "SVO"
    assert data[0]["destination"] == "LED"

def test_get_flight():
    res = httpx.get(f"{BASE_URL}/flights/1")
    assert res.status_code == 200
    data = res.json()
    assert int(data["id"]) == 1
    assert data["origin"] == "SVO"

def test_reserve_seats():
    # Fetch initial seats to be robust
    initial_res = httpx.get(f"{BASE_URL}/flights/1")
    initial_seats = initial_res.json()["available_seats"]

    # Attempt to reserve 2 seats on flight 1
    booking_payload = {
        "user_id": 123,
        "flight_id": 1,
        "passenger_name": "Test User",
        "passenger_email": "test@example.com",
        "seat_count": 2
    }
    
    res = httpx.post(f"{BASE_URL}/bookings", json=booking_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "CONFIRMED"
    assert data["seat_count"] == 2
    
    # Assert flight inventory changed
    flight_res = httpx.get(f"{BASE_URL}/flights/1")
    assert flight_res.json()["available_seats"] == initial_seats - 2

def test_cancel_booking():
    # Make a fresh booking for cancellation
    initial_res = httpx.get(f"{BASE_URL}/flights/1")
    initial_seats = initial_res.json()["available_seats"]

    booking_payload = {
        "user_id": 124,
        "flight_id": 1,
        "passenger_name": "Cancel User",
        "passenger_email": "cancel@example.com",
        "seat_count": 1
    }
    res = httpx.post(f"{BASE_URL}/bookings", json=booking_payload)
    booking_id = res.json()["id"]

    # Now cancel
    cancel_res = httpx.post(f"{BASE_URL}/bookings/{booking_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "success"
    
    # Wait for cancel to reflect
    time.sleep(1)
    
    # Assert flight inventory is back exactly to what it was before this test
    flight_res = httpx.get(f"{BASE_URL}/flights/1")
    assert flight_res.json()["available_seats"] == initial_seats
