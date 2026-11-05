import {
  ProgressEventSchema,
  BusinessIntelligenceSchema,
  ErrorEnvelopeSchema,
  type ProgressEvent,
  type BusinessIntelligence,
  type ErrorEnvelope,
} from './schemas';
import { apiFetch, getApiBaseUrl, ApiError, NetworkError } from './client';

export type SSEResultEvent =
  | { type: 'progress'; data: ProgressEvent }
  | { type: 'result'; data: BusinessIntelligence }
  | { type: 'error'; data: ErrorEnvelope }
  | { type: 'done'; data: Record<string, unknown> };

export interface RawSSEMessage {
  event?: string;
  dataLines: string[];
  id?: string;
}

export const SSE_IDLE_TIMEOUT_MS = 45_000;

/**
 * Low-level SSE parser that takes an async iterable of string or Uint8Array chunks,
 * handles chunk splits mid-line and mid-event, CRLF/LF, heartbeats, and multi-line data.
 */
export async function* parseSSEStream(
  readable: ReadableStream<Uint8Array>,
  options: {
    idleTimeoutMs?: number;
    signal?: AbortSignal;
    onHeartbeat?: () => void;
  } = {}
): AsyncGenerator<SSEResultEvent, void, unknown> {
  const { idleTimeoutMs = SSE_IDLE_TIMEOUT_MS, signal, onHeartbeat } = options;
  const reader = readable.getReader();
  const decoder = new TextDecoder('utf-8');

  let buffer = '';
  let currentEvent: string | undefined = undefined;
  let currentDataLines: string[] = [];

  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  let idleTimeoutTriggered = false;

  const resetIdleTimer = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      idleTimeoutTriggered = true;
      reader.cancel('Idle timeout');
    }, idleTimeoutMs);
  };

  resetIdleTimer();

  try {
    while (true) {
      if (signal?.aborted) {
        throw signal.reason || new Error('Aborted');
      }

      const readPromise = reader.read();
      const { done, value } = await readPromise;

      if (done) {
        break;
      }

      // Any received bytes reset the idle timeout
      resetIdleTimer();

      buffer += decoder.decode(value, { stream: true });

      // Normalize CRLF and CR to LF
      let newlineIdx: number;
      while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
        let line = buffer.slice(0, newlineIdx);
        buffer = buffer.slice(newlineIdx + 1);

        if (line.endsWith('\r')) {
          line = line.slice(0, -1);
        }

        // Empty line indicates event boundary
        if (line === '') {
          if (currentEvent !== undefined || currentDataLines.length > 0) {
            const eventType = currentEvent || 'message';
            const joinedData = currentDataLines.join('\n');

            // Dispatch known events
            if (eventType === 'progress') {
              try {
                const parsedJson = JSON.parse(joinedData);
                const validated = ProgressEventSchema.parse(parsedJson);
                yield { type: 'progress', data: validated };
              } catch (err) {
                throw new Error(
                  `Malformed JSON in progress event: ${err instanceof Error ? err.message : String(err)}`
                );
              }
            } else if (eventType === 'result') {
              try {
                const parsedJson = JSON.parse(joinedData);
                const validated = BusinessIntelligenceSchema.parse(parsedJson);
                yield { type: 'result', data: validated };
              } catch (err) {
                throw new Error(
                  `Malformed JSON in result event: ${err instanceof Error ? err.message : String(err)}`
                );
              }
            } else if (eventType === 'error') {
              try {
                const parsedJson = JSON.parse(joinedData);
                const validated = ErrorEnvelopeSchema.parse(parsedJson);
                yield { type: 'error', data: validated };
              } catch (err) {
                throw new Error(
                  `Malformed JSON in error event: ${err instanceof Error ? err.message : String(err)}`
                );
              }
            } else if (eventType === 'done') {
              let parsedJson = {};
              if (joinedData.trim()) {
                try {
                  parsedJson = JSON.parse(joinedData);
                } catch {
                  // Done payload is often empty or '{}'
                }
              }
              yield { type: 'done', data: parsedJson };
            }
            // Unknown event types are silently ignored according to requirements
          }

          // Reset event state
          currentEvent = undefined;
          currentDataLines = [];
          continue;
        }

        // Comment line (e.g. ": heartbeat")
        if (line.startsWith(':')) {
          onHeartbeat?.();
          continue;
        }

        // Field parsing
        const colonIdx = line.indexOf(':');
        let field: string;
        let val = '';
        if (colonIdx === -1) {
          field = line;
        } else {
          field = line.slice(0, colonIdx);
          val = line.slice(colonIdx + 1);
          if (val.startsWith(' ')) {
            val = val.slice(1);
          }
        }

        if (field === 'event') {
          currentEvent = val;
        } else if (field === 'data') {
          currentDataLines.push(val);
        }
      }
    }
  } finally {
    if (idleTimer) clearTimeout(idleTimer);
    reader.releaseLock();
  }

  if (idleTimeoutTriggered) {
    throw new NetworkError('Connection lost');
  }
}

