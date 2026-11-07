import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { LlmSettingsPage } from './LlmSettingsPage';
import type { LlmSettingsView } from '@/api/schemas';

const mockSettings: LlmSettingsView = {
  model: 'gpt-4o-mini',
  api_mode: 'responses',
  structured_output_mode: 'native',
  tools_mode: 'native',
  temperature: 0.1,
  temperature_supported: true,
  max_turns: 6,
  timeout_s: 60,
  max_retries: 3,
  provider: 'OpenAI-compatible',
  provider_type: 'gateway',
  base_url: 'https://gateway.internal',
  base_host: 'gateway.internal',
  api_key_configured: true,
  capabilities_probe_timestamp: null,
  tracing: 'disabled (forced)',
  learning_enabled: true,
  tracing_enabled: false,
  token_budgets: {
    max_request_tokens: 50000,
    daily_token_budget: 1000000,
    daily_tokens_used: 12500,
    daily_cost_usd: 0.025,
  },
  circuit_state: 'CLOSED',
  last_error_code: null,
  last_error_at: null,
  prompt_versions: {
    sql_agent: 'v1.4.0',
    sap_agent: 'v1.2.0',
    synthesizer: 'v2.0.0',
  },
  embedding_model: 'text-embedding-3-small',
  embeddings: {
    provider_type: 'direct',
    base_host: 'api.openai.com',
    embedding_model: 'text-embedding-3-small',
    dimensions: 1536,
    index_matches_config: true,
    index_built_at: '2026-11-07T00:00:00Z',
    calibrated: true,
    last_embedding_error_code: null,
    pricing_configured: false,
  },
  knowledge_index_status: {
    sap_index_built: true,
    sql_index_built: true,
    verified_entries_present: true,
  },
  cache_backend: {
    type: 'auto',
    l1_ttl_s: 86400,
    l2_ttl_s: 86400,
    similarity_threshold: 0.97,
  },
  guardrail_modes: {
    max_sql_length: 50000,
    concurrency_limit: 10,
    require_subset_containment: true,
    risk_floor_elevation: true,
  },
  pricing_configured: false,
};

function renderLlmSettings() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <LlmSettingsPage />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

describe('LlmSettingsPage', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('renders read-only diagnostic sections and verifies zero secret leakage', async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/llm/settings')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ 'X-Request-ID': 'req-llm-diag' }),
          json: async () => mockSettings,
        } as Response);
      }
      return Promise.reject(new Error(`Unexpected fetch: ${url}`));
    });

    renderLlmSettings();

    expect(screen.getByText('LLM Settings & Diagnostics')).toBeInTheDocument();

    // Verify key fields are rendered
    expect(await screen.findByText('gpt-4o-mini')).toBeInTheDocument();
    expect(screen.getByText('OpenAI-compatible')).toBeInTheDocument();
    expect(screen.getByText('https://gateway.internal')).toBeInTheDocument();
    expect(screen.getByText('CLOSED')).toBeInTheDocument();
    expect(screen.getByText('text-embedding-3-small')).toBeInTheDocument();

    // Verify How to Change These Settings section
    expect(screen.getByText('How to Change These Settings')).toBeInTheDocument();
    expect(screen.getByText('LLM_MODEL')).toBeInTheDocument();
    expect(screen.getByText('OPENAI_BASE_URL')).toBeInTheDocument();

    // Zero secret leakage in DOM
    const html = document.body.innerHTML;
    expect(html).not.toMatch(/sk-proj/);
    expect(html).not.toMatch(/api[_-]?key[_-]?secret/i);
  });
});
