"""ASGI production middleware pipeline coordinating request IDs, logging, security, rate limiting, and metrics."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from typing import Any

from app.api.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    RATE_LIMITED_TOTAL,
)
from app.api.settings import ApiSettings
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("api.access")

REQUEST_ID_REGEX = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def parse_rate_limit(rate_str: str) -> tuple[int, float]:
    """Parses rate limit strings like '20/minute' into (max_requests, window_seconds)."""
    parts = rate_str.strip().split("/")
    count = int(parts[0])
    unit = parts[1].lower() if len(parts) > 1 else "minute"
    window = 60.0
    if unit in ("s", "sec", "second"):
        window = 1.0
    elif unit in ("m", "min", "minute"):
        window = 60.0
    elif unit in ("h", "hr", "hour"):
        window = 3600.0
    elif unit in ("d", "day"):
        window = 86400.0
    return count, window


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter per client key and bucket."""

    def __init__(self) -> None:
        self._history: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, max_requests: int, window_seconds: float) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            timestamps = self._history.setdefault(key, [])
            # Prune expired timestamps
            self._history[key] = [t for t in timestamps if t > cutoff]
            if len(self._history[key]) >= max_requests:
                oldest = self._history[key][0]
                retry_after = max(1, int(oldest + window_seconds - now))
                return False, retry_after
            self._history[key].append(now)
            return True, 0

    def reset_for_test(self) -> None:
        with self._lock:
            self._history.clear()


_GLOBAL_RATE_LIMITER = SlidingWindowRateLimiter()


def get_rate_limiter() -> SlidingWindowRateLimiter:
    """Returns singleton rate limiter."""
    return _GLOBAL_RATE_LIMITER


