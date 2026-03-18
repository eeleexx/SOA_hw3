# ER Diagram (3NF)

```mermaid
erDiagram
    FLIGHTS {
        INT id PK
        VARCHAR flight_number
        VARCHAR airline
        CHAR origin
        CHAR destination
        TIMESTAMP departure_time
        TIMESTAMP arrival_time
        INT total_seats
        INT available_seats
        DECIMAL price
        VARCHAR status
    }

    SEAT_RESERVATIONS {
        INT id PK
        INT flight_id FK
        INT booking_id UNIQUE
        INT seat_count
        VARCHAR status
        TIMESTAMP created_at
    }

    BOOKINGS {
        INT id PK
        INT user_id
        INT flight_id
        VARCHAR passenger_name
        VARCHAR passenger_email
        INT seat_count
        DECIMAL total_price
        VARCHAR status
        TIMESTAMP created_at
    }

    FLIGHTS ||--o{ SEAT_RESERVATIONS : "has"
```

## 3NF Notes

- `FLIGHTS` stores only flight attributes; non-key columns depend on `id`.
- `SEAT_RESERVATIONS` stores reservation facts; `booking_id` is unique to enforce one reservation per booking.
- `BOOKINGS` stores booking facts independently in Booking Service DB.
- No repeating groups and no transitive dependency chains in the modeled entities.

## Integrity Constraints

- `flights.total_seats > 0`
- `flights.available_seats >= 0`
- `flights.available_seats <= flights.total_seats`
- `flights.price > 0`
- `UNIQUE (flight_number, DATE(departure_time))`
- `seat_reservations.seat_count > 0`
- `seat_reservations.status IN (ACTIVE, RELEASED, EXPIRED)`
- `bookings.seat_count > 0`
- `bookings.total_price > 0`
- `bookings.status IN (CONFIRMED, CANCELLED)`
