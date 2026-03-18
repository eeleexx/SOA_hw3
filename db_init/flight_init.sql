CREATE TABLE flights (
    id SERIAL PRIMARY KEY,
    flight_number VARCHAR(50) NOT NULL,
    airline VARCHAR(100) NOT NULL,
    origin VARCHAR(3) NOT NULL,
    destination VARCHAR(3) NOT NULL,
    departure_time TIMESTAMP NOT NULL,
    arrival_time TIMESTAMP NOT NULL,
    total_seats INT NOT NULL CHECK (total_seats > 0),
    available_seats INT NOT NULL CHECK (available_seats >= 0),
    price DECIMAL(10, 2) NOT NULL CHECK (price > 0),
    status VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED' CHECK (status IN ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED')),
    
    CONSTRAINT check_available_seats CHECK (available_seats <= total_seats),
    UNIQUE (flight_number, departure_time)
);

CREATE TABLE seat_reservations (
    id SERIAL PRIMARY KEY,
    flight_id INT NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    booking_id INT NOT NULL UNIQUE,
    seat_count INT NOT NULL CHECK (seat_count > 0),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED')),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Insert dummy data for manual testing
INSERT INTO flights (flight_number, airline, origin, destination, departure_time, arrival_time, total_seats, available_seats, price, status)
VALUES 
('SU1234', 'Aeroflot', 'SVO', 'LED', '2026-04-01 10:00:00', '2026-04-01 11:30:00', 150, 150, 5000.00, 'SCHEDULED'),
('S75678', 'S7 Airlines', 'VKO', 'LED', '2026-04-01 12:00:00', '2026-04-01 13:30:00', 100, 100, 4500.00, 'SCHEDULED');
