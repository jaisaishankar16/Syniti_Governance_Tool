import React, { Suspense } from 'react';
import { Routes, Route } from 'react-router-dom';
import { Toaster } from 'sonner';
import { Navbar } from '@/components/Navbar';
import { HomePage } from './HomePage';
import { ScopeNotice } from '@/components/ScopeNotice';
import { NotFound } from '@/components/NotFound';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { SKPConnectionsPage } from '@/features/configuration/SKPConnectionsPage';
import { RefreshMetadataPage } from '@/features/configuration/RefreshMetadataPage';
import { LlmSettingsPage } from '@/features/configuration/LlmSettingsPage';
import { ResultsPage } from '@/features/results/ResultsPage';

const AdminPage = React.lazy(() => import('@/features/admin/AdminPage'));

export const App: React.FC = () => {
  return (
    <ErrorBoundary>
      {/* Skip to Content accessible navigation */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:px-4 focus:py-2 focus:bg-[#6D3FE0] focus:text-white focus:rounded-md focus:shadow-lg focus:outline-none"
      >
        Skip to main content
      </a>

      <div className="min-h-screen flex flex-col bg-[#F8F9FB] text-[#111827]">
        {/* Sticky Navbar */}
        <Navbar />

        {/* Main Content Area */}
        <main
          id="main-content"
          className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8"
        >
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/configuration/skp-connections" element={<SKPConnectionsPage />} />
            <Route path="/configuration/refresh-metadata" element={<RefreshMetadataPage />} />
            <Route path="/configuration/llm-settings" element={<LlmSettingsPage />} />
            <Route path="/results" element={<ResultsPage />} />
            <Route
              path="/admin"
              element={
                <Suspense
                  fallback={
                    <div className="py-20 text-center text-sm text-[#6B7280] animate-pulse">
                      Loading Administration Console...
                    </div>
                  }
                >
                  <AdminPage />
                </Suspense>
              }
            />
            <Route path="/projects/:id" element={<ScopeNotice />} />
            <Route path="/activity/:id" element={<ScopeNotice />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>

      {/* Toast Notifications */}
      <Toaster position="bottom-right" richColors />
    </ErrorBoundary>
  );
};

export default App;