export interface StreamAnalysisCallbacks {
  onProgress?: (event: ProgressEvent) => void;
  onResult?: (result: BusinessIntelligence) => void;
  onError?: (error: ErrorEnvelope | ApiError | Error) => void;
  onFallback?: () => void;
  onDone?: () => void;
}

/**
 * Executes streaming analysis with fallback support:
 * If stream fails BEFORE any event because of a network/proxy error (not an API error envelope),
 * retries once through POST /api/v1/analyze and notifies onFallback().
 */
export async function streamAnalysis(
  params: {
    sql: string;
    force_refresh?: boolean;
    signal?: AbortSignal;
  },
  callbacks: StreamAnalysisCallbacks = {}
): Promise<void> {
  const baseUrl = getApiBaseUrl();
  const url = `${baseUrl}/api/v1/analyze/stream`;
  let eventReceived = false;

  const controller = new AbortController();
  const onSignalAbort = () => controller.abort(params.signal?.reason);
  if (params.signal) {
    if (params.signal.aborted) {
      throw params.signal.reason;
    }
    params.signal.addEventListener('abort', onSignalAbort, { once: true });
  }

  try {
    let response: Response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({
          sql: params.sql,
          force_refresh: !!params.force_refresh,
        }),
        signal: controller.signal,
      });
    } catch (networkErr) {
      if (controller.signal.aborted) {
        throw networkErr;
      }
      // Initial network error before connection -> fallback
      return await fallbackToSyncAnalyze(params, callbacks);
    }

    const requestId =
      response.headers.get('x-request-id') || response.headers.get('X-Request-ID') || 'unknown';

    // If response is not 200 OK
    if (!response.ok) {
      // Check if server returned an ErrorEnvelope
      try {
        const json = await response.json();
        const parsedEnvelope = ErrorEnvelopeSchema.safeParse(json);
        if (parsedEnvelope.success) {
          const apiErr = new ApiError({
            code: parsedEnvelope.data.error.code,
            message: parsedEnvelope.data.error.message,
            requestId,
            retryAfterS: parsedEnvelope.data.error.retry_after_s,
            details: parsedEnvelope.data.error.details,
            status: response.status,
          });
          callbacks.onError?.(apiErr);
          return;
        }
      } catch {
        // Non-JSON response (e.g. 502/504 Bad Gateway from proxy)
      }

      // Proxy error before any event -> fallback once
      return await fallbackToSyncAnalyze(params, callbacks);
    }

    if (!response.body) {
      throw new NetworkError('Empty response body received for event stream');
    }

    // Stream reading
    for await (const sse of parseSSEStream(response.body, { signal: controller.signal })) {
      eventReceived = true;
      if (sse.type === 'progress') {
        callbacks.onProgress?.(sse.data);
      } else if (sse.type === 'result') {
        callbacks.onResult?.(sse.data);
      } else if (sse.type === 'error') {
        const envelope = sse.data;
        const apiErr = new ApiError({
          code: envelope.error.code,
          message: envelope.error.message,
          requestId: envelope.error.request_id,
          retryAfterS: envelope.error.retry_after_s ?? undefined,
          details: envelope.error.details,
          status: 400,
        });
        callbacks.onError?.(apiErr);
      } else if (sse.type === 'done') {
        callbacks.onDone?.();
      }
    }
  } catch (err: unknown) {
    if (params.signal?.aborted) {
      return;
    }

    // If stream failed before any event due to network/proxy error, fallback
    if (
      !eventReceived &&
      (err instanceof NetworkError || (err instanceof Error && err.name === 'TypeError'))
    ) {
      return await fallbackToSyncAnalyze(params, callbacks);
    }

    if (err instanceof Error) {
      callbacks.onError?.(err);
    } else {
      callbacks.onError?.(new Error(String(err)));
    }
  } finally {
    if (params.signal) {
      params.signal.removeEventListener('abort', onSignalAbort);
    }
  }
}

async function fallbackToSyncAnalyze(
  params: { sql: string; force_refresh?: boolean; signal?: AbortSignal },
  callbacks: StreamAnalysisCallbacks
): Promise<void> {
  callbacks.onFallback?.();
  try {
    const result = await apiFetch<BusinessIntelligence>('/api/v1/analyze', {
      method: 'POST',
      body: JSON.stringify({
        sql: params.sql,
        force_refresh: !!params.force_refresh,
      }),
      schema: BusinessIntelligenceSchema,
      signal: params.signal,
      retries: 0,
    });
    callbacks.onResult?.(result);
    callbacks.onDone?.();
  } catch (err: unknown) {
    if (params.signal?.aborted) return;
    if (err instanceof ApiError || err instanceof Error) {
      callbacks.onError?.(err);
    } else {
      callbacks.onError?.(new Error(String(err)));
    }
  }
}
