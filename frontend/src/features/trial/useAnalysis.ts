import { useReducer, useRef, useCallback, useEffect } from 'react';
import type { BusinessIntelligence, ProgressEvent } from '@/api/schemas';
import { streamAnalysis } from '@/api/sse';
import { resolveError, type ResolvedErrorInfo } from './errorMessages';

export type StageId =
  | 'guardrails'
  | 'parsing'
  | 'cache_lookup'
  | 'agents_running'
  | 'synthesizing'
  | 'validating'
  | 'done';

export interface StageItem {
  id: StageId;
  label: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  elapsedMs?: number;
  message?: string;
  detail?: Record<string, unknown>;
}

export const INITIAL_STAGES: StageItem[] = [
  { id: 'guardrails', label: 'Checking input', status: 'pending' },
  { id: 'parsing', label: 'Parsing SQL', status: 'pending' },
  { id: 'cache_lookup', label: 'Checking cache', status: 'pending' },
  { id: 'agents_running', label: 'SQL Agent + SAP Agent', status: 'pending' },
  { id: 'synthesizing', label: 'Synthesizing', status: 'pending' },
  { id: 'validating', label: 'Validating', status: 'pending' },
  { id: 'done', label: 'Done', status: 'pending' },
];

export type AnalysisStatus = 'idle' | 'running' | 'success' | 'error';

export interface AnalysisState {
  status: AnalysisStatus;
  stages: StageItem[];
  result: BusinessIntelligence | null;
  error: ResolvedErrorInfo | null;
  announcement: string;
  liveProgressUnavailable: boolean;
  countdownSeconds: number | null;
}

type Action =
  | { type: 'START' }
  | { type: 'PROGRESS'; payload: ProgressEvent }
  | { type: 'RESULT'; payload: BusinessIntelligence }
  | { type: 'ERROR'; payload: ResolvedErrorInfo }
  | { type: 'CANCEL' }
  | { type: 'RESET' }
  | { type: 'SET_FALLBACK_NOTICE' }
  | { type: 'TICK_COUNTDOWN' };

const STAGE_ORDER: StageId[] = [
  'guardrails',
  'parsing',
  'cache_lookup',
  'agents_running',
  'synthesizing',
  'validating',
  'done',
];

function analysisReducer(state: AnalysisState, action: Action): AnalysisState {
  switch (action.type) {
    case 'START':
      return {
        status: 'running',
        stages: INITIAL_STAGES.map((s) => ({ ...s, status: 'pending' })),
        result: null,
        error: null,
        announcement: 'Analysis started. Checking input...',
        liveProgressUnavailable: false,
        countdownSeconds: null,
      };

    case 'PROGRESS': {
      const { stage, message, elapsed_ms, detail } = action.payload;
      const targetIndex = STAGE_ORDER.indexOf(stage as StageId);

      const nextStages: StageItem[] = state.stages.map((st, idx): StageItem => {
        if (targetIndex === -1) return st;

        if (idx < targetIndex) {
          // Earlier stages are completed
          return {
            ...st,
            status: 'completed',
          };
        } else if (idx === targetIndex) {
          return {
            ...st,
            status: 'running',
            message,
            elapsedMs: elapsed_ms,
            detail,
          };
        } else {
          return { ...st, status: 'pending' };
        }
      });

      return {
        ...state,
        stages: nextStages,
        announcement: `${action.payload.stage}: ${message}`,
      };
    }

    case 'RESULT': {
      const finalStages = state.stages.map((s) => ({
        ...s,
        status: 'completed' as const,
      }));

      const isCacheHit = action.payload.meta.cache_status === 'hit_final';
      const announcement = isCacheHit
        ? 'Analysis complete. Served from cache. No AI calls were made.'
        : 'Analysis completed successfully.';

      return {
        ...state,
        status: 'success',
        stages: finalStages,
        result: action.payload,
        announcement,
      };
    }

    case 'ERROR': {
      const errStages = state.stages.map((s) =>
        s.status === 'running' ? { ...s, status: 'failed' as const } : s
      );

      return {
        ...state,
        status: 'error',
        stages: errStages,
        error: action.payload,
        countdownSeconds: action.payload.retryAfterS ?? null,
        announcement: `Analysis failed: ${action.payload.title}. ${action.payload.description}`,
      };
    }

    case 'CANCEL':
      return {
        ...state,
        status: 'idle',
        stages: INITIAL_STAGES,
        announcement: 'Analysis cancelled by user.',
      };

    case 'RESET':
      return {
        status: 'idle',
        stages: INITIAL_STAGES,
        result: null,
        error: null,
        announcement: '',
        liveProgressUnavailable: false,
        countdownSeconds: null,
      };

    case 'SET_FALLBACK_NOTICE':
      return {
        ...state,
        liveProgressUnavailable: true,
        announcement: 'Live progress unavailable, falling back to synchronous execution.',
      };

    case 'TICK_COUNTDOWN':
      if (state.countdownSeconds == null || state.countdownSeconds <= 1) {
        return {
          ...state,
          countdownSeconds: 0,
        };
      }
      return {
        ...state,
        countdownSeconds: state.countdownSeconds - 1,
      };

    default:
      return state;
  }
}

export function useAnalysis() {
  const [state, dispatch] = useReducer(analysisReducer, {
    status: 'idle',
    stages: INITIAL_STAGES,
    result: null,
    error: null,
    announcement: '',
    liveProgressUnavailable: false,
    countdownSeconds: null,
  });

  const runIdRef = useRef(0);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Rate-limit countdown timer
  useEffect(() => {
    if (state.countdownSeconds != null && state.countdownSeconds > 0) {
      const interval = setInterval(() => {
        dispatch({ type: 'TICK_COUNTDOWN' });
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [state.countdownSeconds]);

  const analyze = useCallback(async (sql: string, forceRefresh = false) => {
    // Increment run counter to supersede any previous running operations
    runIdRef.current += 1;
    const currentRunId = runIdRef.current;

    // Abort previous request if running
    if (abortControllerRef.current) {
      abortControllerRef.current.abort('Superseded');
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    dispatch({ type: 'START' });

    try {
      await streamAnalysis(
        {
          sql,
          force_refresh: forceRefresh,
          signal: controller.signal,
        },
        {
          onProgress: (evt) => {
            if (runIdRef.current === currentRunId) {
              dispatch({ type: 'PROGRESS', payload: evt });
            }
          },
          onResult: (res) => {
            if (runIdRef.current === currentRunId) {
              dispatch({ type: 'RESULT', payload: res });
            }
          },
          onError: (err) => {
            if (runIdRef.current === currentRunId) {
              const resolved = resolveError(err);
              dispatch({ type: 'ERROR', payload: resolved });
            }
          },
          onFallback: () => {
            if (runIdRef.current === currentRunId) {
              dispatch({ type: 'SET_FALLBACK_NOTICE' });
            }
          },
        }
      );
    } catch (err: unknown) {
      if (runIdRef.current === currentRunId && !controller.signal.aborted) {
        const resolved = resolveError(err);
        dispatch({ type: 'ERROR', payload: resolved });
      }
    }
  }, []);

  const cancel = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort('Cancelled by user');
      abortControllerRef.current = null;
    }
    dispatch({ type: 'CANCEL' });
  }, []);

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort('Reset');
      abortControllerRef.current = null;
    }
    dispatch({ type: 'RESET' });
  }, []);

  return {
    ...state,
    analyze,
    cancel,
    reset,
  };
}
