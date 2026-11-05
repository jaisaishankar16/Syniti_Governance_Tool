import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import { SKPConnectionsPage } from './SKPConnectionsPage';
import { validateSkpUrl, skpConnectionStore } from './skpConnectionStore';

describe('SKP Connections and URL Validation Matrix', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('validates URLs across the full validation matrix with validateSkpUrl', () => {
    // Valid cases
    expect(validateSkpUrl('https://skp.enterprise.com').valid).toBe(true);
    expect(validateSkpUrl('https://skp.corp.internal:8443/api/v1').valid).toBe(true);
    expect(validateSkpUrl('http://localhost:8080').valid).toBe(true);
    expect(validateSkpUrl('http://127.0.0.1:3000/skp').valid).toBe(true);

    // Invalid: http on non-localhost
    const httpRemote = validateSkpUrl('http://insecure-skp.enterprise.com');
    expect(httpRemote.valid).toBe(false);
    expect(httpRemote.error).toMatch(/HTTPS is required/);

    // Invalid: credentials embedded
    const creds = validateSkpUrl('https://user:pass@skp.enterprise.com');
    expect(creds.valid).toBe(false);
    expect(creds.error).toMatch(/Embedded credentials/);

    // Invalid: whitespace
    const spaces = validateSkpUrl('https://skp. enterprise.com');
    expect(spaces.valid).toBe(false);

    // Invalid: non-http protocol
    const ftp = validateSkpUrl('ftp://skp.enterprise.com');
    expect(ftp.valid).toBe(false);

    // Invalid: too long (> 2048 chars)
    const longUrl = 'https://skp.enterprise.com/' + 'a'.repeat(2050);
    expect(validateSkpUrl(longUrl).valid).toBe(false);
  });

  it('handles corrupted localStorage data gracefully without throwing', () => {
    localStorage.setItem('syniti_skp_connection_v1', '{ invalid json garbage');
    expect(skpConnectionStore.getSkpUrl()).toBeNull();

    localStorage.setItem('syniti_skp_connection_v1', JSON.stringify({ notAnObject: 123 }));
    expect(skpConnectionStore.getSkpUrl()).toBeNull();
  });

  it('saves and resets SKP connection in UI and persists to storage', async () => {
    const user = userEvent.setup();
    render(
      <BrowserRouter>
        <SKPConnectionsPage />
      </BrowserRouter>
    );

    expect(screen.getByRole('heading', { level: 1, name: 'SKP Connections' })).toBeInTheDocument();

    const input = screen.getByLabelText(/SKP URL/i);
    await user.clear(input);
    await user.type(input, 'https://skp-prod.enterprise.internal/services');

    const saveBtn = screen.getByRole('button', { name: /Save Connection/i });
    await user.click(saveBtn);

    // Verify stored
    const stored = skpConnectionStore.getSkpUrl();
    expect(stored).not.toBeNull();
    expect(stored).toBe('https://skp-prod.enterprise.internal/services');

    // Test Reset / Clear
    const resetBtn = screen.getByRole('button', { name: /Reset/i });
    await user.click(resetBtn);

    expect(skpConnectionStore.getSkpUrl()).toBeNull();
  });
});
