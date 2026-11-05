import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import { RefreshMetadataPage } from './RefreshMetadataPage';
import { skpConnectionStore } from './skpConnectionStore';

describe('RefreshMetadataPage', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    localStorage.clear();
  });

  it('renders prompt to configure connection when no SKP URL is configured', () => {
    render(
      <BrowserRouter>
        <RefreshMetadataPage />
      </BrowserRouter>
    );

    expect(screen.getByRole('heading', { level: 1, name: 'Refresh Metadata' })).toBeInTheDocument();
    expect(
      screen.getByText(/No SKP URL configured\. Configure a connection to refresh metadata\./)
    ).toBeInTheDocument();
    const configLink = screen.getByRole('link', { name: /Change Connection/i });
    expect(configLink).toHaveAttribute('href', '/configuration/skp-connections');
  });

  it('shows active SKP connection card and allows initiating refresh with terminal logging', async () => {
    const user = userEvent.setup();
    skpConnectionStore.saveSkpUrl('https://skp.enterprise.internal/api');

    global.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/metadata/refresh/stream') && init?.method === 'POST') {
        return Promise.resolve({
          ok: false,
          status: 501,
          headers: new Headers({
            'Content-Type': 'application/json',
            'X-Request-ID': 'req-meta-501',
          }),
          json: async () => ({
            error: {
              code: 'NOT_IMPLEMENTED',
              message:
                'Metadata refresh is not available on this server yet (backend integration pending)',
              request_id: 'req-meta-501',
            },
          }),
        } as Response);
      }
      return Promise.reject(new Error(`Unexpected fetch: ${url}`));
    });

    render(
      <BrowserRouter>
        <RefreshMetadataPage />
      </BrowserRouter>
    );

    expect(screen.getByText('Target SKP Connection')).toBeInTheDocument();
    expect(screen.getByText('https://skp.enterprise.internal/api')).toBeInTheDocument();

    const startBtn = screen.getByRole('button', { name: /Refresh Metadata/i });
    expect(startBtn).toBeEnabled();

    await user.click(startBtn);

    // Terminal should output logs and integration notice
    expect(
      await screen.findByText(/Metadata refresh is not available on this server yet/)
    ).toBeInTheDocument();

    // Terminal controls: Clear button exists
    const clearBtn = screen.getByTitle('Clear terminal');
    expect(clearBtn).toBeInTheDocument();
  });
});