class ProductionMiddleware:
    """ASGI middleware implementing the Phase 5 production hardening stack in exact order."""

    def __init__(self, app: ASGIApp, settings: ApiSettings) -> None:
        self.app = app
        self.settings = settings
        self.rate_limiter = get_rate_limiter()

        self.analyze_limit, self.analyze_window = parse_rate_limit(settings.rate_limit_analyze)
        self.default_limit, self.default_window = parse_rate_limit(settings.rate_limit_default)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)

        # 1. Request ID Middleware
        incoming_req_id = headers.get("X-Request-ID", "").strip()
        if incoming_req_id and REQUEST_ID_REGEX.match(incoming_req_id):
            request_id = incoming_req_id
        else:
            request_id = str(uuid.uuid4())

        # Ensure state dict exists
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id

        # Bind to structlog contextvars if available
        try:
            import structlog

            structlog.contextvars.bind_contextvars(request_id=request_id)
        except (ImportError, AttributeError):
            pass

        # 2. Trusted Host Validation
        host_header = headers.get("host", "").split(":")[0].strip()
        if (
            self.settings.trusted_hosts
            and "*" not in self.settings.trusted_hosts
            and host_header not in self.settings.trusted_hosts
        ):
            await self._send_error(
                send,
                status_code=400,
                code="INVALID_HOST",
                message="Host header does not match trusted host configuration",
                request_id=request_id,
            )
            return

        # 3. Client IP Resolution (honoring TRUSTED_PROXIES)
        direct_client = scope.get("client")
        direct_ip = direct_client[0] if direct_client else "127.0.0.1"

        client_ip = direct_ip
        if direct_ip in self.settings.trusted_proxies:
            x_forwarded_for = headers.get("X-Forwarded-For", "").strip()
            if x_forwarded_for:
                ips = [ip.strip() for ip in x_forwarded_for.split(",") if ip.strip()]
                # Leftmost untrusted IP
                untrusted = [ip for ip in ips if ip not in self.settings.trusted_proxies]
                if untrusted:
                    client_ip = untrusted[0]
                elif ips:
                    client_ip = ips[0]

        scope["state"]["client_ip"] = client_ip

        # 4. Body Size Limit (Content-Length pre-check)
        content_length_str = headers.get("content-length")
        if content_length_str and content_length_str.isdigit():
            content_length = int(content_length_str)
            if content_length > self.settings.max_body_bytes:
                await self._send_error(
                    send,
                    status_code=413,
                    code="PAYLOAD_TOO_LARGE",
                    message=f"Request entity too large ({content_length} bytes exceeds limit of {self.settings.max_body_bytes})",
                    request_id=request_id,
                )
                return

        # 5. Rate Limiting Check
        path = scope.get("path", "")
        method = scope.get("method", "GET")

        if path.startswith("/api/v1/analyze"):
            bucket = "analyze"
            allowed, retry_after = self.rate_limiter.allow(
                f"analyze:{client_ip}", self.analyze_limit, self.analyze_window
            )
        elif path.startswith("/api/v1/admin") or path in ("/health", "/ready", "/metrics"):
            bucket = "system"
            allowed, retry_after = True, 0
        else:
            bucket = "default"
            allowed, retry_after = self.rate_limiter.allow(
                f"default:{client_ip}", self.default_limit, self.default_window
            )

        if not allowed:
            RATE_LIMITED_TOTAL.labels(bucket=bucket).inc()
            await self._send_error(
                send,
                status_code=429,
                code="RATE_LIMITED",
                message=f"Rate limit exceeded for bucket '{bucket}'. Please retry after {retry_after} seconds.",
                request_id=request_id,
                retry_after_s=retry_after,
            )
            return

        # 6. Streamed Body Size Limit Wrapper
        bytes_read = 0
        body_exceeded = False

        async def limited_receive() -> Message:
            nonlocal bytes_read, body_exceeded
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                bytes_read += len(chunk)
                if bytes_read > self.settings.max_body_bytes:
                    body_exceeded = True
                    raise StarletteHTTPException(
                        status_code=413,
                        detail=f"Request entity too large ({bytes_read} bytes exceeds limit of {self.settings.max_body_bytes})",
                    )
            return message

        # 7. Response Interception (Security Headers, Access Log, Metrics)
        start_time = time.perf_counter()
        status_code = 200
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message.get("status", 200)
                raw_headers = list(message.get("headers", []))
                header_keys = {h[0].lower() for h in raw_headers}

                # Inject Request ID only if not already present
                if b"x-request-id" not in header_keys:
                    raw_headers.append((b"x-request-id", request_id.encode("latin-1")))

                # Inject Security Headers
                if b"x-content-type-options" not in header_keys:
                    raw_headers.append((b"x-content-type-options", b"nosniff"))
                if b"x-frame-options" not in header_keys:
                    raw_headers.append((b"x-frame-options", b"DENY"))
                if b"referrer-policy" not in header_keys:
                    raw_headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))

                # Cache control on API routes
                if path.startswith("/api/") and b"cache-control" not in header_keys:
                    raw_headers.append((b"cache-control", b"no-store, no-cache, must-revalidate"))

                # Restrictive CSP on docs
                if (
                    path in ("/docs", "/redoc", "/openapi.json")
                    and b"content-security-policy" not in header_keys
                ):
                    csp = (
                        b"default-src 'self'; "
                        b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                        b"script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                        b"img-src 'self' data: https://fastapi.tiangolo.com;"
                    )
                    raw_headers.append((b"content-security-policy", csp))

                message["headers"] = raw_headers

            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except StarletteHTTPException as exc:
            if not response_started:
                status_code = exc.status_code
                code = "PAYLOAD_TOO_LARGE" if exc.status_code == 413 else "HTTP_ERROR"
                await self._send_error(
                    send,
                    status_code=exc.status_code,
                    code=code,
                    message=str(exc.detail),
                    request_id=request_id,
                )
            else:
                raise
        except Exception:
            if not response_started:
                status_code = 500
                await self._send_error(
                    send,
                    status_code=500,
                    code="INTERNAL_ERROR",
                    message="An unexpected internal server error occurred. Please contact system administration.",
                    request_id=request_id,
                )
            else:
                raise
        finally:
            duration_s = time.perf_counter() - start_time
            duration_ms = duration_s * 1000

            # Access Log: NEVER log body or SQL
            logger.info(
                "%s %s status=%d duration=%.2fms ip=%s req_id=%s",
                method,
                path,
                status_code,
                duration_ms,
                client_ip,
                request_id,
            )

            # Record Prometheus Metrics (low cardinality route template)
            route_template = self._resolve_route_label(path)
            HTTP_REQUESTS_TOTAL.labels(
                route=route_template, method=method, status=str(status_code)
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(route=route_template).observe(duration_s)

    def _resolve_route_label(self, path: str) -> str:
        """Normalizes dynamic path segments to fixed low-cardinality route labels."""
        if path.startswith("/api/v1/projects/"):
            return "/api/v1/projects/{id}"
        if path.startswith("/api/v1/admin/learned/"):
            return "/api/v1/admin/learned/{id}"
        if path.startswith("/api/v1/analyze"):
            return "/api/v1/analyze" if path == "/api/v1/analyze" else "/api/v1/analyze/stream"
        if path in ("/health", "/ready", "/metrics"):
            return path
        if path.startswith("/api/v1/"):
            # Truncate any query or subpath
            base = path.split("?")[0]
            return base
        return "other"

    async def _send_error(
        self,
        send: Send,
        status_code: int,
        code: str,
        message: str,
        request_id: str,
        retry_after_s: int | None = None,
    ) -> None:
        """Sends an immediate standardized ErrorEnvelope response."""
        body: dict[str, Any] = {
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "retry_after_s": retry_after_s,
                "details": [],
            }
        }
        raw_body = json.dumps(body).encode("utf-8")

        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw_body)).encode("latin-1")),
            (b"x-request-id", request_id.encode("latin-1")),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
        ]
        if retry_after_s is not None:
            headers.append((b"retry-after", str(retry_after_s).encode("latin-1")))

        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": raw_body,
                "more_body": False,
            }
        )
