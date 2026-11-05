# SKP Metadata Refresh Streaming Contract

This document defines the interface and event stream contract for initiating and monitoring SAP Knowledge Platform (SKP) metadata extraction and vector catalog refreshes.

## 1. Endpoint Specification

- **Path:** `POST /api/v1/metadata/refresh/stream`
- **Content-Type (Request):** `application/json`
- **Content-Type (Response):** `text/event-stream; charset=utf-8`
- **Transfer-Encoding:** chunked
- **Authentication:** Standard user session / Bearer token (optional X-Admin-Key)

### Request Payload

```json
{
  "skp_url": "https://skp.internal.company.com:8443/api"
}
```

| Field | Type | Constraint | Description |
| :--- | :--- | :--- | :--- |
| `skp_url` | string | 1 - 2048 chars | Valid HTTP/HTTPS endpoint. Embedded credentials (`user:pass@`) forbidden. |

---

## 2. Server-Side Security & SSRF Protection

1. **URL Allowlisting:** The backend implementation MUST validate the target `skp_url` against an enterprise CIDR and domain allowlist (e.g. `*.syniti.internal`, `*.corp`). Requests attempting to connect to link-local (`169.254.169.254`), cloud metadata services, or arbitrary external IPs must be rejected immediately with HTTP 422 `INPUT_REJECTED`.
2. **Credential Redaction:** The extractor and logging pipeline MUST NOT echo authorization headers, basic auth credentials, or bearer tokens in any `log` event message, diagnostic detail, or SSE stream chunk.

---

## 3. Server-Sent Events (SSE) Protocol

The endpoint transmits Server-Sent Events over HTTP using the standardized parser format:

### Event Types

#### 1. `event: log`
Emitted for every structured log line produced by the ingestion scanner.
```
event: log
data: {"ts":"2026-11-06T00:15:00.123Z","level":"info","stage":"catalog_scan","message":"Discovered 48 SAP dictionary tables in staging catalog."}
```
- `ts`: ISO-8601 UTC timestamp string.
- `level`: `"info"` | `"warn"` | `"error"`.
- `stage`: Lifecycle stage identifier (e.g. `connect`, `catalog_scan`, `index_build`, `finalize`).
- `message`: Plain-text log message (rendered literally without HTML execution).

#### 2. `event: progress`
Emitted at stage transitions and batch updates.
```
event: progress
data: {"stage":"catalog_scan","percent":45,"counts":[{"label":"Tables","value":48},{"label":"Fields","value":1240}]}
```
- `stage`: Current pipeline stage name.
- `percent`: Optional integer `0 - 100` when determinate progress is known.
- `counts`: Optional list of `{ "label": string, "value": number }` metric tallies.

#### 3. `event: error`
Emitted when an unrecoverable failure occurs during refresh. Matches the standard Syniti Error Envelope.
```
event: error
data: {"error":{"code":"METADATA_EXTRACT_FAILED","message":"Connection refused by SKP host","request_id":"req-skp-123","retry_after_s":null,"details":[]}}
```

#### 4. `event: done`
Emitted upon completion of the refresh stream.
```
event: done
data: {"summary":"Metadata refresh completed. Catalog synchronized."}
```

#### 5. Heartbeat lines
Periodic comment lines every 15 seconds to prevent proxy / load balancer idle timeouts:
```
: ping
```

---

## 4. Current Status

The route currently returns `HTTP 501 NOT_IMPLEMENTED` with error envelope code `"NOT_IMPLEMENTED"` and message `"Metadata refresh is not available on this server yet (backend integration pending)"`. The UI handles 501 and 404 responses cleanly by displaying this integration pending message without injecting simulated or fake logs.

