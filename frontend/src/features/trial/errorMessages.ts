import type { ApiError, ContractError, NetworkError, TimeoutError } from '@/api/client';

export interface ErrorConfig {
  title: string;
  description: string;
  isRetryable: boolean;
  hasCountdown?: boolean;
}

export const ERROR_CODE_MAP: Record<string, ErrorConfig> = {
  LLM_NOT_CONFIGURED: {
    title: 'AI service not configured',
    description: 'The server has no AI credentials; contact the administrator.',
    isRetryable: false,
  },
  LLM_AUTH_FAILED: {
    title: 'Authentication Failed',
    description: "The AI service rejected the server's credentials.",
    isRetryable: false,
  },
  LLM_QUOTA_EXCEEDED: {
    title: 'Quota Exceeded',
    description:
      'AI provider quota or billing problem (administrator must check the provider account).',
    isRetryable: false,
  },
  LLM_RATE_LIMITED: {
    title: 'Rate Limited',
    description: 'AI service rate limit reached. Please wait before retrying.',
    isRetryable: true,
    hasCountdown: true,
  },
  CLIENT_RATE_LIMITED: {
    title: 'Client Rate Limited',
    description: 'Client rate limit reached. Please wait before retrying.',
    isRetryable: true,
    hasCountdown: true,
  },
  RATE_LIMITED: {
    title: 'Rate Limited',
    description: 'Request rate limit reached. Please wait before retrying.',
    isRetryable: true,
    hasCountdown: true,
  },
  LLM_BUDGET_EXCEEDED: {
    title: 'Daily Budget Exceeded',
    description: 'Daily token or cost budget cap reached.',
    isRetryable: true,
    hasCountdown: true,
  },
  LLM_UNAVAILABLE: {
    title: 'AI Service Temporarily Unavailable',
    description: 'The AI service is temporarily unavailable. Please retry.',
    isRetryable: true,
  },
  CIRCUIT_BREAKER_OPEN: {
    title: 'Circuit Breaker Open',
    description: 'AI subsystem circuit breaker is open. Temporarily unavailable.',
    isRetryable: true,
  },
  SYNTHESIS_FAILED: {
    title: 'Synthesis Failed',
    description: 'The AI could not produce a validated result.',
    isRetryable: true,
  },
  SQL_PARSE_ERROR: {
    title: 'SQL Syntax Error',
    description: 'The SQL statement contains syntax errors and could not be parsed.',
    isRetryable: false,
  },
  INPUT_REJECTED: {
    title: 'Rejected by safety checks',
    description: 'Input query was rejected by enterprise safety checks.',
    isRetryable: false,
  },
  VALIDATION_ERROR: {
    title: 'Validation Error',
    description: 'Request fields failed schema validation.',
    isRetryable: false,
  },
  PAYLOAD_TOO_LARGE: {
    title: 'Query Too Large',
    description: 'The query exceeds the maximum allowed character limit.',
    isRetryable: false,
  },
  TIMEOUT: {
    title: 'Request Timed Out',
    description: 'The server analysis operation timed out.',
    isRetryable: true,
  },
  INTERNAL_ERROR: {
    title: 'Internal Server Error',
    description: 'An unexpected internal error occurred on the server.',
    isRetryable: true,
  },
  ContractError: {
    title: 'Server Contract Error',
    description: 'Unexpected response from the server.',
    isRetryable: false,
  },
  NetworkError: {
    title: 'Network Connection Lost',
    description: 'Unable to communicate with the server. Please check your connection.',
    isRetryable: true,
  },
  TimeoutError: {
    title: 'Request Timed Out',
    description: 'The request took longer than allowed and timed out.',
    isRetryable: true,
  },
};

export interface ResolvedErrorInfo {
  code: string;
  title: string;
  description: string;
  message?: string;
  bannerColor?: 'red' | 'amber' | 'blue';
  requestId: string;
  retryAfterS: number | null;
  isRetryable: boolean;
  hasCountdown: boolean;
  details: Array<{ field?: string | null; issue: string }>;
  parseLocation?: { line: number; col?: number };
}

export function resolveError(
  err: ApiError | ContractError | NetworkError | TimeoutError | Error | unknown
): ResolvedErrorInfo {
  let code = 'INTERNAL_ERROR';
  let message = 'An unexpected error occurred.';
  let requestId = 'unknown';
  let retryAfterS: number | null = null;
  let details: Array<{ field?: string | null; issue: string }> = [];

  if (err && typeof err === 'object') {
    const root =
      'error' in err && err.error && typeof err.error === 'object'
        ? (err.error as Record<string, unknown>)
        : (err as Record<string, unknown>);

    if ('code' in root && typeof root.code === 'string') {
      code = root.code;
    } else if ('name' in root && typeof root.name === 'string') {
      code = root.name;
    }

    if ('requestId' in root && typeof root.requestId === 'string') {
      requestId = root.requestId;
    } else if ('request_id' in root && typeof root.request_id === 'string') {
      requestId = root.request_id;
    }

    if ('retryAfterS' in root && typeof root.retryAfterS === 'number') {
      retryAfterS = root.retryAfterS;
    } else if ('retry_after_s' in root && typeof root.retry_after_s === 'number') {
      retryAfterS = root.retry_after_s;
    }

    if ('details' in root && Array.isArray(root.details)) {
      details = root.details;
    }

    if ('message' in root && typeof root.message === 'string') {
      message = root.message;
    }
  }

  const mapped = ERROR_CODE_MAP[code];
  const title = mapped ? mapped.title : code || 'Error';
  const description = mapped ? mapped.description : message;
  const isRetryable = mapped ? mapped.isRetryable : false;
  const hasCountdown = (mapped?.hasCountdown ?? false) && retryAfterS !== null && retryAfterS > 0;

  // Parse location extraction for SQL_PARSE_ERROR
  let parseLocation: { line: number; col?: number } | undefined = undefined;
  if (code === 'SQL_PARSE_ERROR') {
    for (const d of details) {
      if (d.field) {
        const match = /line\s+(\d+)(?:,\s*col\s+(\d+))?/i.exec(d.field);
        if (match && match[1]) {
          parseLocation = {
            line: parseInt(match[1], 10),
            col: match[2] ? parseInt(match[2], 10) : undefined,
          };
          break;
        }
      }
    }
  }

  const bannerColor: 'red' | 'amber' | 'blue' =
    code.includes('RATE') || code.includes('BUDGET')
      ? 'amber'
      : code === 'INPUT_REJECTED'
        ? 'blue'
        : 'red';

  return {
    code,
    title,
    description,
    message: message || description,
    bannerColor,
    requestId,
    retryAfterS,
    isRetryable,
    hasCountdown,
    details,
    parseLocation,
  };
}
