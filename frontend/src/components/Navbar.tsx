import React, { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Home,
  ChevronRight,
  ChevronDown,
  Menu,
  X,
  Link2,
  RefreshCw,
  Sliders,
  Shield,
} from 'lucide-react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { apiFetch } from '@/api/client';
import { PrincipalSchema, type Principal } from '@/api/schemas';
import { cn } from '@/lib/utils';

export const Navbar: React.FC = () => {
  const location = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // User Profile Query from GET /api/v1/me
  const {
    data: principal,
    isLoading: userLoading,
    isError: userError,
  } = useQuery<Principal>({
    queryKey: ['me'],
    queryFn: () => apiFetch<Principal>('/api/v1/me', { schema: PrincipalSchema }),
    staleTime: 60_000,
    retry: 1,
  });

  const getBreadcrumb = () => {
    const path = location.pathname;
    if (path === '/') {
      return (
        <span className="text-sm font-semibold text-[#6D3FE0] flex items-center">
          <ChevronRight className="w-4 h-4 text-[#9CA3AF] mr-1.5" aria-hidden="true" />
          Projects
        </span>
      );
    }
    if (path.startsWith('/configuration')) {
      let pageTitle = 'Configuration';
      if (path === '/configuration/skp-connections') pageTitle = 'SKP Connections';
      else if (path === '/configuration/refresh-metadata') pageTitle = 'Refresh Metadata';
      else if (path === '/configuration/llm-settings') pageTitle = 'LLM Settings';

      return (
        <span className="text-sm flex items-center">
          <ChevronRight className="w-4 h-4 text-[#9CA3AF] mr-1.5" aria-hidden="true" />
          <span className="text-[#6B7280]">Configuration</span>
          <ChevronRight className="w-4 h-4 text-[#9CA3AF] mx-1.5" aria-hidden="true" />
          <span className="font-semibold text-[#6D3FE0]">{pageTitle}</span>
        </span>
      );
    }
    if (path === '/admin') {
      return (
        <span className="text-sm font-semibold text-[#6D3FE0] flex items-center">
          <ChevronRight className="w-4 h-4 text-[#9CA3AF] mr-1.5" aria-hidden="true" />
          Admin
        </span>
      );
    }
    if (path === '/results') {
      return (
        <span className="text-sm flex items-center">
          <ChevronRight className="w-4 h-4 text-[#9CA3AF] mr-1.5" aria-hidden="true" />
          <span className="text-[#6B7280]">Validations</span>
          <ChevronRight className="w-4 h-4 text-[#9CA3AF] mx-1.5" aria-hidden="true" />
          <span className="font-semibold text-[#6D3FE0]">Analysis Results</span>
        </span>
      );
    }
    return (
      <span className="text-sm font-semibold text-[#6D3FE0] flex items-center">
        <ChevronRight className="w-4 h-4 text-[#9CA3AF] mr-1.5" aria-hidden="true" />
        Details
      </span>
    );
  };

  const isConfigActive = location.pathname.startsWith('/configuration');

  return (
    <header className="sticky top-0 z-40 w-full bg-white border-b border-[#E5E7EB]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Left: Home Icon + Brand Title + Breadcrumbs */}
          <div className="flex items-center space-x-2 min-w-0">
            <Link
              to="/"
              className="flex items-center space-x-2.5 text-[#111827] hover:opacity-90 transition-opacity focus-visible:ring-2 focus-visible:ring-[#6D3FE0] rounded-md p-1"
              aria-label="Syniti Governance Tool Home"
            >
              <Home className="w-5 h-5 text-[#6B7280] flex-shrink-0" aria-hidden="true" />
              <span className="text-base sm:text-lg font-extrabold tracking-tight text-[#6D3FE0] bg-gradient-to-r from-[#6D3FE0] to-[#4F46E5] bg-clip-text text-transparent forced-colors:text-[LinkText] whitespace-nowrap">
                Syniti Governance Tool
              </span>
            </Link>
            <div className="hidden sm:flex items-center min-w-0" aria-label="Breadcrumb">
              {getBreadcrumb()}
            </div>
          </div>

          {/* Desktop Right Navigation */}
          <nav className="hidden md:flex items-center space-x-6" aria-label="Main Navigation">
            {/* Validations Link (Home) */}
            <Link
              to="/"
              className={cn(
                'text-sm font-medium transition-colors py-5 border-b-2',
                location.pathname === '/'
                  ? 'border-[#6D3FE0] text-[#6D3FE0]'
                  : 'border-transparent text-[#6B7280] hover:text-[#111827]'
              )}
              aria-current={location.pathname === '/' ? 'page' : undefined}
            >
              Validations
            </Link>

            {/* Configuration Dropdown */}
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button
                  className={cn(
                    'flex items-center space-x-1 text-sm font-medium py-2 px-1 rounded-md focus-visible:ring-2 focus-visible:ring-[#6D3FE0]',
                    isConfigActive
                      ? 'text-[#6D3FE0] font-semibold'
                      : 'text-[#6B7280] hover:text-[#111827]'
                  )}
                  aria-label="Configuration menu"
                  aria-haspopup="menu"
                >
                  <span>Configuration</span>
                  <ChevronDown className="w-4 h-4" aria-hidden="true" />
                </button>
              </DropdownMenu.Trigger>

              <DropdownMenu.Portal>
                <DropdownMenu.Content
                  className="min-w-[220px] bg-white rounded-xl p-1.5 shadow-lg border border-[#E5E7EB] z-50 text-sm focus:outline-none animate-in fade-in-80"
                  sideOffset={6}
                >
                  <DropdownMenu.Item asChild>
                    <Link
                      to="/configuration/skp-connections"
                      className={cn(
                        'flex items-center space-x-2.5 px-3 py-2 rounded-lg cursor-pointer outline-none transition-colors',
                        location.pathname === '/configuration/skp-connections'
                          ? 'bg-violet-50 text-[#6D3FE0] font-semibold'
                          : 'text-[#111827] hover:bg-[#F3F4F6] focus:bg-[#F3F4F6]'
                      )}
                    >
                      <Link2 className="w-4 h-4 text-[#6D3FE0]" aria-hidden="true" />
                      <span>SKP Connections</span>
                    </Link>
                  </DropdownMenu.Item>

                  <DropdownMenu.Item asChild>
                    <Link
                      to="/configuration/refresh-metadata"
                      className={cn(
                        'flex items-center space-x-2.5 px-3 py-2 rounded-lg cursor-pointer outline-none transition-colors',
                        location.pathname === '/configuration/refresh-metadata'
                          ? 'bg-violet-50 text-[#6D3FE0] font-semibold'
                          : 'text-[#111827] hover:bg-[#F3F4F6] focus:bg-[#F3F4F6]'
                      )}
                    >
                      <RefreshCw className="w-4 h-4 text-[#6D3FE0]" aria-hidden="true" />
                      <span>Refresh Metadata</span>
                    </Link>
                  </DropdownMenu.Item>

                  <DropdownMenu.Item asChild>
                    <Link
                      to="/configuration/llm-settings"
                      className={cn(
                        'flex items-center space-x-2.5 px-3 py-2 rounded-lg cursor-pointer outline-none transition-colors',
                        location.pathname === '/configuration/llm-settings'
                          ? 'bg-violet-50 text-[#6D3FE0] font-semibold'
                          : 'text-[#111827] hover:bg-[#F3F4F6] focus:bg-[#F3F4F6]'
                      )}
                    >
                      <Sliders className="w-4 h-4 text-[#6D3FE0]" aria-hidden="true" />
                      <span>LLM Settings</span>
                    </Link>
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>

            {/* Static Admin TAG: small pill, violet tint, not focusable, not a link */}
            <span
              className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[#6D3FE0]/10 text-[#6D3FE0] select-none pointer-events-none"
              tabIndex={-1}
              aria-hidden="true"
            >
              Admin
            </span>

            {/* User Profile Avatar & Menu */}
            {userLoading ? (
              <div className="flex items-center space-x-2 animate-pulse" aria-busy="true">
                <div className="w-8 h-8 rounded-full bg-gray-200" />
                <div className="hidden md:block w-20 h-4 bg-gray-200 rounded" />
              </div>
            ) : userError || !principal ? null : (
              <DropdownMenu.Root>
                <DropdownMenu.Trigger asChild>
                  <button
                    type="button"
                    className="flex items-center space-x-2 p-1 rounded-full hover:bg-gray-100 focus-visible:ring-2 focus-visible:ring-[#6D3FE0] outline-none"
                    aria-label={`User menu for ${principal.display_name}`}
                  >
                    <div
                      className="w-8 h-8 rounded-full bg-gradient-to-tr from-[#6D3FE0] to-[#3B82F6] text-white flex items-center justify-center text-xs font-bold shadow-xs select-none"
                      aria-hidden="true"
                    >
                      {principal.initials || 'JS'}
                    </div>
                    <span className="hidden md:inline font-medium text-sm text-[#111827] max-w-[140px] truncate">
                      {principal.display_name}
                    </span>
                    <ChevronDown
                      className="w-3.5 h-3.5 text-gray-500 hidden md:inline"
                      aria-hidden="true"
                    />
                  </button>
                </DropdownMenu.Trigger>

                <DropdownMenu.Portal>
                  <DropdownMenu.Content
                    className="min-w-[190px] bg-white rounded-xl p-1.5 shadow-lg border border-[#E5E7EB] z-50 text-sm focus:outline-none"
                    sideOffset={6}
                    align="end"
                  >
                    <div className="px-3 py-2 border-b border-[#E5E7EB] mb-1">
                      <p className="text-xs font-semibold text-[#111827] truncate">
                        {principal.display_name}
                      </p>
                      <p className="text-[11px] text-[#6B7280] truncate">{principal.email}</p>
                    </div>

                    <DropdownMenu.Item asChild>
                      <Link
                        to="/admin"
                        className="flex items-center space-x-2 px-3 py-2 text-[#111827] hover:bg-[#F3F4F6] focus:bg-[#F3F4F6] rounded-lg cursor-pointer outline-none transition-colors font-medium text-xs"
                      >
                        <Shield className="w-4 h-4 text-[#6D3FE0]" aria-hidden="true" />
                        <span>Admin console</span>
                      </Link>
                    </DropdownMenu.Item>
                  </DropdownMenu.Content>
                </DropdownMenu.Portal>
              </DropdownMenu.Root>
            )}
          </nav>

          {/* Mobile menu trigger button */}
          <div className="flex md:hidden items-center space-x-2">
            <span
              className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-[#6D3FE0]/10 text-[#6D3FE0] select-none"
              tabIndex={-1}
              aria-hidden="true"
            >
              Admin
            </span>
            <button
              type="button"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="p-2 rounded-md text-[#6B7280] hover:text-[#111827] hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-[#6D3FE0]"
              aria-label="Toggle navigation menu"
              aria-expanded={mobileMenuOpen}
            >
              {mobileMenuOpen ? (
                <X className="w-6 h-6" aria-hidden="true" />
              ) : (
                <Menu className="w-6 h-6" aria-hidden="true" />
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Mobile Drawer */}
      {mobileMenuOpen && (
        <div className="md:hidden border-t border-[#E5E7EB] bg-white px-4 pt-3 pb-6 space-y-3">
          <Link
            to="/"
            onClick={() => setMobileMenuOpen(false)}
            className={cn(
              'block px-3 py-2 rounded-md text-base font-medium',
              location.pathname === '/'
                ? 'bg-violet-50 text-[#6D3FE0]'
                : 'text-[#6B7280] hover:bg-gray-50'
            )}
          >
            Validations
          </Link>

          <div className="pt-2 border-t border-gray-100">
            <p className="px-3 text-xs font-semibold uppercase tracking-wider text-gray-400">
              Configuration
            </p>
            <div className="mt-1 space-y-1">
              <Link
                to="/configuration/skp-connections"
                onClick={() => setMobileMenuOpen(false)}
                className="flex items-center space-x-2 px-3 py-2 rounded-md text-sm text-[#111827] hover:bg-gray-50"
              >
                <Link2 className="w-4 h-4 text-[#6D3FE0]" />
                <span>SKP Connections</span>
              </Link>
              <Link
                to="/configuration/refresh-metadata"
                onClick={() => setMobileMenuOpen(false)}
                className="flex items-center space-x-2 px-3 py-2 rounded-md text-sm text-[#111827] hover:bg-gray-50"
              >
                <RefreshCw className="w-4 h-4 text-[#6D3FE0]" />
                <span>Refresh Metadata</span>
              </Link>
              <Link
                to="/configuration/llm-settings"
                onClick={() => setMobileMenuOpen(false)}
                className="flex items-center space-x-2 px-3 py-2 rounded-md text-sm text-[#111827] hover:bg-gray-50"
              >
                <Sliders className="w-4 h-4 text-[#6D3FE0]" />
                <span>LLM Settings</span>
              </Link>
            </div>
          </div>

          {principal && (
            <div className="pt-3 border-t border-gray-100 flex items-center justify-between px-3">
              <div className="flex items-center space-x-2">
                <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-[#6D3FE0] to-[#3B82F6] text-white flex items-center justify-center text-xs font-bold">
                  {principal.initials || 'JS'}
                </div>
                <div className="text-left">
                  <p className="text-sm font-semibold text-[#111827]">{principal.display_name}</p>
                  <p className="text-xs text-[#6B7280]">{principal.email}</p>
                </div>
              </div>
              <Link
                to="/admin"
                onClick={() => setMobileMenuOpen(false)}
                className="text-xs font-semibold text-[#6D3FE0] hover:underline"
              >
                Admin console
              </Link>
            </div>
          )}
        </div>
      )}
    </header>
  );
};

export default Navbar;
