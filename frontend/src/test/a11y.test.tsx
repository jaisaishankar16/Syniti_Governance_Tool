import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe, toHaveNoViolations } from 'jest-axe';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { HomePage } from '@/app/HomePage';
import { ResultsPanel } from '@/features/results/ResultsPanel';
import { AdminPage } from '@/features/admin/AdminPage';
import { SKPConnectionsPage } from '@/features/configuration/SKPConnectionsPage';
import { RefreshMetadataPage } from '@/features/configuration/RefreshMetadataPage';
import { LlmSettingsPage } from '@/features/configuration/LlmSettingsPage';
import { mockBusinessIntelligence } from './fixtures/biFixture';

expect.extend(toHaveNoViolations);

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{ui}</BrowserRouter>
    </QueryClientProvider>
  );
}

describe('Accessibility (a11y) and Keyboard Navigation', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/projects')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => [],
        } as Response);
      }
      if (url.includes('/api/v1/activity')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => [],
        } as Response);
      }
      if (url.includes('/api/v1/llm/status')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => ({
            configured: true,
            model: 'gpt-4o-mini',
            api_mode: 'responses',
            base_url_set: false,
            circuit_state: 'CLOSED',
            budget: { used_tokens: 0, daily_limit: 1000 },
          }),
        } as Response);
      }
      if (url.includes('/api/v1/llm/settings')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => ({
            model: 'gpt-4o-mini',
            api_mode: 'responses',
            structured_output_mode: 'native',
            temperature: 0.1,
            max_turns: 6,
            timeout_s: 60,
            max_retries: 3,
            provider: 'OpenAI-compatible',
            base_url: 'default',
            api_key_configured: true,
            learning_enabled: true,
            tracing_enabled: false,
            token_budgets: {
              max_request_tokens: 50000,
              daily_token_budget: 1000000,
              daily_tokens_used: 0,
              daily_cost_usd: 0,
            },
            circuit_state: 'CLOSED',
            last_error_code: null,
            last_error_at: null,
            prompt_versions: { sql_agent: 'v1', sap_agent: 'v1', synthesizer: 'v1' },
            embedding_model: 'all-MiniLM-L6-v2',
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
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) } as Response);
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('verifies Home page accessibility with jest-axe', async () => {
    const { container } = renderWithClient(<HomePage />);
    // Allow initial query resolutions
    await screen.findByText('Data Migration Projects');
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('verifies ResultsPanel accessibility with jest-axe and keyboard tab navigation', async () => {
    const user = userEvent.setup();
    const { container } = renderWithClient(<ResultsPanel result={mockBusinessIntelligence} />);

    // Assert initial axe violations
    const results = await axe(container);
    expect(results).toHaveNoViolations();

    // Verify keyboard navigation between Radix tabs
    const summaryTab = screen.getByRole('tab', { name: 'Summary' });
    const govTab = screen.getByRole('tab', { name: 'Governance' });

    summaryTab.focus();
    expect(summaryTab).toHaveFocus();

    // Navigate with arrow keys
    await user.keyboard('{ArrowRight}');
    expect(govTab).toHaveFocus();

    // Select tab with Enter
    await user.keyboard('{Enter}');
    expect(await screen.findByText('Supply Chain Management')).toBeInTheDocument();
  });

  it('verifies Admin login view accessibility with jest-axe', async () => {
    const { container } = renderWithClient(<AdminPage />);
    expect(screen.getByText('System Administration')).toBeInTheDocument();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('verifies SKPConnectionsPage accessibility with jest-axe', async () => {
    const { container } = renderWithClient(<SKPConnectionsPage />);
    expect(screen.getByRole('heading', { level: 1, name: 'SKP Connections' })).toBeInTheDocument();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('verifies RefreshMetadataPage accessibility with jest-axe', async () => {
    const { container } = renderWithClient(<RefreshMetadataPage />);
    expect(screen.getByRole('heading', { level: 1, name: 'Refresh Metadata' })).toBeInTheDocument();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('verifies LlmSettingsPage accessibility with jest-axe', async () => {
    const { container } = renderWithClient(<LlmSettingsPage />);
    expect(screen.getByText('LLM Settings & Diagnostics')).toBeInTheDocument();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
