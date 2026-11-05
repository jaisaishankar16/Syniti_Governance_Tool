import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { z } from 'zod';
import { apiFetch, ContractError, ApiError, TimeoutError, NetworkError } from './client';

describe('API Client (apiFetch)', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('captures X-Request-ID and returns parsed payload on success', async () => {
    const mockData = { id: 'p1', name: 'Project 1' };
    const schema = z.object({ id: z.string(), name: z.string() });

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Request-ID': 'req-abc-123' }),
      json: async () => mockData,
    } as Response);

    const result = await apiFetch('/api/v1/projects', { schema });
    expect(result).toEqual(mockData);
  });

  it('throws ContractError when Zod validation fails', async () => {
    // Missing required field 'name'
    const invalidData = { id: 'p1' };
    const schema = z.object({ id: z.string(), name: z.string() });

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Request-ID': 'req-invalid-contract' }),
      json: async () => invalidData,
    } as Response);

    await expect(apiFetch('/api/v1/projects', { schema })).rejects.toThrow(ContractError);

    try {
      await apiFetch('/api/v1/projects', { schema });
    } catch (err) {
      expect(err).toBeInstanceOf(ContractError);
      const cErr = err as ContractError;
      expect(cErr.requestId).toBe('req-invalid-contract');
      expect(cErr.message).toContain('req-invalid-contract');
      expect(cErr.issues.length).toBeGreaterThan(0);
    }
  });

  it('maps error envelope to ApiError for typed server error codes', async () => {
    const errorBody = {
      error: {
        code: 'LLM_NOT_CONFIGURED',
        message: 'OpenAI API key is not configured',
        request_id: 'req-err-456',
        retry_after_s: null,
        details: [{ field: 'key', issue: 'Missing environment variable' }],
      },
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      headers: new Headers({ 'X-Request-ID': 'req-err-456' }),
      json: async () => errorBody,
    } as Response);

    try {
      await apiFetch('/api/v1/analyze', { method: 'POST', body: '{}' });
      expect.unreachable('Should have thrown ApiError');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.code).toBe('LLM_NOT_CONFIGURED');
      expect(apiErr.message).toBe('OpenAI API key is not configured');
      expect(apiErr.requestId).toBe('req-err-456');
      expect(apiErr.status).toBe(503);
      expect(apiErr.details).toHaveLength(1);
    }
  });

  it('maps Retry-After header and body for rate-limited codes', async () => {
    const errorBody = {
      error: {
        code: 'LLM_RATE_LIMITED',
        message: 'Rate limit exceeded',
        request_id: 'req-rate-limit',
        retry_after_s: 30,
        details: [],
      },
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      headers: new Headers({
        'X-Request-ID': 'req-rate-limit',
        'Retry-After': '30',
      }),
      json: async () => errorBody,
    } as Response);

    try {
      await apiFetch('/api/v1/analyze', { method: 'POST', body: '{}' });
      expect.unreachable('Should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.code).toBe('LLM_RATE_LIMITED');
      expect(apiErr.retryAfterS).toBe(30);
    }
  });

  it('times out and throws TimeoutError when request exceeds timeoutMs', async () => {
    global.fetch = vi.fn().mockImplementation((_url, options) => {
      return new Promise((_, reject) => {
        options?.signal?.addEventListener('abort', () => {
          reject(options.signal.reason);
        });
      });
    });

    await expect(apiFetch('/api/v1/projects', { timeoutMs: 50, retries: 0 })).rejects.toThrow(
      TimeoutError
    );
  });

  it('retries GET queries on NetworkError up to 2 times, but does not retry POST', async () => {
    let callCount = 0;
    global.fetch = vi.fn().mockImplementation(() => {
      callCount++;
      if (callCount < 3) {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers({ 'X-Request-ID': 'req-retry' }),
        json: async () => ({ status: 'ok' }),
      } as Response);
    });

    // GET should retry and succeed on 3rd attempt
    const res = await apiFetch<{ status: string }>('/health', { method: 'GET' });
    expect(res.status).toBe('ok');
    expect(callCount).toBe(3);

    // POST should NOT retry on failure
    let postCallCount = 0;
    global.fetch = vi.fn().mockImplementation(() => {
      postCallCount++;
      return Promise.reject(new TypeError('Failed to fetch'));
    });

    await expect(apiFetch('/api/v1/analyze', { method: 'POST', body: '{}' })).rejects.toThrow(
      NetworkError
    );
    expect(postCallCount).toBe(1);
  });

  it('converts an envelope-less 500 (e.g. Vite dev proxy failure) to NetworkError with custom message', async () => {
    let callCount = 0;
    global.fetch = vi.fn().mockImplementation(() => {
      callCount++;
      return Promise.resolve({
        ok: false,
        status: 500,
        headers: new Headers({ 'Content-Type': 'text/plain' }),
        json: async () => {
          throw new SyntaxError('Unexpected token in JSON');
        },
      } as unknown as Response);
    });

    // For GET, it should retry twice (total 3 attempts) and then throw NetworkError
    await expect(apiFetch('/api/v1/projects', { method: 'GET' })).rejects.toThrow(
      'Cannot reach the server. Check that the backend is running.'
    );
    expect(callCount).toBe(3);
  });

  it('preserves ApiError when 500 response contains a valid error envelope', async () => {
    const errorBody = {
      error: {
        code: 'INTERNAL_ERROR',
        message: 'Database query execution failed',
        request_id: 'req-db-fail',
        retry_after_s: null,
        details: [],
      },
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Headers({ 'X-Request-ID': 'req-db-fail' }),
      json: async () => errorBody,
    } as Response);

    await expect(apiFetch('/api/v1/projects', { method: 'GET', retries: 0 })).rejects.toThrow(
      ApiError
    );
  });
});
