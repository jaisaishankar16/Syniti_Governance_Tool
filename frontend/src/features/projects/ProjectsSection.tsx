import React, { useState, useMemo, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Search,
  CheckCircle2,
  Layers,
  Flag,
  ArrowRight,
  AlertCircle,
  RefreshCw,
  Plus,
  X,
  Loader2,
} from 'lucide-react';
import * as Dialog from '@radix-ui/react-dialog';
import { apiFetch, ApiError } from '@/api/client';
import { ProjectsListSchema, type Project } from '@/api/schemas';
import { PageHeader } from '@/components/PageHeader';

export const ProjectsSection: React.FC = () => {
  const [searchTerm, setSearchTerm] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');

  // New Project Dialog State
  const [dialogOpen, setDialogOpen] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [projectDescription, setProjectDescription] = useState('');
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchTerm);
    }, 200);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  const {
    data: projects,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: () => apiFetch<Project[]>('/api/v1/projects', { schema: ProjectsListSchema }),
    staleTime: 30_000,
  });

  const filteredProjects = useMemo(() => {
    if (!projects) return [];
    if (!debouncedSearch.trim()) return projects;
    const q = debouncedSearch.toLowerCase();
    return projects.filter(
      (p) => p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q)
    );
  }, [projects, debouncedSearch]);

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = projectName.trim();
    if (!trimmedName || trimmedName.length > 80) return;

    setCreateSubmitting(true);
    setCreateError(null);

    try {
      await apiFetch<Project>('/api/v1/projects', {
        method: 'POST',
        body: JSON.stringify({
          name: trimmedName,
          description: projectDescription.trim() || null,
        }),
      });
      // If server ever returns success in future
      setDialogOpen(false);
      setProjectName('');
      setProjectDescription('');
      refetch();
    } catch (err: unknown) {
      if (
        err instanceof ApiError &&
        (err.status === 501 || err.status === 404 || err.status === 405)
      ) {
        setCreateError('Project creation is not available yet (backend integration pending)');
      } else {
        setCreateError('Project creation is not available yet (backend integration pending)');
      }
    } finally {
      setCreateSubmitting(false);
    }
  };

  return (
    <section className="space-y-8" aria-labelledby="projects-heading">
      {/* 1. Page Header with gradient title */}
      <PageHeader
        title="Data Migration Projects"
        subtitle="Manage your data migration and validation projects"
      />

      {/* 2. All Projects Heading + "+ New Project" Button + Search Box */}
      <div className="space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="flex items-center space-x-3">
            <h2 id="projects-heading" className="text-xl font-bold text-[#111827]">
              All Projects
            </h2>

            {/* Gradient "+ New Project" Button */}
            <Dialog.Root open={dialogOpen} onOpenChange={setDialogOpen}>
              <Dialog.Trigger asChild>
                <button
                  type="button"
                  className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs sm:text-sm font-semibold text-white gradient-brand hover:opacity-90 shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0] transition-opacity"
                  aria-label="Create new project"
                >
                  <Plus className="w-4 h-4" aria-hidden="true" />
                  <span>New Project</span>
                </button>
              </Dialog.Trigger>

              <Dialog.Portal>
                <Dialog.Overlay className="fixed inset-0 bg-black/40 z-50 animate-in fade-in" />
                <Dialog.Content
                  className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md bg-white rounded-xl p-6 shadow-xl border border-[#E5E7EB] z-50 focus:outline-none animate-in zoom-in-95"
                  aria-describedby="new-project-desc"
                >
                  <div className="flex items-center justify-between border-b border-[#E5E7EB] pb-3 mb-4">
                    <Dialog.Title className="text-lg font-bold text-[#111827]">
                      New Project
                    </Dialog.Title>
                    <Dialog.Close asChild>
                      <button
                        type="button"
                        className="p-1 rounded-md text-gray-400 hover:text-gray-600 focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
                        aria-label="Close dialog"
                      >
                        <X className="w-5 h-5" />
                      </button>
                    </Dialog.Close>
                  </div>

                  <p id="new-project-desc" className="text-xs text-[#6B7280] mb-4">
                    Create a new enterprise migration project to track data validations.
                  </p>

                  <form onSubmit={handleCreateProject} className="space-y-4">
                    <div>
                      <label
                        htmlFor="project-name"
                        className="block text-xs font-semibold text-[#111827] mb-1"
                      >
                        Project Name <span className="text-red-500">*</span>
                      </label>
                      <input
                        id="project-name"
                        type="text"
                        required
                        maxLength={80}
                        value={projectName}
                        onChange={(e) => setProjectName(e.target.value)}
                        placeholder="e.g. S/4HANA Finance Migration"
                        className="w-full px-3 py-2 text-sm bg-white border border-[#E5E7EB] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#6D3FE0]"
                      />
                      <span className="text-[11px] text-[#9CA3AF] mt-0.5 block text-right">
                        {projectName.length}/80
                      </span>
                    </div>

                    <div>
                      <label
                        htmlFor="project-desc"
                        className="block text-xs font-semibold text-[#111827] mb-1"
                      >
                        Description (optional)
                      </label>
                      <textarea
                        id="project-desc"
                        rows={3}
                        maxLength={300}
                        value={projectDescription}
                        onChange={(e) => setProjectDescription(e.target.value)}
                        placeholder="Project migration waves and target SAP modules..."
                        className="w-full px-3 py-2 text-sm bg-white border border-[#E5E7EB] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#6D3FE0]"
                      />
                      <span className="text-[11px] text-[#9CA3AF] mt-0.5 block text-right">
                        {projectDescription.length}/300
                      </span>
                    </div>

                    {createError && (
                      <div
                        role="alert"
                        className="p-3 bg-amber-50 border border-amber-200 text-amber-900 rounded-lg text-xs font-medium flex items-start space-x-2"
                      >
                        <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
                        <span>{createError}</span>
                      </div>
                    )}

                    <div className="flex items-center justify-end space-x-3 pt-3 border-t border-[#E5E7EB]">
                      <Dialog.Close asChild>
                        <button
                          type="button"
                          className="px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-100 rounded-lg"
                        >
                          Cancel
                        </button>
                      </Dialog.Close>
                      <button
                        type="submit"
                        disabled={createSubmitting || !projectName.trim()}
                        className="inline-flex items-center space-x-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-white gradient-brand hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
                      >
                        {createSubmitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                        <span>Create Project</span>
                      </button>
                    </div>
                  </form>
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
          </div>

          {/* Search Input on the Right */}
          <div className="relative max-w-xs w-full">
            <label htmlFor="project-search" className="sr-only">
              Search projects by name or description
            </label>
            <Search className="w-4 h-4 text-[#9CA3AF] absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              id="project-search"
              type="search"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search projects..."
              className="w-full pl-9 pr-4 py-2 text-sm bg-white border border-[#E5E7EB] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#6D3FE0] placeholder-[#9CA3AF]"
            />
          </div>
        </div>

        {/* Loading Skeletons */}
        {isLoading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6" aria-busy="true">
            {[1, 2, 3].map((n) => (
              <div key={n} className="card-base p-6 space-y-4 animate-pulse">
                <div className="flex justify-between items-start">
                  <div className="w-3/4 h-5 bg-gray-200 rounded" />
                  <div className="w-6 h-6 bg-gray-200 rounded-full" />
                </div>
                <div className="space-y-2">
                  <div className="w-full h-4 bg-gray-200 rounded" />
                  <div className="w-2/3 h-4 bg-gray-200 rounded" />
                </div>
                <div className="flex items-center justify-between pt-4 border-t border-[#E5E7EB]">
                  <div className="w-20 h-4 bg-gray-200 rounded" />
                  <div className="w-24 h-8 bg-gray-200 rounded" />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Error State */}
        {isError && (
          <div className="card-base p-6 bg-red-50 border-red-200 text-red-900 flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <AlertCircle className="w-6 h-6 text-red-600 flex-shrink-0" />
              <div>
                <h3 className="font-semibold text-sm">Failed to load projects</h3>
                <p className="text-xs text-red-700 mt-0.5">
                  {error instanceof Error ? error.message : 'Please check connection.'}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => refetch()}
              className="px-3 py-1.5 bg-red-600 text-white rounded-md text-xs font-semibold hover:bg-red-700 flex items-center space-x-1"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Retry</span>
            </button>
          </div>
        )}

        {/* Empty State when zero projects returned from backend */}
        {!isLoading && !isError && projects?.length === 0 && (
          <div className="card-base p-10 text-center text-[#6B7280]">
            <p className="text-base font-semibold text-[#111827]">
              No projects yet - Click on New project to add
            </p>
          </div>
        )}

        {/* Empty State when search filter matches nothing */}
        {!isLoading &&
          !isError &&
          projects &&
          projects.length > 0 &&
          filteredProjects.length === 0 && (
            <div className="card-base p-10 text-center text-[#6B7280]">
              <p className="text-base font-semibold text-[#111827]">
                No projects match your filter
              </p>
              <p className="text-sm mt-1">
                Try searching for a different keyword or view all projects.
              </p>
            </div>
          )}

        {/* Projects Grid (No star toggle) */}
        {!isLoading && !isError && filteredProjects.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredProjects.map((project) => (
              <div
                key={project.id}
                className="card-base p-6 flex flex-col justify-between hover:border-[#6D3FE0]/50 transition-all duration-200"
              >
                <div className="space-y-3">
                  {/* Header: Title, Active pill, Check tick */}
                  <div className="space-y-1">
                    <h3 className="font-bold text-base text-[#111827] line-clamp-1">
                      {project.name}
                    </h3>
                    <div className="flex items-center space-x-2">
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold bg-[#6D4AE8] text-white">
                        {project.status === 'active' ? 'active' : 'archived'}
                      </span>
                      <span
                        className="w-5 h-5 rounded-full bg-green-100 flex items-center justify-center text-[#22C55E]"
                        title="Validated project"
                      >
                        <CheckCircle2 className="w-3.5 h-3.5" />
                      </span>
                    </div>
                  </div>

                  {/* Description */}
                  <p className="text-xs sm:text-sm text-[#6B7280] line-clamp-2">
                    {project.description}
                  </p>
                </div>

                {/* Footer: Releases, Milestones, View Project Button */}
                <div className="pt-4 mt-4 border-t border-[#E5E7EB] flex items-center justify-between">
                  <div className="flex items-center space-x-3 text-xs text-[#6B7280]">
                    <span className="flex items-center space-x-1" title="Releases configured">
                      <Layers className="w-3.5 h-3.5 text-[#9CA3AF]" />
                      <span>{project.release_count} releases</span>
                    </span>
                    <span className="flex items-center space-x-1" title="Milestones configured">
                      <Flag className="w-3.5 h-3.5 text-[#9CA3AF]" />
                      <span>{project.milestone_count} milestones</span>
                    </span>
                  </div>

                  <Link
                    to={`/projects/${project.id}`}
                    className="inline-flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-white gradient-brand hover:opacity-90 shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
                  >
                    <span>View Project</span>
                    <ArrowRight className="w-3 h-3" />
                  </Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

export default ProjectsSection;
