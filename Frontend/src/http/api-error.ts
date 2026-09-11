import axios from 'axios';
import type { ApiErrorOptions } from '../../typefiles';

const errorMessageMap: Readonly<Record<string, string>> = {
  'not found': 'Resource not found',
  unauthorized: 'Please log in to continue',
  forbidden: "You don't have permission for this action",
  'bad request': 'Invalid request data',
  'internal server error': 'Server error. Please try again later',
  'network error': 'Connection failed. Check your internet',
  timeout: 'Request timed out. Please try again',
  'failed to fetch': 'Unable to connect to server',
};

export class ApiError extends Error {
  readonly status?: number;
  readonly code?: string;
  readonly details?: unknown;
  readonly isNetworkError: boolean;
  readonly isCanceled: boolean;
  readonly retryAfterMs?: number;

  constructor(message: string, options: ApiErrorOptions = {}) {
    super(message, { cause: options.cause });
    this.name = 'ApiError';
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
    this.isNetworkError = options.isNetworkError ?? false;
    this.isCanceled = options.isCanceled ?? false;
    this.retryAfterMs = options.retryAfterMs;
  }
}

export function formatApiError(value: unknown): string | undefined {
  if (typeof value === 'string') return value;

  if (Array.isArray(value)) {
    const messages = value.flatMap((issue) => {
      if (typeof issue === 'string') return [issue];
      if (!issue || typeof issue !== 'object') return [];

      const data = issue as Record<string, unknown>;
      const issueMessage = typeof data.msg === 'string'
        ? data.msg
        : typeof data.message === 'string'
          ? data.message
          : undefined;
      if (!issueMessage) return [];

      const location = Array.isArray(data.loc)
        ? data.loc.filter((part) => part !== 'body').map(String).join('.')
        : '';
      return [location ? `${location}: ${issueMessage}` : issueMessage];
    });

    return messages.length > 0 ? messages.join('; ') : undefined;
  }

  if (value && typeof value === 'object') {
    const data = value as Record<string, unknown>;
    return formatApiError(data.detail)
      ?? formatApiError(data.message)
      ?? formatApiError(data.error);
  }

  return undefined;
}

function parseRetryAfter(value: unknown): number | undefined {
  if (typeof value !== 'string' && typeof value !== 'number') return undefined;

  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);

  const retryAt = Date.parse(String(value));
  if (Number.isNaN(retryAt)) return undefined;
  return Math.max(0, retryAt - Date.now());
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;

  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    const details = error.response?.data;
    const isCanceled = axios.isCancel(error) || error.code === 'ERR_CANCELED';
    const isTimeout = error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT';
    const responseHeaders = error.response?.headers as Record<string, unknown> | undefined;
    const retryAfterMs = parseRetryAfter(responseHeaders?.['retry-after']);
    const message = isCanceled
      ? 'Request cancelled'
      : isTimeout
        ? 'Request timed out. Please try again'
        : formatApiError(details) ?? error.message ?? 'Request failed';

    return new ApiError(message, {
      status,
      code: error.code ?? (status ? `HTTP_${status}` : undefined),
      details,
      isNetworkError: !error.response && !isCanceled,
      isCanceled,
      retryAfterMs,
      cause: error,
    });
  }

  if (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError') {
    return new ApiError('Request cancelled', {
      code: 'ERR_CANCELED',
      isCanceled: true,
      cause: error,
    });
  }

  if (error instanceof Error) {
    return new ApiError(error.message || 'Request failed', { cause: error });
  }

  return new ApiError(formatApiError(error) ?? 'Request failed', {
    details: error,
    cause: error,
  });
}

export function getErrorMessage(error: unknown): string {
  let message = formatApiError(error) ?? 'Something went wrong';

  const lowerMessage = message.toLowerCase();
  for (const [pattern, friendly] of Object.entries(errorMessageMap)) {
    if (lowerMessage.includes(pattern)) return friendly;
  }

  if (/^(error:|http \d+|status \d+)/i.test(message)) {
    return 'Operation failed. Please try again';
  }

  if (message.length > 200) message = `${message.substring(0, 200)}…`;
  return message;
}
