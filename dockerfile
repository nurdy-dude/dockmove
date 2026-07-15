FROM docker:cli AS docker-cli

FROM python:3.11-slim

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 6767

CMD ["python", "main.py"]
