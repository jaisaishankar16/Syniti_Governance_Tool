import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { ProjectsSection } from './ProjectsSection';
import type { Project } from '@/api/schemas';

const mockProjects: Project[] = [
  {
    id: 'proj-1',
    name: 'S/4HANA Supply Chain Migration',
    description: 'Material Master plant extensions wave 1',
    status: 'active',
    release_count: 3,
    milestone_count: 9,
  },
  {
    id: 'proj-2',
    name: 'Customer Master Harmonization',
    description: 'Central business partner consolidation',
    status: 'active',
    release_count: 2,
    milestone_count: 5,
  },
];

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

describe('ProjectsSection', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('renders exact empty state text when no projects exist', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Request-ID': 'req-empty' }),
      json: async () => [],
    } as Response);

    renderWithClient(<ProjectsSection />);

    // Must show exact required text per Phase 6.2 spec
    expect(
      await screen.findByText('No projects yet - Click on New project to add')
    ).toBeInTheDocument();
  });

  it('renders project list when projects exist', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Request-ID': 'req-1' }),
      json: async () => mockProjects,
    } as Response);

    renderWithClient(<ProjectsSection />);

    expect(await screen.findByText('S/4HANA Supply Chain Migration')).toBeInTheDocument();
    expect(screen.getByText('Customer Master Harmonization')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New Project/i })).toBeInTheDocument();
  });

  it('opens accessible New Project dialog and handles 501 Not Implemented response gracefully', async () => {
    const user = userEvent.setup();
    global.fetch = vi.fn().mockImplementation((url, init) => {
      if (typeof url === 'string' && url.includes('/api/v1/projects') && init?.method === 'POST') {
        return Promise.resolve({
          ok: false,
          status: 501,
          headers: new Headers({
            'Content-Type': 'application/json',
            'X-Request-ID': 'req-501',
          }),
          json: async () => ({
            error: {
              code: 'NOT_IMPLEMENTED',
              message: 'Project creation is not available yet (backend integration pending)',
              request_id: 'req-501',
            },
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers({ 'X-Request-ID': 'req-get' }),
        json: async () => [],
      } as Response);
    });

    renderWithClient(<ProjectsSection />);

    const newProjectBtn = await screen.findByRole('button', { name: /New Project/i });
    await user.click(newProjectBtn);

    // Dialog title
    expect(await screen.findByRole('heading', { name: 'New Project' })).toBeInTheDocument();

    const nameInput = screen.getByLabelText(/Project Name/i);
    await user.type(nameInput, 'Global Finance Wave 2');

    const submitBtn = screen.getByRole('button', { name: /Create Project/i });
    await user.click(submitBtn);

    // Verify 501 pending message is shown to user
    expect(
      await screen.findByText('Project creation is not available yet (backend integration pending)')
    ).toBeInTheDocument();
  });

  it('filters projects with debounced search input', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Request-ID': 'req-1' }),
      json: async () => mockProjects,
    } as Response);

    renderWithClient(<ProjectsSection />);
    await screen.findByText('S/4HANA Supply Chain Migration');

    const searchInput = screen.getByPlaceholderText('Search projects...');
    fireEvent.change(searchInput, { target: { value: 'Customer' } });

    await waitFor(() => {
      expect(screen.queryByText('S/4HANA Supply Chain Migration')).not.toBeInTheDocument();
      expect(screen.getByText('Customer Master Harmonization')).toBeInTheDocument();
    });

    // Test no match empty state
    fireEvent.change(searchInput, { target: { value: 'NonexistentProject' } });
    await waitFor(() => {
      expect(screen.getByText('No projects match your filter')).toBeInTheDocument();
    });
  });

  it('renders error state and retries on failure', async () => {
    let failRequests = true;
    global.fetch = vi.fn().mockImplementation(() => {
      if (failRequests) {
        return Promise.reject(new Error('Server unavailable'));
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers({ 'X-Request-ID': 'req-retry' }),
        json: async () => mockProjects,
      } as Response);
    });

    renderWithClient(<ProjectsSection />);

    expect(await screen.findByText('Failed to load projects')).toBeInTheDocument();

    failRequests = false;
    const retryBtn = screen.getByRole('button', { name: /Retry/i });
    fireEvent.click(retryBtn);

    expect(await screen.findByText('S/4HANA Supply Chain Migration')).toBeInTheDocument();
  });
});
