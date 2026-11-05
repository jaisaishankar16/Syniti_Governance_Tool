import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { TrialSection } from './TrialSection';
import { resolveError, ERROR_CODE_MAP } from './errorMessages';
import { ApiError } from '@/api/client';
import { mockJsonResponse } from '@/test/mockResponse';

function renderTrialSection(initialSql?: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <TrialSection initialSql={initialSql} />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

function createErrorSSEStream(
  code: string,
  message: string,
  retryAfterS?: number,
  details: Array<{ field?: string | null; issue: string }> = []
): ReadableStream<Uint8Array> {
  const payload = {
    error: {
      code,
      message,
      request_id: 'req-err-suite',
      retry_after_s: retryAfterS ?? null,
      details,
    },
  };
  const text = `event: error\ndata: ${JSON.stringify(payload)}\n\nevent: done\ndata: {}\n\n`;
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
}

describe('Error UX and Error Code Coverage', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.useRealTimers();
  });

  it('maps every typed error code in ERROR_CODE_MAP properly', () => {
    const requiredCodes = [
      'LLM_NOT_CONFIGURED',
      'LLM_AUTH_FAILED',
      'LLM_QUOTA_EXCEEDED',
      'LLM_RATE_LIMITED',
      'CLIENT_RATE_LIMITED',
      'RATE_LIMITED',
      'LLM_BUDGET_EXCEEDED',
      'LLM_UNAVAILABLE',
      'CIRCUIT_BREAKER_OPEN',
      'SYNTHESIS_FAILED',
      'SQL_PARSE_ERROR',
      'INPUT_REJECTED',
      'VALIDATION_ERROR',
      'PAYLOAD_TOO_LARGE',
      'INTERNAL_ERROR',
    ];

    for (const code of requiredCodes) {
      expect(ERROR_CODE_MAP[code], `Missing error mapping for ${code}`).toBeDefined();
      const resolved = resolveError(
        new ApiError({
          code,
          message: 'Server error',
          requestId: 'test-req',
          status: 500,
        })
      );
      expect(resolved.title.length).toBeGreaterThan(0);
      expect(resolved.description.length).toBeGreaterThan(0);
    }
  });

  it('disables Execute and shows amber banner when LLM is not configured', async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/llm/status')) {
        return mockJsonResponse({
          configured: false, // NOT configured
          model: 'gpt-4o-mini',
          api_mode: 'responses',
          base_url_set: false,
          circuit_state: 'CLOSED',
          budget: { used_tokens: 0, daily_limit: 1000 },
          last_error_code: null,
          last_error_at: null,
        });
      }
      return mockJsonResponse({});
    });

    renderTrialSection('SELECT 1');

    expect(await screen.findByText('AI Subsystem Offline')).toBeInTheDocument();
    expect(
      screen.getByText(/AI service is not configured\. The server has no valid API credentials\./)
    ).toBeInTheDocument();

    const executeBtn = screen.getByRole('button', { name: /Execute query analysis/i });
    expect(executeBtn).toBeDisabled();
  });

  it('disables Execute when circuit breaker is OPEN', async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/llm/status')) {
        return mockJsonResponse({
          configured: true,
          model: 'gpt-4o-mini',
          api_mode: 'responses',
          base_url_set: false,
          circuit_state: 'OPEN', // OPEN circuit
          budget: { used_tokens: 0, daily_limit: 1000 },
          last_error_code: null,
          last_error_at: null,
        });
      }
      return mockJsonResponse({});
    });

    renderTrialSection('SELECT 1');

    expect(await screen.findByText(/AI circuit breaker is currently OPEN/)).toBeInTheDocument();
    const executeBtn = screen.getByRole('button', { name: /Execute query analysis/i });
    expect(executeBtn).toBeDisabled();
  });

  it('handles SQL_PARSE_ERROR and extracts line/column indicators', async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/llm/status')) {
        return mockJsonResponse({
          configured: true,
          model: 'gpt-4o-mini',
          api_mode: 'responses',
          base_url_set: false,
          circuit_state: 'CLOSED',
          budget: { used_tokens: 0, daily_limit: 1000 },
          last_error_code: null,
          last_error_at: null,
        });
      }
      if (url.includes('/api/v1/analyze/stream')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({
            'Content-Type': 'text/event-stream',
            'X-Request-ID': 'req-err-suite',
          }),
          body: createErrorSSEStream('SQL_PARSE_ERROR', 'Syntax error in query', undefined, [
            { field: 'line 4, col 12', issue: 'Unexpected token FROM' },
          ]),
        } as unknown as Response);
      }
      return mockJsonResponse({});
    });

    renderTrialSection('SELECT * FROM');

    const executeBtn = screen.getByRole('button', { name: /Execute query analysis/i });
    await waitFor(() => {
      expect(executeBtn).not.toBeDisabled();
    });
    fireEvent.click(executeBtn);

    expect(await screen.findByText('SQL Syntax Error')).toBeInTheDocument();
    expect(screen.getByText(/Syntax parse issue at line 4, column 12/)).toBeInTheDocument();
    expect(screen.getByText('Unexpected token FROM')).toBeInTheDocument();
  });

  it('handles rate-limited error with countdown timer enabling retry', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/llm/status')) {
        return mockJsonResponse({
          configured: true,
          model: 'gpt-4o-mini',
          api_mode: 'responses',
          base_url_set: false,
          circuit_state: 'CLOSED',
          budget: { used_tokens: 0, daily_limit: 1000 },
          last_error_code: null,
          last_error_at: null,
        });
      }
      if (url.includes('/api/v1/analyze/stream')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({
            'Content-Type': 'text/event-stream',
            'X-Request-ID': 'req-err-suite',
          }),
          body: createErrorSSEStream('LLM_RATE_LIMITED', 'Rate limit hit', 3),
        } as unknown as Response);
      }
      return mockJsonResponse({});
    });

    renderTrialSection('SELECT 1');

    const executeBtn = screen.getByRole('button', { name: /Execute query analysis/i });
    await waitFor(() => {
      expect(executeBtn).not.toBeDisabled();
    });
    fireEvent.click(executeBtn);

    // Wait for error card
    expect(await screen.findByText('Rate Limited')).toBeInTheDocument();

    // Check countdown button disabled
    const retryBtn = screen.getByRole('button', { name: /Retry in/i });
    expect(retryBtn).toBeDisabled();

    // Advance timers by 3 seconds
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3500);
    });

    await waitFor(() => {
      const activeRetry = screen.getByRole('button', { name: /Retry Execution/i });
      expect(activeRetry).not.toBeDisabled();
    });
  });
});
