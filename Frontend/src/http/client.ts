import axios from 'axios';
import type { AxiosRequestConfig } from 'axios';
import { ApiError, toApiError } from './api-error';
import type {
  HttpAuthMode,
  HttpMethod,
  HttpRequestOptions,
  QueryParameters,
  RetryPolicy,
} from '../../typefiles';

const API_BASE_URL = '/api';
const APP_KEY = import.meta.env.VITE_API_KEY || '';
const TOKEN_STORAGE_KEY = 'auth_token';
const USER_STORAGE_KEY = 'auth_user';
const DEFAULT_TIMEOUT_MS = 30_000;

export const AUTH_SESSION_EXPIRED_EVENT = 'fleetctrl:auth-session-expired';

export const httpClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: DEFAULT_TIMEOUT_MS,
  headers: { Accept: 'application/json' },
});

function getStoredToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function expireStoredSession(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(USER_STORAGE_KEY);
  } catch {
    // The in-memory auth state is still cleared by the event below.
  }
  window.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT));
}

function normalizeEndpoint(endpoint: string): string {
  const value = endpoint.trim();
  if (!value) throw new ApiError('API endpoint is required', { code: 'INVALID_ENDPOINT' });
  if (/^[a-z][a-z\d+.-]*:/i.test(value) || value.startsWith('//') || value.startsWith('\\')) {
    throw new ApiError('Absolute API URLs are not allowed', { code: 'INVALID_ENDPOINT' });
  }
  return value.startsWith('/') ? value : `/${value}`;
}

function buildQueryParameters(query?: QueryParameters): URLSearchParams | undefined {
  if (!query) return undefined;

  const params = new URLSearchParams();
  for (const [key, rawValue] of Object.entries(query)) {
    const values = Array.isArray(rawValue) ? rawValue : [rawValue];
    for (const value of values) {
      if (value !== null && value !== undefined) params.append(key, String(value));
    }
  }
  return params;
}

function normalizeRetryPolicy(retry?: number | RetryPolicy): Required<RetryPolicy> {
  if (typeof retry === 'number') {
    return {
      maxRetries: Math.max(0, retry),
      baseDelayMs: 400,
      maxDelayMs: 5_000,
      jitter: true,
    };
  }

  return {
    maxRetries: Math.max(0, retry?.maxRetries ?? 0),
    baseDelayMs: Math.max(0, retry?.baseDelayMs ?? 400),
    maxDelayMs: Math.max(0, retry?.maxDelayMs ?? 5_000),
    jitter: retry?.jitter ?? true,
  };
}

function isRetryable(method: HttpMethod, error: ApiError): boolean {
  if (method !== 'get' || error.isCanceled) return false;
  if (error.isNetworkError) return true;
  return error.status !== undefined && [408, 425, 429, 500, 502, 503, 504].includes(error.status);
}

function retryDelay(attempt: number, policy: Required<RetryPolicy>, error: ApiError): number {
  if (error.retryAfterMs !== undefined) return Math.min(error.retryAfterMs, policy.maxDelayMs);
  const exponential = Math.min(policy.baseDelayMs * (2 ** attempt), policy.maxDelayMs);
  return policy.jitter ? Math.round(exponential * (0.5 + Math.random() * 0.5)) : exponential;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    return Promise.reject(new ApiError('Request cancelled', {
      code: 'ERR_CANCELED',
      isCanceled: true,
    }));
  }

  return new Promise((resolve, reject) => {
    const finish = () => {
      signal?.removeEventListener('abort', abort);
      resolve();
    };
    const timer = globalThis.setTimeout(finish, ms);
    const abort = () => {
      globalThis.clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      reject(new ApiError('Request cancelled', {
        code: 'ERR_CANCELED',
        isCanceled: true,
      }));
    };
    signal?.addEventListener('abort', abort, { once: true });
  });
}

function buildHeaders<TBody>(
  options: HttpRequestOptions<TBody>,
  auth: HttpAuthMode,
): Record<string, string> {
  const headers: Record<string, string> = {
    ...options.headers,
    'X-App-Key': APP_KEY,
  };
  const token = getStoredToken();

  if (auth !== 'none' && token) headers.Authorization = `Bearer ${token}`;

  const isFormData = typeof FormData !== 'undefined' && options.data instanceof FormData;
  if (options.contentType && !isFormData) headers['Content-Type'] = options.contentType;

  return headers;
}

export async function request<TResponse, TBody = undefined>(
  options: HttpRequestOptions<TBody>,
): Promise<TResponse> {
  const method = options.method ?? 'get';
  const auth = options.auth ?? 'required';
  const retryPolicy = normalizeRetryPolicy(options.retry);
  const token = getStoredToken();

  if (auth === 'required' && !token) {
    expireStoredSession();
    throw new ApiError('Please log in to continue', {
      status: 401,
      code: 'AUTH_REQUIRED',
    });
  }

  const config: AxiosRequestConfig<TBody> = {
    method,
    url: normalizeEndpoint(options.endpoint),
    data: options.data,
    params: buildQueryParameters(options.query),
    headers: buildHeaders(options, auth),
    responseType: options.responseType ?? 'json',
    signal: options.signal,
    timeout: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    withCredentials: options.withCredentials ?? false,
  };

  for (let attempt = 0; ; attempt += 1) {
    try {
      const response = await httpClient.request<TResponse>(config);
      if (response.status === 204 || response.status === 205) return undefined as TResponse;
      return response.data;
    } catch (error) {
      const apiError = toApiError(error);

      if (attempt < retryPolicy.maxRetries && isRetryable(method, apiError)) {
        await wait(retryDelay(attempt, retryPolicy, apiError), options.signal);
        continue;
      }

      if (apiError.status === 401 && auth === 'required') expireStoredSession();
      throw apiError;
    }
  }
}
