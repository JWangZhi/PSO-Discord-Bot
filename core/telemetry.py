"""
Prometheus Metrics Telemetry

Exports internal bot metrics (latency, intent accuracy, request count) to an HTTP endpoint
for Grafana dashboards to consume.
"""

from prometheus_client import Counter, Histogram, start_http_server

# Metrics Definitions
COMMAND_REQUESTS = Counter(
    "pso2bot_command_requests_total",
    "Total slash command usages",
    ["command_name"]
)

INTENT_REQUESTS = Counter(
    "pso2bot_intent_requests_total",
    "Total number of intents classified by the RouterAgent",
    ["intent_type"]
)

MESSAGE_PROCESSING_TIME = Histogram(
    "pso2bot_message_processing_seconds",
    "Time spent processing a user message end-to-end",
    ["intent_type"]
)

EXTERNAL_API_ERRORS = Counter(
    "pso2bot_external_api_errors_total",
    "Total failures communicating with external APIs (Pinecone, Gemini, MongoDB)",
    ["service_name"]
)

def start_metrics_server(port: int = 8000):
    """Start the Prometheus metrics HTTP server."""
    try:
        start_http_server(port)
        print(f"[Telemetry] Prometheus metrics server started on port {port}")
    except Exception as e:
        print(f"[Telemetry] Failed to start metrics server on port {port}: {e}")
