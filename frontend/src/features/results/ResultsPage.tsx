import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Sparkles } from 'lucide-react';
import { PageHeader } from '@/components/PageHeader';
import { ResultsPanel } from './ResultsPanel';
import { BusinessIntelligenceSchema, type BusinessIntelligence } from '@/api/schemas';

export const ResultsPage: React.FC = () => {
  const [result, setResult] = useState<BusinessIntelligence | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      const stored = sessionStorage.getItem('latest_analysis_result');
      if (stored) {
        const raw = JSON.parse(stored);
        const parsed = BusinessIntelligenceSchema.safeParse(raw);
        if (parsed.success) {
          setResult(parsed.data);
        } else {
          setError('Stored analysis result failed schema validation.');
        }
      }
    } catch {
      setError('Could not read analysis results from storage.');
    }
  }, []);

  return (
    <div className="space-y-6 pb-16">
      {/* Back link */}
      <div>
        <Link
          to="/"
          className="inline-flex items-center space-x-1.5 text-xs sm:text-sm font-semibold text-[#6D3FE0] hover:text-[#5B2FD0] transition-colors focus-visible:ring-2 focus-visible:ring-[#6D3FE0] rounded-md p-1"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Trial Analysis</span>
        </Link>
      </div>

      {/* Header */}
      <PageHeader
        title="Analysis Results"
        subtitle="Authoritative Business Intelligence and SAP Data Governance Report"
      />

      {/* Content */}
      {result ? (
        <ResultsPanel result={result} />
      ) : (
        <div className="card-base p-10 text-center space-y-4">
          <div className="w-12 h-12 rounded-full bg-violet-50 text-[#6D3FE0] flex items-center justify-center mx-auto">
            <Sparkles className="w-6 h-6" />
          </div>
          <div className="space-y-1">
            <h3 className="text-base font-bold text-[#111827]">No Analysis Executed Yet</h3>
            <p className="text-xs sm:text-sm text-[#6B7280] max-w-md mx-auto">
              {error ||
                'Run an MS SQL analysis on the Trial page to generate and view full Business Intelligence results.'}
            </p>
          </div>
          <div>
            <Link
              to="/"
              className="inline-flex items-center space-x-2 px-4 py-2 rounded-lg text-xs font-semibold text-white gradient-brand hover:opacity-90 shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
            >
              <span>Go to Trial Section</span>
            </Link>
          </div>
        </div>
      )}
    </div>
  );
};

export default ResultsPage;
