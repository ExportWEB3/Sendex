import { useEffect, useRef } from 'react';
import useSWR from 'swr';
import { showToast } from '../components/toast-store';
import { getErrorMessage, toApiError } from '../http/api-error';
import { request } from '../http/client';
import type { ApiQueryOptions } from '../../typefiles';

export function useApiQuery<TResponse>(options: ApiQueryOptions<TResponse>) {
  const {
    cacheKey,
    endpoint,
    query,
    headers,
    auth,
    responseType,
    signal,
    timeoutMs,
    retry,
    withCredentials,
    enabled = true,
    fallbackData,
    refreshInterval,
    keepPreviousData = true,
    revalidateOnFocus = false,
    shouldRetryOnError = false,
    errorNotification,
    onSuccess,
  } = options;
  const lastNotifiedErrorRef = useRef<unknown>(null);

  const swrKey = enabled
    ? { cacheKey, endpoint, query: query ?? null }
    : null;

  const result = useSWR<TResponse, unknown>(
    swrKey,
    () => request<TResponse>({
      endpoint,
      method: 'get',
      query,
      headers,
      auth,
      responseType,
      signal,
      timeoutMs,
      retry,
      withCredentials,
    }),
    {
      refreshInterval,
      fallbackData,
      revalidateOnFocus,
      shouldRetryOnError,
      keepPreviousData,
      ...(onSuccess ? { onSuccess } : {}),
    },
  );

  const apiError = result.error ? toApiError(result.error) : undefined;

  useEffect(() => {
    if (!apiError || !errorNotification?.enabled || apiError.isCanceled) return;
    if (lastNotifiedErrorRef.current === result.error) return;
    lastNotifiedErrorRef.current = result.error;
    const message = typeof errorNotification.message === 'function'
      ? errorNotification.message(apiError)
      : errorNotification.message || getErrorMessage(apiError);
    showToast(message, 'error');
  }, [apiError, errorNotification, result.error]);

  return {
    data: result.data,
    error: apiError,
    isLoading: result.isLoading,
    isValidating: result.isValidating,
    mutate: result.mutate,
    fetchData: result.data,
    fetchError: apiError,
    fetchIsLoading: result.isLoading,
  };
}
