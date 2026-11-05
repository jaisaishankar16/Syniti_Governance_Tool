import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  RefreshCw,
  XCircle,
  Copy,
  Download,
  Trash2,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Loader2,
  ExternalLink,
} from 'lucide-react';
import { PageHeader } from '@/components/PageHeader';
import { skpConnectionStore } from './skpConnectionStore';
import { parseSSEStream } from '@/api/sse';
import { getApiBaseUrl, ApiError } from '@/api/client';
import { cn } from '@/lib/utils';

interface LogLine {
  id: string;
  ts: string;
  level: 'info' | 'warn' | 'error';
  stage: string;
  message: string;
}

interface ProgressCount {
  label: string;
  value: number;
}

type RefreshStatus = 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';

const MAX_LOG_LINES = 5000;

export const RefreshMetadataPage: React.FC = () => {
  const [skpUrl, setSkpUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<RefreshStatus>('idle');
  const [currentStage, setCurrentStage] = useState<string>('Idle');
  const [progressPercent, setProgressPercent] = useState<number | null>(null);
  const [counts, setCounts] = useState<ProgressCount[]>([]);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [pendingError, setPendingError] = useState<string | null>(null);

  // Terminal state
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [droppedCount, setDroppedCount] = useState(0);
  const [autoScroll, setAutoScroll] = useState(true);
  const [isScrolledUp, setIsScrolledUp] = useState(false);

  const terminalRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    setSkpUrl(skpConnectionStore.getSkpUrl());
  }, []);

  // Elapsed timer
  useEffect(() => {
    if (status === 'running') {
      const startTime = Date.now();
      timerRef.current = setInterval(() => {
        setElapsedSeconds(Math.floor((Date.now() - startTime) / 1000));
      }, 1000);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [status]);

  // Terminal auto-scroll behavior
  const scrollToBottom = useCallback(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
      setIsScrolledUp(false);
    }
  }, []);

  useEffect(() => {
    if (autoScroll && !isScrolledUp) {
      scrollToBottom();
    }
  }, [logs, autoScroll, isScrolledUp, scrollToBottom]);

  const handleTerminalScroll = () => {
    if (!terminalRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = terminalRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 30;
    setIsScrolledUp(!isAtBottom);
  };

  const appendLog = useCallback((line: LogLine) => {
    setLogs((prev) => {
      if (prev.length >= MAX_LOG_LINES) {
        setDroppedCount((c) => c + 1);
        return [...prev.slice(1), line];
      }
      return [...prev, line];
    });
  }, []);

  const handleStartRefresh = async () => {
    if (!skpUrl || status === 'running') return;

    setStatus('running');
    setCurrentStage('Connecting');
    setProgressPercent(null);
    setCounts([]);
    setElapsedSeconds(0);
    setPendingError(null);
    setLogs([]);
    setDroppedCount(0);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const baseUrl = getApiBaseUrl();
      const url = `${baseUrl}/api/v1/metadata/refresh/stream`;

      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({ skp_url: skpUrl }),
        signal: controller.signal,
      });

      if (!response.ok) {
        if (response.status === 501 || response.status === 404) {
          setPendingError(
            'Metadata refresh is not available on this server yet (backend integration pending)'
          );
          setStatus('failed');
          return;
        }
        let msg = `HTTP error ${response.status}`;
        try {
          const errBody = await response.json();
          if (errBody?.error?.message) msg = errBody.error.message;
        } catch {
          // ignore
        }
        setPendingError(msg);
        setStatus('failed');
        return;
      }

      if (!response.body) {
        throw new Error('Response body stream is not available');
      }

      for await (const sse of parseSSEStream(response.body, { signal: controller.signal })) {
        if (sse.type === 'progress') {
          if (sse.data.stage) setCurrentStage(sse.data.stage);
          if (typeof sse.data.detail?.percent === 'number') {
            setProgressPercent(sse.data.detail.percent as number);
          }
          if (Array.isArray(sse.data.detail?.counts)) {
            setCounts(sse.data.detail.counts as ProgressCount[]);
          }
          appendLog({
            id: `log-${Date.now()}-${Math.random()}`,
            ts: new Date().toISOString(),
            level: 'info',
            stage: sse.data.stage,
            message: sse.data.message || `Stage: ${sse.data.stage}`,
          });
        } else if (sse.type === 'result') {
          appendLog({
            id: `log-${Date.now()}-${Math.random()}`,
            ts: new Date().toISOString(),
            level: 'info',
            stage: 'result',
            message: 'Metadata refresh completed successfully',
          });
        } else if (sse.type === 'error') {
          setStatus('failed');
          setPendingError(sse.data.error.message || 'Stream error occurred');
          appendLog({
            id: `log-${Date.now()}-${Math.random()}`,
            ts: new Date().toISOString(),
            level: 'error',
            stage: 'error',
            message: sse.data.error.message || 'Failed',
          });
        } else if (sse.type === 'done') {
          setStatus('completed');
          setCurrentStage('Done');
          setProgressPercent(100);
        }
      }

      setStatus('completed');
      setCurrentStage('Completed');
    } catch (err: unknown) {
      if (controller.signal.aborted) {
        setStatus('cancelled');
        setCurrentStage('Cancelled');
      } else {
        setStatus('failed');
        setCurrentStage('Failed');
        if (err instanceof ApiError && (err.status === 501 || err.status === 404)) {
          setPendingError(
            'Metadata refresh is not available on this server yet (backend integration pending)'
          );
        } else {
          setPendingError(
            err instanceof Error
              ? err.message
              : 'Metadata refresh is not available on this server yet (backend integration pending)'
          );
        }
      }
    } finally {
      abortControllerRef.current = null;
    }
  };

  const handleCancel = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setStatus('cancelled');
    setCurrentStage('Cancelled');
  };

  const handleCopyLogs = async () => {
    const text = logs
      .map((l) => `[${l.ts}] [${l.level.toUpperCase()}] [${l.stage}] ${l.message}`)
      .join('\n');
    await navigator.clipboard.writeText(text);
  };

  const handleDownloadLogs = () => {
    const text = logs
      .map((l) => `[${l.ts}] [${l.level.toUpperCase()}] [${l.stage}] ${l.message}`)
      .join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `metadata-refresh-${new Date().toISOString().replace(/[:.]/g, '-')}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleClearLogs = () => {
    setLogs([]);
    setDroppedCount(0);
  };

  return (
    <div className="space-y-6 pb-16">
      <PageHeader
        title="Refresh Metadata"
        subtitle="Extract catalog schemas and update vector knowledge indexes from the SKP service"
      />

      {/* Target SKP Endpoint Card */}
      <div className="card-base p-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <span className="text-xs font-bold uppercase tracking-wider text-[#6B7280]">
            Target SKP Connection
          </span>
          {skpUrl ? (
            <p className="text-sm font-semibold text-[#111827] font-mono mt-0.5 break-all">
              {skpUrl}
            </p>
          ) : (
            <div className="flex items-center space-x-2 mt-1 text-xs text-amber-800">
              <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0" />
              <span>No SKP URL configured. Configure a connection to refresh metadata.</span>
            </div>
          )}
        </div>

        <div>
          <Link
            to="/configuration/skp-connections"
            className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-[#6D3FE0] bg-violet-50 hover:bg-violet-100 border border-violet-200 transition-colors"
          >
            <span>Change Connection</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </Link>
        </div>
      </div>

      {/* Control & Status Card */}
      <div className="card-base p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center space-x-3">
            {status !== 'running' ? (
              <button
                type="button"
                onClick={handleStartRefresh}
                disabled={!skpUrl}
                className="inline-flex items-center space-x-2 px-5 py-2.5 rounded-lg text-xs sm:text-sm font-semibold text-white gradient-brand hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0] transition-opacity"
              >
                <RefreshCw className="w-4 h-4" />
                <span>Refresh Metadata</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={handleCancel}
                className="inline-flex items-center space-x-2 px-4 py-2 rounded-lg text-xs sm:text-sm font-semibold text-red-700 bg-red-50 hover:bg-red-100 border border-red-200 focus-visible:ring-2 focus-visible:ring-red-500"
              >
                <XCircle className="w-4 h-4" />
                <span>Cancel</span>
              </button>
            )}
          </div>

          {/* Status Line (aria-live polite) */}
          <div
            role="status"
            aria-live="polite"
            className="text-xs sm:text-sm text-[#4B5563] flex flex-wrap items-center gap-2"
          >
            <span className="font-semibold text-[#111827]">Status:</span>
            <span
              className={cn(
                'inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold',
                status === 'idle' && 'bg-gray-100 text-gray-700',
                status === 'running' && 'bg-purple-100 text-[#6D3FE0] animate-pulse',
                status === 'completed' && 'bg-green-100 text-green-800',
                status === 'failed' && 'bg-red-100 text-red-800',
                status === 'cancelled' && 'bg-amber-100 text-amber-800'
              )}
            >
              {status === 'running' && <Loader2 className="w-3 h-3 animate-spin" />}
              {status === 'completed' && <CheckCircle2 className="w-3 h-3 text-green-600" />}
              {status === 'failed' && <AlertCircle className="w-3 h-3 text-red-600" />}
              <span>{status.charAt(0).toUpperCase() + status.slice(1)}</span>
            </span>

            {status === 'running' && (
              <span className="text-gray-500 font-mono">({elapsedSeconds}s)</span>
            )}

            <span className="text-gray-300">|</span>
            <span className="text-gray-600">
              Stage: <strong className="text-[#111827]">{currentStage}</strong>
            </span>
          </div>
        </div>

        {/* Progress Bar when percent known */}
        {progressPercent != null && (
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-gray-500 font-medium">
              <span>Overall Progress</span>
              <span>{progressPercent}%</span>
            </div>
            <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-[#6D3FE0] to-[#3B82F6] transition-all duration-300"
                style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }}
              />
            </div>
          </div>
        )}

        {/* Metric Counts Chips */}
        {counts.length > 0 && (
          <div className="flex flex-wrap gap-2 pt-2">
            {counts.map((c, i) => (
              <span
                key={i}
                className="inline-flex items-center space-x-1.5 px-3 py-1 bg-violet-50 border border-violet-100 text-violet-950 rounded-full text-xs font-medium"
              >
                <span className="text-[#6D3FE0] font-bold">{c.value.toLocaleString()}</span>
                <span className="text-gray-600">{c.label}</span>
              </span>
            ))}
          </div>
        )}

        {/* Integration Pending / Error notice */}
        {pendingError && (
          <div
            role="alert"
            className="p-3.5 bg-amber-50 border border-amber-200 rounded-xl text-xs text-amber-900 font-medium flex items-start space-x-2"
          >
            <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
            <span>{pendingError}</span>
          </div>
        )}
      </div>

      {/* Live Terminal Panel */}
      <div className="card-base overflow-hidden border border-[#1E293B]">
        {/* Terminal Header Toolbar */}
        <div className="bg-[#0B1020] border-b border-gray-800 px-4 py-2.5 flex flex-wrap items-center justify-between gap-3 text-xs text-gray-300">
          <div className="flex items-center space-x-2">
            <span className="w-2.5 h-2.5 rounded-full bg-red-500 inline-block" />
            <span className="w-2.5 h-2.5 rounded-full bg-amber-500 inline-block" />
            <span className="w-2.5 h-2.5 rounded-full bg-green-500 inline-block" />
            <span className="ml-2 font-mono text-gray-400 font-semibold">
              Live Refresh Terminal
            </span>
          </div>

          <div className="flex items-center space-x-3">
            <label className="flex items-center space-x-1.5 cursor-pointer text-gray-300 hover:text-white">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="rounded border-gray-700 bg-gray-900 text-[#6D3FE0] focus:ring-[#6D3FE0]"
              />
              <span className="text-[11px]">Auto-scroll</span>
            </label>

            <button
              type="button"
              onClick={handleCopyLogs}
              disabled={logs.length === 0}
              className="p-1 hover:text-white disabled:opacity-40 rounded focus-visible:ring-1 focus-visible:ring-[#6D3FE0]"
              title="Copy all logs"
              aria-label="Copy all logs to clipboard"
            >
              <Copy className="w-3.5 h-3.5" />
            </button>

            <button
              type="button"
              onClick={handleDownloadLogs}
              disabled={logs.length === 0}
              className="p-1 hover:text-white disabled:opacity-40 rounded focus-visible:ring-1 focus-visible:ring-[#6D3FE0]"
              title="Download log file"
              aria-label="Download log file"
            >
              <Download className="w-3.5 h-3.5" />
            </button>

            <button
              type="button"
              onClick={handleClearLogs}
              disabled={logs.length === 0}
              className="p-1 hover:text-red-400 disabled:opacity-40 rounded focus-visible:ring-1 focus-visible:ring-[#6D3FE0]"
              title="Clear terminal"
              aria-label="Clear terminal logs"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Terminal Screen Region */}
        <div className="relative">
          <div
            ref={terminalRef}
            onScroll={handleTerminalScroll}
            role="log"
            aria-live="off"
            aria-label="Refresh execution log terminal output"
            className="bg-[#0B1020] text-gray-100 font-mono text-xs p-4 h-96 overflow-y-auto space-y-1 focus:outline-none focus:ring-1 focus:ring-[#6D3FE0]"
          >
            {droppedCount > 0 && (
              <div className="text-gray-500 italic pb-2 border-b border-gray-800 text-[11px]">
                [{droppedCount.toLocaleString()} earlier lines dropped]
              </div>
            )}

            {logs.length === 0 ? (
              <div className="h-full flex items-center justify-center text-gray-500 italic select-none">
                No refresh running. Click Refresh Metadata to start.
              </div>
            ) : (
              logs.map((log) => {
                const levelColor =
                  log.level === 'error'
                    ? 'text-red-400'
                    : log.level === 'warn'
                      ? 'text-amber-400'
                      : 'text-emerald-400';

                return (
                  <div
                    key={log.id}
                    className="leading-relaxed hover:bg-gray-900/60 px-1 rounded flex items-start space-x-2"
                  >
                    <span className="text-gray-500 select-none text-[11px] whitespace-nowrap">
                      {log.ts.split('T')[1]?.replace('Z', '') || log.ts}
                    </span>
                    <span
                      className={cn(
                        'font-bold uppercase text-[11px] w-12 flex-shrink-0',
                        levelColor
                      )}
                    >
                      {log.level}
                    </span>
                    <span className="text-purple-300 font-medium select-none text-[11px]">
                      [{log.stage}]
                    </span>
                    <span className="text-gray-200 break-all">{log.message}</span>
                  </div>
                );
              })
            )}
          </div>

          {/* Jump to Latest Pill */}
          {isScrolledUp && (
            <button
              type="button"
              onClick={scrollToBottom}
              className="absolute bottom-3 right-4 bg-[#6D3FE0] hover:bg-[#5B2FD0] text-white text-xs font-semibold px-3 py-1.5 rounded-full shadow-lg flex items-center space-x-1 animate-in fade-in transition-transform active:scale-95"
            >
              <span>Jump to latest</span>
              <ChevronDown className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default RefreshMetadataPage;
