// @vitest-environment jsdom

import type { ReactNode } from 'react';
import { cleanup, renderHook, waitFor } from '@testing-library/react';
import { SWRConfig } from 'swr';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AxiosHeaders } from 'axios';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { useApiQuery } from '../src/hooks/useApiQuery';
import { httpClient } from '../src/http/client';

function response<T>(data: T): AxiosResponse<T> {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: { headers: new AxiosHeaders() } as InternalAxiosRequestConfig,
  };
}

function IsolatedSWR({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      {children}
    </SWRConfig>
  );
}

describe('useApiQuery', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('returns typed query data through the shared transport', async () => {
    const requestSpy = vi.spyOn(httpClient, 'request').mockResolvedValue(
      response({ enabled: true, message: 'pilot' }),
    );
    const { result } = renderHook(() => useApiQuery<{ enabled: boolean; message: string }>({
      cacheKey: 'pilot:status',
      endpoint: '/system/kill-switch/status',
      auth: 'none',
    }), { wrapper: IsolatedSWR });

    await waitFor(() => {
      expect(result.current.fetchData).toEqual({ enabled: true, message: 'pilot' });
    });
    expect(result.current.fetchError).toBeUndefined();
    expect(requestSpy).toHaveBeenCalledOnce();
  });

  it('does not request data while disabled', async () => {
    const requestSpy = vi.spyOn(httpClient, 'request');
    const { result } = renderHook(() => useApiQuery<{ enabled: boolean }>({
      cacheKey: 'pilot:disabled',
      endpoint: '/system/kill-switch/status',
      auth: 'none',
      enabled: false,
    }), { wrapper: IsolatedSWR });

    expect(result.current.fetchData).toBeUndefined();
    expect(result.current.fetchIsLoading).toBe(false);
    expect(requestSpy).not.toHaveBeenCalled();
  });

  it('loads and combines every page of an offset-based collection', async () => {
    const requestSpy = vi.spyOn(httpClient, 'request')
      .mockResolvedValueOnce(response([{ id: 1 }, { id: 2 }]))
      .mockResolvedValueOnce(response([{ id: 3 }]));
    const { result } = renderHook(() => useApiQuery<Array<{ id: number }>>({
      cacheKey: 'pilot:all-pages',
      endpoint: '/lists/',
      auth: 'none',
      query: { active: true },
      pagination: { offsetParameter: 'skip', pageSize: 2 },
    }), { wrapper: IsolatedSWR });

    await waitFor(() => {
      expect(result.current.fetchData).toEqual([{ id: 1 }, { id: 2 }, { id: 3 }]);
    });
    expect(requestSpy).toHaveBeenCalledTimes(2);

    const firstParams = requestSpy.mock.calls[0]?.[0].params as URLSearchParams;
    const secondParams = requestSpy.mock.calls[1]?.[0].params as URLSearchParams;
    expect(firstParams.get('active')).toBe('true');
    expect(firstParams.get('limit')).toBe('2');
    expect(firstParams.get('skip')).toBe('0');
    expect(secondParams.get('skip')).toBe('2');
  });
});
