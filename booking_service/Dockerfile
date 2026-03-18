FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Generate proto code
RUN chmod +x generate_proto.sh && ./generate_proto.sh

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
