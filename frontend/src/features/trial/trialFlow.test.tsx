import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { TrialSection } from './TrialSection';
import { mockBusinessIntelligence } from '@/test/fixtures/biFixture';
import { mockJsonResponse } from '@/test/mockResponse';
import { MAX_SQL_LENGTH } from '@/api/limits';

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

function createSSEChunkStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

describe('Trial Flow and Results Presentation', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('runs analysis via SSE stream and renders every field of BusinessIntelligence', async () => {
    const user = userEvent.setup();

    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/llm/status')) {
        return mockJsonResponse({
          configured: true,
          model: 'gpt-4o-mini',
          api_mode: 'responses',
          base_url_set: false,
          circuit_state: 'CLOSED',
          budget: { used_tokens: 100, daily_limit: 10000 },
          last_error_code: null,
          last_error_at: null,
        });
      }

      if (url.includes('/api/v1/analyze/stream')) {
        const streamData = [
          'event: progress\ndata: {"stage":"guardrails","message":"Checking input","elapsed_ms":10,"detail":{}}\n\n',
          'event: progress\ndata: {"stage":"parsing","message":"Parsing SQL","elapsed_ms":25,"detail":{}}\n\n',
          'event: progress\ndata: {"stage":"cache_lookup","message":"Checking cache","elapsed_ms":35,"detail":{}}\n\n',
          'event: progress\ndata: {"stage":"agents_running","message":"Agents running","elapsed_ms":200,"detail":{"sql":"running","sap":"running"}}\n\n',
          'event: progress\ndata: {"stage":"synthesizing","message":"Synthesizing","elapsed_ms":400,"detail":{}}\n\n',
          'event: progress\ndata: {"stage":"validating","message":"Validating","elapsed_ms":500,"detail":{}}\n\n',
          `event: result\ndata: ${JSON.stringify(mockBusinessIntelligence)}\n\n`,
          'event: done\ndata: {}\n\n',
        ];

        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({
            'Content-Type': 'text/event-stream',
            'X-Request-ID': 'req-fixture-98765',
          }),
          body: createSSEChunkStream(streamData),
        } as unknown as Response);
      }

      return Promise.reject(new Error(`Unexpected call to ${url}`));
    });

    renderTrialSection();

    // 1. Execute button initially disabled because input is empty
    const executeBtn = screen.getByRole('button', { name: /Execute query analysis/i });
    expect(executeBtn).toBeDisabled();

    // 2. Set SQL in editor
    const sqlInput = await screen.findByTestId('sql-editor-input');
    fireEvent.change(sqlInput, {
      target: { value: 'SELECT MARC.MATNR FROM dbo.MARC WHERE MARC.ZACTIVE = 1' },
    });

    // Execute button should now be enabled
    await waitFor(() => {
      expect(executeBtn).not.toBeDisabled();
    });

    // 3. Click Execute
    await user.click(executeBtn);

    // 4. Verify Results Panel appears after stream finishes
    expect(await screen.findByText('Executive Business Narrative')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Executive summary explaining active material records across manufacturing plants.'
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText('Performs INNER JOIN between MARC and MARA filtering on ZACTIVE = 1.')
    ).toBeInTheDocument();

    // Verify affected entities chips
    expect(screen.getByText('MARC')).toBeInTheDocument();
    expect(screen.getByText('MARA')).toBeInTheDocument();

    // Verify risk summary and recommendations
    expect(screen.getByText(/Missing secondary index on MARC.ZACTIVE/)).toBeInTheDocument();
    expect(
      screen.getByText(/Create composite index on \(MANDT, MATNR, ZACTIVE\)/)
    ).toBeInTheDocument();

    // Verify final learning
    expect(
      screen.getByText(/Plant status flag ZACTIVE is deprecated in S\/4HANA/)
    ).toBeInTheDocument();

    // Verify Degraded Mode Alert Banner
    expect(screen.getByText('Analysis Executed in Degraded Mode')).toBeInTheDocument();
    expect(
      screen.getByText(/SAP Agent timed out during vector index retrieval/)
    ).toBeInTheDocument();

    // Verify Audit Warnings List
    expect(screen.getByText('Audit Warnings')).toBeInTheDocument();
    expect(
      screen.getByText(/Rule term Active Material Plant Status was newly created/)
    ).toBeInTheDocument();

    // 5. Navigate to Governance Tab
    const govTab = screen.getByRole('tab', { name: 'Governance' });
    await user.click(govTab);

    // Assert all governance fields and New badges
    expect(screen.getByText('Supply Chain Management')).toBeInTheDocument();
    expect(screen.getByText('Procure to Pay')).toBeInTheDocument();
    expect(screen.getByText('Material Master Plant Extension Data')).toBeInTheDocument();
    expect(screen.getAllByText(/Active Material Plant Status/).length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByText('Material Must Have Valid Active Plant Assignment')
    ).toBeInTheDocument();
    expect(screen.getByText(/Every MARC record where WERKS in \(1000, 2000\)/)).toBeInTheDocument();
    expect(screen.getByText(/Block migration wave and flag for data steward/)).toBeInTheDocument();
    expect(
      screen.getByText(/All MARC records for operational plants 1000 and 2000/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/MARC records in plants 1000 and 2000 where ZACTIVE is 0 or NULL/)
    ).toBeInTheDocument();

    // Assert "New" badges exist on newly proposed terms
    const newBadges = screen.getAllByText('New');
    expect(newBadges.length).toBeGreaterThanOrEqual(2);

    // 6. Navigate to Queries Tab
    const queriesTab = screen.getByRole('tab', { name: 'Queries' });
    await user.click(queriesTab);

    expect(screen.getByText(/AI-generated SQL. Review before running/)).toBeInTheDocument();
    expect(screen.getAllByText('Valid SQL').length).toBe(2);
    expect(screen.getByText('Subset check passed')).toBeInTheDocument();
    expect(screen.getByText('Matches parser baseline benchmark')).toBeInTheDocument();

    // Test Copy button on queries
    const spy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined);
    const copyQueryBtn = screen.getByRole('button', { name: /Copy Opportunity Query/i });
    fireEvent.click(copyQueryBtn);
    await waitFor(() => {
      expect(spy).toHaveBeenCalled();
    });

    // 7. Navigate to Run Details Tab
    const runDetailsTab = screen.getByRole('tab', { name: 'Run Details' });
    await user.click(runDetailsTab);

    expect(screen.getByText('v1.4.0')).toBeInTheDocument();
    expect(screen.getByText('v1.2.0')).toBeInTheDocument();
    expect(screen.getByText('v2.0.0')).toBeInTheDocument();
    expect(screen.getByText(/Estimated Cost:\s*\$0\.0035/)).toBeInTheDocument();
    expect(screen.getByText('sqlglot_parse_guard')).toBeInTheDocument();
    expect(screen.getByText('sensitive_keyword_check')).toBeInTheDocument();

    // 8. Navigate to Raw JSON Tab
    const rawJsonTab = screen.getByRole('tab', { name: 'Raw JSON' });
    await user.click(rawJsonTab);

    expect(screen.getByRole('button', { name: /Copy JSON/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Download/i })).toBeInTheDocument();
  });

  it('enforces SQL size limit and disables Execute button with inline error', async () => {
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
      return mockJsonResponse({});
    });

    renderTrialSection();

    const sqlInput = await screen.findByTestId('sql-editor-input');
    const executeBtn = screen.getByRole('button', { name: /Execute query analysis/i });

    // Exceed max length
    const hugeSql = 'SELECT ' + 'x'.repeat(MAX_SQL_LENGTH + 10);
    fireEvent.change(sqlInput, { target: { value: hugeSql } });

    await waitFor(() => {
      expect(
        screen.getByText(
          new RegExp(
            `Query exceeds maximum allowed length of ${MAX_SQL_LENGTH.toLocaleString()} characters`
          )
        )
      ).toBeInTheDocument();
      expect(executeBtn).toBeDisabled();
    });
  });

  it('prevents XSS by rendering untrusted tags strictly as literal text', async () => {
    const xssPayload: typeof mockBusinessIntelligence = {
      ...mockBusinessIntelligence,
      business_meaning: '<img src=x onerror=alert(1)> <script>alert(2)</script>',
      technical_summary: '<svg onload=alert(3)>',
    };

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
        const streamData = [
          `event: result\ndata: ${JSON.stringify(xssPayload)}\n\n`,
          'event: done\ndata: {}\n\n',
        ];
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ 'Content-Type': 'text/event-stream' }),
          body: createSSEChunkStream(streamData),
        } as unknown as Response);
      }
      return mockJsonResponse([]);
    });

    renderTrialSection('SELECT MARC.MATNR FROM dbo.MARC WHERE MARC.ZACTIVE = 1');

    const executeBtn = screen.getByRole('button', { name: /Execute query analysis/i });
    await waitFor(() => {
      expect(executeBtn).not.toBeDisabled();
    });

    fireEvent.click(executeBtn);

    // Wait for result to render
    await screen.findByText('Executive Business Narrative');

    // Confirm that no <img> or <script> tags were actually injected into the DOM
    const imgElement = document.querySelector('img[src="x"]');
    expect(imgElement).toBeNull();

    const scriptElement = document.querySelector('script:not([type="module"])');
    expect(scriptElement).toBeNull();

    // Verify raw text is rendered safely as plain string
    expect(
      screen.getByText('<img src=x onerror=alert(1)> <script>alert(2)</script>')
    ).toBeInTheDocument();
  });

  it('falls back once to POST /api/v1/analyze when stream fails before any event', async () => {
    let streamCalled = false;
    let syncCalled = false;

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
        streamCalled = true;
        // Network or proxy disconnect before any event
        return Promise.reject(new TypeError('Failed to fetch'));
      }

      if (url.endsWith('/api/v1/analyze')) {
        syncCalled = true;
        return mockJsonResponse(mockBusinessIntelligence);
      }

      return mockJsonResponse([]);
    });

    renderTrialSection('SELECT MARC.MATNR FROM dbo.MARC WHERE MARC.ZACTIVE = 1');

    const executeBtn = screen.getByRole('button', { name: /Execute query analysis/i });
    await waitFor(() => {
      expect(executeBtn).not.toBeDisabled();
    });

    fireEvent.click(executeBtn);

    // Should display fallback notice
    expect(
      await screen.findByText(
        /Live progress unavailable\. Analysis running in synchronous fallback mode\./
      )
    ).toBeInTheDocument();

    // And result should render successfully
    expect(await screen.findByText('Executive Business Narrative')).toBeInTheDocument();
    expect(streamCalled).toBe(true);
    expect(syncCalled).toBe(true);
  });
});
