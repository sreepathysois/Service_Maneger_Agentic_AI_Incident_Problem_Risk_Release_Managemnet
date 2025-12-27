from fastapi import FastAPI, HTTPException
import random
import time
import os
import logging
import json
import sys

from prometheus_client import Counter, Histogram, generate_latest
from prometheus_client import CONTENT_TYPE_LATEST
from starlette.responses import Response

app = FastAPI(title="Ecommerce Service")

# --------------------
# Config
# --------------------
FAIL_PAYMENTS = os.getenv("FAIL_PAYMENTS", "false").lower() == "true"

# --------------------
# Logging (Loki-friendly JSON logs)
# --------------------
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(message)s"
)

def log_event(event_type, service, message, **kwargs):
    log = {
        "event": event_type,
        "service": service,
        "message": message,
        "timestamp": int(time.time()),
        **kwargs
    }
    logging.info(json.dumps(log))

# --------------------
# Metrics
# --------------------
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)

REQUEST_LATENCY = Histogram(
    "http_request_latency_seconds",
    "Request latency",
    ["endpoint"]
)

PAYMENT_FAILURES = Counter(
    "payment_failures_total",
    "Total payment failures"
)

# --------------------
# Helpers
# --------------------
def record_metrics(endpoint, status, start_time):
    REQUEST_COUNT.labels("GET", endpoint, status).inc()
    REQUEST_LATENCY.labels(endpoint).observe(time.time() - start_time)

# --------------------
# Endpoints
# --------------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/orders")
def get_orders():
    start = time.time()
    time.sleep(random.uniform(0.05, 0.2))

    log_event(
        event_type="page_visit",
        service="ecommerce",
        message="Orders page accessed",
        endpoint="/orders"
    )

    record_metrics("/orders", "200", start)
    return {"orders": ["order-1", "order-2"]}

@app.post("/payments")
def process_payment():
    start = time.time()
    time.sleep(random.uniform(0.1, 0.3))

    if FAIL_PAYMENTS or random.random() < 0.3:
        PAYMENT_FAILURES.inc()

        log_event(
            event_type="payment_failure",
            service="payment",
            message="Payment gateway timeout",
            error_code="PG_TIMEOUT",
            dependency="payment-gateway"
        )

        record_metrics("/payments", "500", start)
        raise HTTPException(status_code=500, detail="Payment gateway error")

    log_event(
        event_type="payment_success",
        service="payment",
        message="Payment processed successfully"
    )

    record_metrics("/payments", "200", start)
    return {"status": "payment successful"}

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

