// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AxiosError, AxiosHeaders } from 'axios';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { ApiError } from '../src/http/api-error';
import { httpClient, request } from '../src/http/client';
import type { LoginRequest, LoginResponse, User } from '../typefiles';

const user: User = {
  id: 7,
  email: 'pilot@example.com',
  name: 'Pilot User',
  role: 'admin',
  is_active: true,
  account_expires_at: null,
};

function axiosConfig(): InternalAxiosRequestConfig {
  return { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
}

function axiosResponse<T>(data: T, status = 200): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: status >= 400 ? 'Error' : 'OK',
    headers: {},
    config: axiosConfig(),
  };
}

function axiosFailure(status: number, data: unknown): AxiosError {
  return new AxiosError(
    `Request failed with status code ${status}`,
    'ERR_BAD_REQUEST',
    axiosConfig(),
    undefined,
    axiosResponse(data, status),
  );
}

describe('typed HTTP client', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('sends a public login request without an authorization header', async () => {
    const payload: LoginRequest = {
      email: 'pilot@example.com',
      password: 'correct-password',
    };
    const response: LoginResponse = { success: true, token: 'token-value', user };
    const requestSpy = vi.spyOn(httpClient, 'request').mockResolvedValue(axiosResponse(response));

    await expect(request<LoginResponse, LoginRequest>({
      endpoint: 'auth/login',
      method: 'post',
      data: payload,
      auth: 'none',
    })).resolves.toEqual(response);

    const config = requestSpy.mock.calls[0]?.[0];
    expect(config?.url).toBe('/auth/login');
    expect(config?.method).toBe('post');
    expect(config?.data).toEqual(payload);
    expect(config?.headers).toMatchObject({ 'X-App-Key': expect.any(String) });
    expect(config?.headers).not.toHaveProperty('Authorization');
  });

  it('rejects protocol-relative endpoints and prevents app-key overrides', async () => {
    await expect(request({
      endpoint: '//untrusted.example/api',
      auth: 'none',
    })).rejects.toMatchObject({ code: 'INVALID_ENDPOINT' });

    const requestSpy = vi.spyOn(httpClient, 'request').mockResolvedValue(
      axiosResponse({ ok: true }),
    );
    await request<{ ok: boolean }>({
      endpoint: '/public-check',
      auth: 'none',
      headers: { 'X-App-Key': 'caller-override' },
    });
    expect(requestSpy.mock.calls[0]?.[0].headers).not.toMatchObject({
      'X-App-Key': 'caller-override',
    });
  });

  it('normalizes FastAPI login errors without expiring a public session', async () => {
    window.localStorage.setItem('auth_token', 'existing-token');
    vi.spyOn(httpClient, 'request').mockRejectedValue(
      axiosFailure(401, { detail: 'Invalid email or password' }),
    );

    const result = request<LoginResponse, LoginRequest>({
      endpoint: '/auth/login',
      method: 'post',
      data: { email: 'pilot@example.com', password: 'wrong-password' },
      auth: 'none',
    });

    await expect(result).rejects.toMatchObject<ApiError>({
      name: 'ApiError',
      message: 'Invalid email or password',
      status: 401,
    });
    expect(window.localStorage.getItem('auth_token')).toBe('existing-token');
  });

  it('injects the bearer token for authenticated requests', async () => {
    window.localStorage.setItem('auth_token', 'stored-token');
    const requestSpy = vi.spyOn(httpClient, 'request').mockResolvedValue(axiosResponse(user));

    await request<User>({ endpoint: '/auth/me' });

    expect(requestSpy.mock.calls[0]?.[0].headers).toMatchObject({
      Authorization: 'Bearer stored-token',
      'X-App-Key': expect.any(String),
    });
  });

  it('does not set a multipart content type so Axios can add the boundary', async () => {
    window.localStorage.setItem('auth_token', 'stored-token');
    const requestSpy = vi.spyOn(httpClient, 'request').mockResolvedValue(
      axiosResponse({ success: true }),
    );
    const formData = new FormData();
    formData.append('file', new Blob(['content']), 'sample.txt');

    await request<{ success: boolean }, FormData>({
      endpoint: '/campaigns/upload-attachment',
      method: 'post',
      data: formData,
      contentType: 'multipart/form-data',
    });

    expect(requestSpy.mock.calls[0]?.[0].headers).not.toHaveProperty('Content-Type');
  });

  it('retries eligible reads but never retries mutations', async () => {
    window.localStorage.setItem('auth_token', 'stored-token');
    const networkError = new AxiosError('Network Error', 'ERR_NETWORK', axiosConfig());
    const requestSpy = vi.spyOn(httpClient, 'request')
      .mockRejectedValueOnce(networkError)
      .mockResolvedValueOnce(axiosResponse({ ok: true }));

    await expect(request<{ ok: boolean }>({
      endpoint: '/system/health',
      retry: { maxRetries: 1, baseDelayMs: 0, maxDelayMs: 0, jitter: false },
    })).resolves.toEqual({ ok: true });
    expect(requestSpy).toHaveBeenCalledTimes(2);

    requestSpy.mockReset();
    requestSpy.mockRejectedValue(networkError);
    await expect(request({
      endpoint: '/campaigns',
      method: 'post',
      data: { name: 'Do not duplicate' },
      retry: 3,
    })).rejects.toMatchObject({ isNetworkError: true });
    expect(requestSpy).toHaveBeenCalledTimes(1);
  });

  it('handles successful empty responses', async () => {
    window.localStorage.setItem('auth_token', 'stored-token');
    vi.spyOn(httpClient, 'request').mockResolvedValue(axiosResponse('', 204));

    await expect(request<void>({
      endpoint: '/resource/1',
      method: 'delete',
    })).resolves.toBeUndefined();
  });
});
