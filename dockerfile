# dockerfile - High Performance Micro-image for dockmove
FROM python:3.11-alpine

# Install Docker standard client and utilities
RUN apk add --no-cache docker-cli tar gzip

WORKDIR /app

# Pull configurations and setup dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Mount assets and configurations
COPY . .

EXPOSE 6767

# Run API server (Ensure socket mounting in deployment composition)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "6767"]
