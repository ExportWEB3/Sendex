import { useCallback, useEffect, useRef, useState } from 'react';
import { showToast } from '../components/toast-store';
import { getErrorMessage, toApiError } from '../http/api-error';
import { request } from '../http/client';
import type {
  HttpFetcherOptions,
  HttpRequestOptions,
  RequestNotificationOptions,
} from '../../typefiles';

function notificationMessage<TValue>(
  notification: RequestNotificationOptions<TValue>,
  value: TValue,
  fallback: string,
): string {
  if (typeof notification.message === 'function') return notification.message(value);
  return notification.message || fallback;
}

export function useHttpFetcher() {
  const [pendingRequests, setPendingRequests] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const fetchIt = useCallback(async <TResponse, TBody = undefined>(
    options: HttpFetcherOptions<TBody, TResponse>,
  ): Promise<TResponse> => {
    if (mountedRef.current) setPendingRequests((count) => count + 1);

    const requestOptions: HttpRequestOptions<TBody> = {
      endpoint: options.apiEndPoint,
      method: options.httpMethod,
      data: options.reqData,
      query: options.query,
      headers: options.headers,
      auth: options.auth,
      contentType: options.contentType,
      responseType: options.responseType,
      signal: options.signal,
      timeoutMs: options.timeoutMs,
      retry: options.retry,
      withCredentials: options.withCredentials,
    };

    try {
      const response = await request<TResponse, TBody>(requestOptions);
      if (options.successNotification?.enabled) {
        showToast(
          notificationMessage(options.successNotification, response, 'Request completed successfully'),
          'success',
        );
      }
      return response;
    } catch (error) {
      const apiError = toApiError(error);
      if (options.errorNotification?.enabled && !apiError.isCanceled) {
        showToast(
          notificationMessage(options.errorNotification, apiError, getErrorMessage(apiError)),
          'error',
        );
      }
      throw apiError;
    } finally {
      if (mountedRef.current) {
        setPendingRequests((count) => Math.max(0, count - 1));
      }
    }
  }, []);

  return {
    fetchIt,
    isLoading: pendingRequests > 0,
    pendingRequests,
  };
}
