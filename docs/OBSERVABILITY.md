# Observability — Metrics / Tracing / Edge Rate Limit

> Companion to `PRODUCTIZATION_BACKLOG.md` P0-4. This page captures the
> opinionated default config for the production deployment so SRE can
> wire it up without re-reading the codebase.

## 1. Metrics endpoint

`GET /metrics` (enabled when `ENABLE_METRICS=true`) exposes a small
Prometheus text-format payload. Counters / summaries currently emitted:

| Name | Type | Labels | Source |
|---|---|---|---|
| `ai_photo_coach_analyze_requests_total` | counter | `status` | `api/analyze.py` |
| `ai_photo_coach_analyze_latency_ms` | summary | – | `api/analyze.py` |
| `ai_photo_coach_feedback_requests_total` | counter | `kind` | `api/feedback.py` |
| `ai_photo_coach_recon3d_jobs_total` | counter | `state` | `services/recon3d.py` |
| `ai_photo_coach_circuit_breaker_state` | gauge | `name` | `services/circuit_breaker.py` |

Scrape config (Prometheus / Datadog Agent OpenMetrics):

```yaml
- job_name: ai-photo-coach-backend
  scrape_interval: 30s
  metrics_path: /metrics
  static_configs:
    - targets: ['backend.aiphotocoach.app:443']
      labels:
        env: prod
```

## 2. APM (ddtrace)

Set `ENABLE_DDTRACE=true` and the standard env vars:

```
DD_SERVICE=ai-photo-coach-backend
DD_ENV=prod
DD_VERSION=<git sha>
DD_AGENT_HOST=datadog-agent
```

`main.py` calls `ddtrace.patch_all()` lazily so no code changes are
needed when toggling. FastAPI / httpx / sqlite3 all auto-instrument.

## 3. Datadog dashboard (starter JSON)

Save as `ops/datadog/ai_photo_coach.json` and import via Datadog UI →
*Dashboards → New → Import dashboard JSON*.

```json
{
  "title": "AI Photo Coach — Backend",
  "widgets": [
    {"definition": {"type": "timeseries", "title": "/analyze p95 latency",
      "requests": [{"q": "p95:ai_photo_coach.analyze_latency_ms{env:prod}"}]}},
    {"definition": {"type": "timeseries", "title": "/analyze RPS by status",
      "requests": [{"q": "sum:ai_photo_coach.analyze_requests_total{env:prod} by {status}.as_rate()"}]}},
    {"definition": {"type": "timeseries", "title": "Recon3D job state",
      "requests": [{"q": "sum:ai_photo_coach.recon3d_jobs_total{env:prod} by {state}.as_count()"}]}},
    {"definition": {"type": "query_value", "title": "Circuit breaker open",
      "requests": [{"q": "max:ai_photo_coach.circuit_breaker_state{env:prod}"}],
      "conditional_formats": [{"comparator": ">", "value": 0, "palette": "white_on_red"}]}}
  ],
  "layout_type": "ordered"
}
```

## 4. Edge rate limit (nginx sample)

Application-layer `rate_limit.enforce` is a backstop; do this at the
edge first so abusive clients never hit Python.

```nginx
limit_req_zone $binary_remote_addr zone=analyze_ip:10m rate=6r/m;
limit_req_zone $binary_remote_addr zone=default_ip:10m rate=30r/m;
limit_req_zone $http_x_device_id  zone=analyze_dev:10m rate=20r/m;

server {
  listen 443 ssl http2;
  server_name backend.aiphotocoach.app;

  location /analyze {
    limit_req zone=analyze_ip   burst=4 nodelay;
    limit_req zone=analyze_dev  burst=10 nodelay;
    proxy_pass http://backend_upstream;
  }

  location /recon3d/ {
    limit_req zone=default_ip burst=10 nodelay;
    client_max_body_size 80m;       # 30 imgs * 2MB + slack
    proxy_pass http://backend_upstream;
  }

  location / {
    limit_req zone=default_ip burst=20 nodelay;
    proxy_pass http://backend_upstream;
  }
}
```

## 5. Log redaction

`backend/app/logging_setup.py::_RedactorFilter` scrubs known sensitive
fields (`gps_track`, `keyframes_b64`, `model_api_key`, full
high-precision lat/lon) before they reach any sink. If you add a new
sensitive field, extend `_PATTERNS` there in the same change.

## 6. Privacy data deletion

Self-service: `DELETE /feedback/by_device?device_id=<uuid>` removes all
`shot_results` / `user_spot_votes` rows for that device. Pair this with
an in-app "Delete my data" button (iOS Settings → Privacy).
