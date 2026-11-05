import { z } from 'zod';
import { ErrorEnvelopeSchema, type ErrorDetail } from './schemas';

// ==============================================================================
// Custom Typed Errors
// ==============================================================================

export class ContractError extends Error {
  readonly requestId: string;
  readonly issues: z.ZodIssue[];

  constructor(requestId: string, zodError: z.ZodError) {
    super(`Unexpected response from the server (Request ID: ${requestId})`);
    this.name = 'ContractError';
    this.requestId = requestId;
    this.issues = zodError.issues;
  }
}

export class ApiError extends Error {
  readonly code: string;
  readonly requestId: string;
  readonly retryAfterS: number | null;
  readonly details: ErrorDetail[];
  readonly status: number;

  constructor(params: {
    code: string;
    message: string;
    requestId: string;
    retryAfterS?: number | null;
    details?: ErrorDetail[];
    status: number;
  }) {
    super(params.message);
    this.name = 'ApiError';
    this.code = params.code;
    this.requestId = params.requestId;
    this.retryAfterS = params.retryAfterS ?? null;
    this.details = params.details ?? [];
    this.status = params.status;
  }
}

export class NetworkError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = 'NetworkError';
  }
}

export class TimeoutError extends Error {
  readonly timeoutMs: number;

  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs / 1000} seconds`);
    this.name = 'TimeoutError';
    this.timeoutMs = timeoutMs;
  }
}

// ==============================================================================
// Client Configuration
// ==============================================================================

export function getApiBaseUrl(): string {
  const url = import.meta.env.VITE_API_BASE_URL || '';
  return url.replace(/\/+$/, '');
}

export interface RequestOptions extends RequestInit {
  timeoutMs?: number;
  schema?: z.ZodTypeAny;
  retries?: number;
  adminKey?: string | null;
}

const DEFAULT_GET_TIMEOUT_MS = 15_000;
export const ANALYZE_TIMEOUT_MS = 130_000;

/**
 * Standard fetch wrapper that handles:
 * - Base URL prefixing
 * - Timeout via AbortController
 * - Capturing X-Request-ID
 * - Parsing and mapping ErrorEnvelope -> ApiError
 * - Validating responses with Zod -> ContractError on mismatch
 * - Retrying GETs on network failure
 */
export async function apiFetch<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const {
    timeoutMs = options.method === 'POST' ? ANALYZE_TIMEOUT_MS : DEFAULT_GET_TIMEOUT_MS,
    schema,
    retries = options.method === 'GET' || !options.method ? 2 : 0,
    adminKey,
    headers: customHeaders,
    ...restInit
  } = options;

  const baseUrl = getApiBaseUrl();
  const url = `${baseUrl}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`;

  const headers = new Headers(customHeaders);
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json');
  }
  if (restInit.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (adminKey) {
    headers.set('X-Admin-Key', adminKey);
  }

  let attempt = 0;
  while (attempt <= retries) {
    attempt++;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort('timeout'), timeoutMs);

    // Link caller's signal if present
    const callerSignal = restInit.signal;
    const onCallerAbort = () => controller.abort(callerSignal?.reason);
    if (callerSignal) {
      if (callerSignal.aborted) {
        clearTimeout(timeoutId);
        throw callerSignal.reason;
      }
      callerSignal.addEventListener('abort', onCallerAbort, { once: true });
    }

    try {
      const response = await fetch(url, {
        ...restInit,
        headers,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);
      if (callerSignal) {
        callerSignal.removeEventListener('abort', onCallerAbort);
      }

      const requestId =
        response.headers.get('x-request-id') || response.headers.get('X-Request-ID') || 'unknown';

      // 1. Handle HTTP error envelopes
      if (!response.ok) {
        let code = 'INTERNAL_ERROR';
        let message = `Request failed with HTTP status ${response.status}`;
        let retryAfterS: number | null = null;
        let details: ErrorDetail[] = [];
        let hasValidEnvelope = false;

        const retryHeader = response.headers.get('retry-after');
        if (retryHeader) {
          const parsed = parseInt(retryHeader, 10);
          if (!isNaN(parsed)) retryAfterS = parsed;
        }

        try {
          const rawJson = await response.json();
          const parsedEnvelope = ErrorEnvelopeSchema.safeParse(rawJson);
          if (parsedEnvelope.success) {
            hasValidEnvelope = true;
            const err = parsedEnvelope.data.error;
            code = err.code;
            message = err.message;
            if (err.retry_after_s != null) {
              retryAfterS = err.retry_after_s;
            }
            details = err.details || [];
          } else if (rawJson?.detail && typeof rawJson.detail === 'string') {
            message = rawJson.detail;
          }
        } catch {
          // Non-JSON error body (e.g. 502/504 HTML page from proxy or ECONNREFUSED 500)
        }

        // 5xx WITHOUT the API error envelope (proxy/gateway/connection failure)
        // must become a NetworkError shown as "Cannot reach the server. Check that the backend is running."
        if (response.status >= 500 && !hasValidEnvelope) {
          throw new NetworkError('Cannot reach the server. Check that the backend is running.');
        }

        throw new ApiError({
          code,
          message,
          requestId,
          retryAfterS,
          details,
          status: response.status,
        });
      }

      // 2. Handle HTTP 204 No Content
      if (response.status === 204) {
        return undefined as unknown as T;
      }

      // 3. Parse JSON response
      let data: unknown;
      try {
        data = await response.json();
      } catch {
        throw new ContractError(
          requestId,
          new z.ZodError([
            {
              code: 'custom',
              path: [],
              message: 'Response is not valid JSON',
            },
          ])
        );
      }

      // 4. Validate with Zod schema if provided
      if (schema) {
        const result = schema.safeParse(data);
        if (!result.success) {
          throw new ContractError(requestId, result.error);
        }
        return result.data as T;
      }

      return data as T;
    } catch (err: unknown) {
      clearTimeout(timeoutId);
      if (callerSignal) {
        callerSignal.removeEventListener('abort', onCallerAbort);
      }

      // Handle abort / timeout
      if (controller.signal.aborted) {
        if (controller.signal.reason === 'timeout') {
          throw new TimeoutError(timeoutMs);
        }
        throw err;
      }

      // If already ApiError or ContractError, do not retry
      if (err instanceof ApiError || err instanceof ContractError || err instanceof TimeoutError) {
        throw err;
      }

      // It's a network/connection error
      const isLastAttempt = attempt > retries;
      if (isLastAttempt) {
        throw new NetworkError(err instanceof Error ? err.message : 'Network connection failed', {
          cause: err,
        });
      }
      // Brief backoff before retry (only for GETs)
      await new Promise((resolve) => setTimeout(resolve, 200 * attempt));
    }
  }

  throw new NetworkError('Network request failed');
}
