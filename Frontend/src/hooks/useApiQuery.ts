import { useEffect, useRef } from 'react';
import useSWR from 'swr';
import { showToast } from '../components/toast-store';
import { ApiError, getErrorMessage, toApiError } from '../http/api-error';
import { request } from '../http/client';
import type { ApiQueryOptions } from '../../typefiles';

export function useApiQuery<TResponse>(options: ApiQueryOptions<TResponse>) {
  const {
    cacheKey,
    endpoint,
    query,
    pagination,
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
    ? { cacheKey, endpoint, query: query ?? null, pagination: pagination ?? null }
    : null;

  const result = useSWR<TResponse, unknown>(
    swrKey,
    async () => {
      const requestPage = <T,>(pageQuery = query) => request<T>({
        endpoint,
        method: 'get',
        query: pageQuery,
        headers,
        auth,
        responseType,
        signal,
        timeoutMs,
        retry,
        withCredentials,
      });

      if (!pagination) return requestPage<TResponse>();

      const pageSize = Math.max(1, Math.floor(pagination.pageSize ?? 100));
      const maxPages = Math.max(1, Math.floor(pagination.maxPages ?? 100));
      const items: unknown[] = [];

      for (let pageNumber = 0; pageNumber < maxPages; pageNumber += 1) {
        const page = await requestPage<unknown[]>({
          ...query,
          limit: pageSize,
          [pagination.offsetParameter]: pageNumber * pageSize,
        });
        if (!Array.isArray(page)) {
          throw new ApiError('Expected a list response while loading all pages', {
            code: 'INVALID_PAGINATED_RESPONSE',
          });
        }
        items.push(...page);
        if (page.length < pageSize) return items as TResponse;
      }

      throw new ApiError(`Stopped after loading ${maxPages} pages`, {
        code: 'PAGINATION_LIMIT_REACHED',
      });
    },
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
