#!/bin/bash
# Generate protobuf classes for flight_service
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. proto/flight.proto

# We also need to fix the import in the generated flight_pb2_grpc.py 
# because it uses `import flight_pb2 as flight__pb2` which might fail if run from flight_service root.
# Actually let's use the standard fix:
sed -i.bak -e 's/import flight_pb2 as flight__pb2/from . import flight_pb2 as flight__pb2/g' proto/flight_pb2_grpc.py
rm proto/flight_pb2_grpc.py.bak
