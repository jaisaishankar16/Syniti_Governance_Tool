import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { Navbar } from './Navbar';
import type { Principal } from '@/api/schemas';

const mockPrincipal: Principal = {
  id: 'usr-123',
  display_name: 'Jai Shankar',
  email: 'jai.shankar@example.com',
  roles: ['data_engineer', 'governance_admin'],
  initials: 'JS',
};

function renderNavbar(initialRoute: string = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialRoute]}>
        <Navbar />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('Navbar Component', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('renders gradient brand title, breadcrumb, validations, and static Admin tag', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Request-ID': 'req-nav' }),
      json: async () => mockPrincipal,
    } as Response);

    renderNavbar('/');

    // Brand link
    const brand = screen.getByText('Syniti Governance Tool');
    expect(brand).toBeInTheDocument();
    expect(brand.closest('a')).toHaveAttribute('href', '/');

    // Breadcrumb on home page
    expect(screen.getByText('Projects')).toBeInTheDocument();

    // Validations link
    expect(screen.getByText('Validations')).toBeInTheDocument();

    // Static Admin TAG (non-interactive pill, not a link or button)
    const adminTags = screen.getAllByText('Admin');
    expect(adminTags.length).toBeGreaterThanOrEqual(1);
    expect(adminTags[0]!.closest('a')).toBeNull();
    expect(adminTags[0]!.closest('button')).toBeNull();
  });

  it('renders dynamic breadcrumbs based on route', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Request-ID': 'req-nav' }),
      json: async () => mockPrincipal,
    } as Response);

    renderNavbar('/configuration/skp-connections');

    const breadcrumb = screen.getByLabelText('Breadcrumb');
    expect(within(breadcrumb).getByText('Configuration')).toBeInTheDocument();
    expect(within(breadcrumb).getByText('SKP Connections')).toBeInTheDocument();
  });

  it('renders user profile avatar with initials and name from /api/v1/me, with Admin console in avatar menu', async () => {
    const user = userEvent.setup();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Request-ID': 'req-nav' }),
      json: async () => mockPrincipal,
    } as Response);

    renderNavbar();

    expect(await screen.findByText('Jai Shankar')).toBeInTheDocument();
    expect(screen.getByText('JS')).toBeInTheDocument();

    // Open avatar profile menu
    const avatarBtn = screen.getByRole('button', { name: /User menu for Jai Shankar/i });
    await user.click(avatarBtn);

    // Should contain Admin console link
    const adminConsoleLink = await screen.findByRole('menuitem', { name: /Admin console/i });
    expect(adminConsoleLink).toBeInTheDocument();
  });

  it('hides profile if /api/v1/me fails', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      headers: new Headers({ 'X-Request-ID': 'req-err' }),
      json: async () => ({ error: { code: 'UNAUTHORIZED', message: 'No auth' } }),
    } as Response);

    renderNavbar();

    await waitFor(() => {
      expect(screen.queryByText('Jai Shankar')).not.toBeInTheDocument();
    });
  });

  it('configuration dropdown has exactly three items: SKP Connections, Refresh Metadata, LLM Settings', async () => {
    const user = userEvent.setup();

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Request-ID': 'req-me' }),
      json: async () => mockPrincipal,
    } as Response);

    renderNavbar();

    // Open Configuration dropdown
    const configTrigger = screen.getByRole('button', { name: /Configuration menu/i });
    await user.click(configTrigger);

    // Exactly 3 menu items in order
    const menuItems = await screen.findAllByRole('menuitem');
    expect(menuItems).toHaveLength(3);
    expect(menuItems[0]).toHaveTextContent('SKP Connections');
    expect(menuItems[1]).toHaveTextContent('Refresh Metadata');
    expect(menuItems[2]).toHaveTextContent('LLM Settings');

    // Confirm old items are absent
    expect(screen.queryByText('LLM Status')).not.toBeInTheDocument();
    expect(screen.queryByText('API Reference')).not.toBeInTheDocument();
  });
});
