import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { AdminPage } from './AdminPage';
import type { CacheStats, LearnedSapEntry } from '@/api/schemas';
import { mockJsonResponse } from '@/test/mockResponse';

const mockCacheStats: CacheStats = {
  total_requests: 120,
  l1_hits: 40,
  l1_misses: 80,
  l1_hit_ratio: 0.333,
  l2_sql_hits: 15,
  l2_sql_misses: 25,
  l2_sap_hits: 18,
  l2_sap_misses: 22,
  l3_similarity_reuses: 5,
  l3_similarity_hints: 3,
  entries_count: 50,
  backend_type: 'memory',
  backend_healthy: true,
  estimated_tokens_saved: 15000,
  estimated_cost_saved_usd: 12.5,
};

const mockLearnedSap: LearnedSapEntry[] = [
  {
    id: 'lrn-1',
    table: 'MARC',
    field: 'ZACTIVE',
    description: 'Plant active indicator',
    business_meaning: 'Active status',
    module: 'MM',
    evidence: {
      chunk_id: 'chk-1',
      source_file: 'sap_ecc_mm.pdf',
      page: 42,
      evidence_text: 'ZACTIVE specifies active material record',
      retrieval_score: 0.88,
      match_type: 'exact',
    },
    confidence: 0.95,
    source: 'retrieved',
    status: 'pending',
    created_at: '2026-03-01T12:00:00Z',
    last_seen_at: '2026-03-02T12:00:00Z',
    hit_count: 5,
  },
];

function renderAdminPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AdminPage />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

describe('Admin Page Controls and Security', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('gates access with in-memory admin key and handles 401 unauthorized', async () => {
    const user = userEvent.setup();

    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/cache/stats')) {
        return mockJsonResponse(
          { error: { code: 'UNAUTHORIZED', message: 'Invalid admin key' } },
          401
        );
      }
      return mockJsonResponse({});
    });

    renderAdminPage();

    expect(screen.getByText('Enter Admin Credentials')).toBeInTheDocument();

    const input = screen.getByLabelText(/Admin Secret Key/i);
    await user.type(input, 'wrong-secret');

    const submitBtn = screen.getByRole('button', { name: /Authenticate Session/i });
    await user.click(submitBtn);

    expect(await screen.findByText('Invalid admin key. Access denied.')).toBeInTheDocument();

    // Verify key was NEVER persisted to storage
    expect(localStorage.getItem('adminKey')).toBeNull();
    expect(sessionStorage.getItem('adminKey')).toBeNull();
  });

  it('handles 404 when Admin API is disabled on the server', async () => {
    const user = userEvent.setup();

    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/cache/stats')) {
        return mockJsonResponse({ error: { code: 'NOT_FOUND', message: 'Not found' } }, 404);
      }
      return mockJsonResponse({});
    });

    renderAdminPage();

    const input = screen.getByLabelText(/Admin Secret Key/i);
    await user.type(input, 'my-key');

    const submitBtn = screen.getByRole('button', { name: /Authenticate Session/i });
    await user.click(submitBtn);

    expect(await screen.findByText('Admin API is not enabled on the server.')).toBeInTheDocument();
  });

  it('authenticates, renders cache stats, and allows cache purge with confirmation', async () => {
    const user = userEvent.setup();

    global.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/cache/stats')) {
        return mockJsonResponse(mockCacheStats);
      }

      if (url.includes('/api/v1/llm/status')) {
        return mockJsonResponse({
          configured: true,
          model: 'gpt-4o-mini',
          api_mode: 'responses',
          base_url_set: false,
          circuit_state: 'CLOSED',
          budget: { used_tokens: 500, daily_limit: 10000 },
          last_error_code: null,
          last_error_at: null,
        });
      }

      if (url.includes('/api/v1/cache') && init?.method === 'DELETE') {
        return mockJsonResponse({ cleared: true, kind: 'all' });
      }

      return mockJsonResponse([]);
    });

    renderAdminPage();

    const input = screen.getByLabelText(/Admin Secret Key/i);
    await user.type(input, 'valid-secret-key');

    const submitBtn = screen.getByRole('button', { name: /Authenticate Session/i });
    await user.click(submitBtn);

    // Verify stats render
    expect(await screen.findByText('Total Requests')).toBeInTheDocument();
    expect(screen.getByText('120')).toBeInTheDocument();
    expect(screen.getByText('33.3%')).toBeInTheDocument();

    // Open Clear Cache dialog
    const clearBtn = screen.getByRole('button', { name: /Clear Cache/i });
    await user.click(clearBtn);

    expect(await screen.findByText('Confirm Cache Eviction')).toBeInTheDocument();

    // Confirm purge
    const confirmBtn = screen.getByRole('button', { name: /Confirm Purge/i });
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(screen.queryByText('Confirm Cache Eviction')).not.toBeInTheDocument();
    });
  });

  it('manages learned knowledge approval flow with confirmation dialog', async () => {
    const user = userEvent.setup();

    global.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/cache/stats')) {
        return mockJsonResponse(mockCacheStats);
      }
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
      if (url.includes('/api/v1/admin/learned') && !url.includes('/approve')) {
        return mockJsonResponse(mockLearnedSap);
      }
      if (url.includes('/approve') && init?.method === 'POST') {
        return mockJsonResponse({ id: 'lrn-1', status: 'approved', auditor: 'admin@test.com' });
      }
      return mockJsonResponse([]);
    });

    renderAdminPage();

    // Authenticate
    await user.type(screen.getByLabelText(/Admin Secret Key/i), 'secret');
    await user.click(screen.getByRole('button', { name: /Authenticate Session/i }));

    // Switch to Learned Knowledge tab
    const learnedTab = await screen.findByRole('tab', { name: /Learned Knowledge/i });
    await user.click(learnedTab);

    // Verify entry is listed
    expect(await screen.findByText('MARC.ZACTIVE')).toBeInTheDocument();
    expect(screen.getByText('Plant active indicator')).toBeInTheDocument();

    // Click Approve
    const approveBtn = screen.getByRole('button', { name: /Approve/i });
    await user.click(approveBtn);

    // Confirmation dialog
    expect(await screen.findByText('Confirm approve Entry')).toBeInTheDocument();
    const confirmBtn = screen.getByRole('button', { name: 'Confirm' });
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(screen.queryByText('Confirm approve Entry')).not.toBeInTheDocument();
    });
  });
});
