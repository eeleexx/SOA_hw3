#!/usr/bin/env bash
set -euo pipefail

# Generate protobuf classes for flight_service
PYTHON_BIN="${PYTHON_BIN:-python3}"
"${PYTHON_BIN}" -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. proto/flight.proto

# Fix import to package-relative form.
sed -i.bak -e 's/import flight_pb2 as flight__pb2/from . import flight_pb2 as flight__pb2/g' proto/flight_pb2_grpc.py
rm proto/flight_pb2_grpc.py.bak
