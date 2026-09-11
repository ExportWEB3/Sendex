// @vitest-environment jsdom

import { StrictMode } from 'react';
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AxiosHeaders } from 'axios';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { useHttpFetcher } from '../src/hooks/useHttpFetcher';
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

describe('useHttpFetcher', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('tracks pending requests correctly under React Strict Mode', async () => {
    let resolveRequest: ((value: AxiosResponse<{ ok: boolean }>) => void) | undefined;
    vi.spyOn(httpClient, 'request').mockImplementation(() => new Promise((resolve) => {
      resolveRequest = resolve;
    }));

    const { result } = renderHook(() => useHttpFetcher(), { wrapper: StrictMode });
    let pendingRequest: Promise<{ ok: boolean }> | undefined;

    act(() => {
      pendingRequest = result.current.fetchIt<{ ok: boolean }>({
        apiEndPoint: '/public-check',
        auth: 'none',
      });
    });
    expect(result.current.pendingRequests).toBe(1);
    expect(result.current.isLoading).toBe(true);

    await act(async () => {
      resolveRequest?.(response({ ok: true }));
      await pendingRequest;
    });
    expect(result.current.pendingRequests).toBe(0);
    expect(result.current.isLoading).toBe(false);
  });
});