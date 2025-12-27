FROM python:3.11-slim

WORKDIR /app

# Install system deps (optional but good practice)
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY incident_agent.py .
COPY . .

EXPOSE 5000

CMD ["python", "incident_agent.py"]

