import React, { useState, Suspense, useCallback, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  Sparkles,
  AlertTriangle,
  Play,
  RotateCcw,
  XCircle,
  CheckCircle2,
  Loader2,
  ShieldAlert,
  Info,
  ExternalLink,
} from 'lucide-react';
import { apiFetch } from '@/api/client';
import { LLMStatusResponseSchema, type LLMStatusResponse } from '@/api/schemas';
import { MAX_SQL_LENGTH } from '@/api/limits';
import { useAnalysis } from './useAnalysis';
import { ResultsPanel } from '@/features/results/ResultsPanel';
import { cn } from '@/lib/utils';

const SqlEditor = React.lazy(() => import('@/components/SqlEditor'));

export interface TrialSectionProps {
  initialSql?: string;
}

export const TrialSection: React.FC<TrialSectionProps> = ({ initialSql = '' }) => {
  const [sql, setSql] = useState(initialSql);
  const queryClient = useQueryClient();

  const {
    status,
    stages,
    result,
    error,
    announcement,
    liveProgressUnavailable,
    countdownSeconds,
    analyze,
    cancel,
    reset,
  } = useAnalysis();

  // Invalidate activity query when execution completes (success or error)
  useEffect(() => {
    if (status === 'success' || status === 'error') {
      queryClient.invalidateQueries({ queryKey: ['activity'] });
    }
  }, [status, queryClient]);

  // Store result in sessionStorage for dedicated /results page
  useEffect(() => {
    if (status === 'success' && result) {
      try {
        sessionStorage.setItem('latest_analysis_result', JSON.stringify(result));
      } catch {
        // Ignore session storage errors
      }
    }
  }, [status, result]);

  // LLM Status query: poll every 30s and on window focus
  const { data: llmStatus } = useQuery<LLMStatusResponse>({
    queryKey: ['llm-status-trial'],
    queryFn: () =>
      apiFetch<LLMStatusResponse>('/api/v1/llm/status', {
        schema: LLMStatusResponseSchema,
      }),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });

  // Evaluate LLM readiness
  const isLlmUnavailable = React.useMemo(() => {
    if (!llmStatus) return false;
    if (!llmStatus.configured) return true;
    if (llmStatus.circuit_state === 'OPEN') return true;
    if (
      llmStatus.last_error_code &&
      (llmStatus.last_error_code.includes('QUOTA') || llmStatus.last_error_code.includes('AUTH'))
    ) {
      return true;
    }
    return false;
  }, [llmStatus]);

  const llmUnavailableReason = React.useMemo(() => {
    if (!llmStatus) return null;
    if (!llmStatus.configured) {
      return 'AI service is not configured. The server has no valid API credentials.';
    }
    if (llmStatus.circuit_state === 'OPEN') {
      return 'AI circuit breaker is currently OPEN due to consecutive upstream failures.';
    }
    if (llmStatus.last_error_code) {
      return `AI provider issue detected: ${llmStatus.last_error_code}. Analysis requests are currently paused.`;
    }
    return null;
  }, [llmStatus]);

  const charCount = sql.length;
  const isOverLimit = charCount > MAX_SQL_LENGTH;
  const isEmpty = sql.trim().length === 0;
  const isRunning = status === 'running';

  const canExecute = !isEmpty && !isOverLimit && !isLlmUnavailable && !isRunning;

  const handleExecute = useCallback(() => {
    if (!canExecute) return;
    analyze(sql);
  }, [canExecute, analyze, sql]);

  // Keyboard shortcut Ctrl/Cmd + Enter
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        if (canExecute) {
          e.preventDefault();
          handleExecute();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [canExecute, handleExecute]);

  return (
    <section className="space-y-6" aria-labelledby="trial-heading">
      {/* Live polite screen reader announcement */}
      <div className="sr-only" role="status" aria-live="polite">
        {announcement}
      </div>

      <div className="card-base p-6 space-y-4">
        <div className="border-b border-[#E5E7EB] pb-4">
          <h2
            id="trial-heading"
            className="text-xl font-bold text-[#111827] flex items-center space-x-2"
          >
            <Sparkles className="w-5 h-5 text-[#6D3FE0]" />
            <span>Trial Analysis</span>
          </h2>
          <p className="text-xs sm:text-sm text-[#6B7280] mt-0.5">
            Paste an MS SQL query over SAP tables. The analysis is produced by AI agents on the
            server.
          </p>
        </div>

        {/* Amber Status Banner when LLM is unavailable */}
        {isLlmUnavailable && (
          <div
            className="p-4 bg-amber-50 border border-amber-200 rounded-lg text-amber-900 text-sm flex items-start space-x-3"
            role="alert"
          >
            <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-amber-950">AI Subsystem Offline</p>
              <p className="text-xs text-amber-800 mt-0.5">{llmUnavailableReason}</p>
            </div>
          </div>
        )}

        {/* Live Progress Fallback Notice */}
        {liveProgressUnavailable && (
          <div
            className="p-3 bg-blue-50 border border-blue-200 rounded-lg text-blue-900 text-xs flex items-center space-x-2"
            role="status"
          >
            <Info className="w-4 h-4 text-blue-600 flex-shrink-0" />
            <span>Live progress unavailable. Analysis running in synchronous fallback mode.</span>
          </div>
        )}

        {/* CodeMirror SQL Editor (Lazy-Loaded) */}
        <div className="relative">
          <Suspense
            fallback={
              <div className="w-full h-[260px] bg-gray-50 border border-[#E5E7EB] rounded-lg animate-pulse flex items-center justify-center text-xs text-gray-400 font-mono">
                Loading SQL Editor...
              </div>
            }
          >
            <SqlEditor
              value={sql}
              onChange={setSql}
              readOnly={isRunning}
              minHeight="260px"
              errorLine={error?.parseLocation?.line}
              placeholder="Paste an MS SQL query over SAP tables here (e.g. SELECT MARC.MATNR, MARA.MTART FROM dbo.MM_MARC_ECC_EU_R1_MARC_S MARC...)"
            />
          </Suspense>

          {/* Over-Limit Inline Error under editor */}
          {isOverLimit && (
            <div
              role="alert"
              className="mt-2 p-2.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-800 font-medium flex items-center space-x-2"
            >
              <AlertTriangle className="w-4 h-4 text-red-600 flex-shrink-0" />
              <span>
                Query exceeds maximum allowed length of {MAX_SQL_LENGTH.toLocaleString()}{' '}
                characters.
              </span>
            </div>
          )}

          {/* Parse error line marker banner if syntax error */}
          {error?.parseLocation && (
            <div className="mt-2 p-2 bg-red-50 border border-red-200 rounded text-xs text-red-800 flex items-center space-x-2">
              <AlertTriangle className="w-4 h-4 text-red-600 flex-shrink-0" />
              <span>
                Syntax parse issue at line {error.parseLocation.line}
                {error.parseLocation.col ? `, column ${error.parseLocation.col}` : ''}
              </span>
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
          <div className="flex items-center space-x-3">
            {/* Execute Button (formerly Analyze) */}
            <button
              type="button"
              onClick={handleExecute}
              disabled={!canExecute}
              className={cn(
                'inline-flex items-center space-x-2 px-5 py-2.5 rounded-lg text-sm font-semibold text-white transition-all shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0]',
                canExecute
                  ? 'gradient-brand hover:opacity-90 active:scale-[0.99]'
                  : 'bg-gray-300 cursor-not-allowed text-gray-500'
              )}
              aria-label="Execute query analysis"
            >
              {isRunning ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Executing...</span>
                </>
              ) : (
                <>
                  <Play className="w-4 h-4 fill-white" />
                  <span>Execute</span>
                </>
              )}
            </button>

            {/* Cancel Button */}
            {isRunning && (
              <button
                type="button"
                onClick={cancel}
                className="px-4 py-2 border border-red-300 text-red-700 bg-red-50 hover:bg-red-100 rounded-lg text-sm font-semibold focus-visible:ring-2 focus-visible:ring-red-500"
              >
                Cancel
              </button>
            )}

            {/* Clear Button */}
            <button
              type="button"
              onClick={() => {
                setSql('');
                reset();
              }}
              disabled={isRunning || (isEmpty && !result && !error)}
              className="px-4 py-2 border border-[#E5E7EB] text-[#4B5563] hover:bg-gray-50 rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
            >
              Clear
            </button>
          </div>

          <div className="text-xs text-[#9CA3AF] hidden sm:block">
            Press{' '}
            <kbd className="px-1.5 py-0.5 bg-gray-100 border border-gray-300 rounded text-[11px] font-sans">
              Ctrl
            </kbd>{' '}
            +{' '}
            <kbd className="px-1.5 py-0.5 bg-gray-100 border border-gray-300 rounded text-[11px] font-sans">
              Enter
            </kbd>{' '}
            to execute
          </div>
        </div>

        {/* Progress Stepper while running or completed */}
        {(isRunning || (status === 'success' && stages[0]?.status === 'completed')) && (
          <div className="pt-4 border-t border-[#E5E7EB] space-y-3">
            <div className="text-xs font-semibold text-[#111827]">Analysis Pipeline Stages</div>
            <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-2">
              {stages.map((stage) => {
                const isPending = stage.status === 'pending';
                const isStageRunning = stage.status === 'running';
                const isCompleted = stage.status === 'completed';

                return (
                  <div
                    key={stage.id}
                    className={cn(
                      'p-2.5 rounded-lg border text-xs flex flex-col justify-between transition-colors',
                      isCompleted && 'bg-green-50/60 border-green-200 text-green-900',
                      isStageRunning &&
                        'bg-purple-50 border-[#6D3FE0] text-[#6D3FE0] font-semibold ring-1 ring-[#6D3FE0]',
                      isPending && 'bg-gray-50 border-gray-200 text-gray-400'
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="truncate">{stage.label}</span>
                      {isCompleted && (
                        <CheckCircle2 className="w-3.5 h-3.5 text-[#22C55E] flex-shrink-0" />
                      )}
                      {isStageRunning && (
                        <Loader2 className="w-3.5 h-3.5 text-[#6D3FE0] animate-spin flex-shrink-0" />
                      )}
                    </div>
                    {stage.elapsedMs != null && stage.elapsedMs > 0 && (
                      <span className="text-[10px] text-gray-500 mt-1">{stage.elapsedMs}ms</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Error Banner when failed */}
        {status === 'error' && error && (
          <div
            className={cn(
              'p-4 rounded-xl border space-y-3',
              error.bannerColor === 'red' && 'bg-red-50 border-red-200',
              error.bannerColor === 'amber' && 'bg-amber-50 border-amber-200',
              error.bannerColor === 'blue' && 'bg-blue-50 border-blue-200'
            )}
            role="alert"
          >
            <div className="flex items-start justify-between">
              <div className="flex items-start space-x-3">
                {error.bannerColor === 'red' && (
                  <XCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                )}
                {error.bannerColor === 'amber' && (
                  <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
                )}
                {error.bannerColor === 'blue' && (
                  <ShieldAlert className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
                )}

                <div>
                  <h3 className="text-sm font-bold text-gray-900">{error.title}</h3>
                  <p className="text-xs text-gray-700 mt-0.5">{error.message}</p>
                </div>
              </div>

              {/* Error Code & Request ID chips */}
              <div className="flex flex-col items-end space-y-1">
                <span className="text-[10px] font-mono px-2 py-0.5 bg-white/80 rounded border border-gray-200 text-gray-600">
                  {error.code}
                </span>
                <span className="text-[10px] font-mono text-gray-500">Req: {error.requestId}</span>
              </div>
            </div>

            {/* Error Details */}
            {error.details.length > 0 && (
              <div className="p-3 bg-white/70 rounded-lg border border-red-200 text-xs text-red-900 space-y-1">
                <span className="font-semibold text-red-950">Diagnostic Details:</span>
                <ul className="list-disc pl-5 space-y-0.5">
                  {error.details.map((d, i) => (
                    <li key={i}>
                      {d.field ? <span className="font-semibold">{d.field}: </span> : null}
                      <span className="whitespace-pre-wrap">{d.issue}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Retry Button / Countdown */}
            {error.isRetryable && (
              <div className="pt-2 flex items-center space-x-4">
                <button
                  type="button"
                  onClick={handleExecute}
                  disabled={Boolean(error.hasCountdown && countdownSeconds && countdownSeconds > 0)}
                  className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-gray-300 disabled:cursor-not-allowed text-white rounded-lg text-xs font-semibold flex items-center space-x-1.5 focus-visible:ring-2 focus-visible:ring-red-500"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  <span>
                    {countdownSeconds && countdownSeconds > 0
                      ? `Retry in ${countdownSeconds}s`
                      : 'Retry Execution'}
                  </span>
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Served From Cache Notice */}
      {status === 'success' && result?.meta.cache_status === 'hit_final' && (
        <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-emerald-900 text-xs font-semibold flex items-center space-x-2 shadow-xs">
          <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0" />
          <span>Served from cache. No AI calls were made.</span>
        </div>
      )}

      {/* Results Presentation (Embedded ResultsPanel with separate page button) */}
      {status === 'success' && result && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-bold text-[#111827]">Executive Analysis Results</h3>
            <Link
              to="/results"
              className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-[#6D3FE0] bg-violet-50 hover:bg-violet-100 border border-violet-200 shadow-xs transition-colors"
            >
              <span>Open in Separate Page</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </Link>
          </div>
          <ResultsPanel result={result} />
        </div>
      )}
    </section>
  );
};

export default TrialSection;
