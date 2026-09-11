// @vitest-environment jsdom

import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AxiosError, AxiosHeaders } from 'axios';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { AuthProvider } from '../src/contexts/AuthContext';
import { useAuth } from '../src/contexts/useAuth';
import { httpClient } from '../src/http/client';
import type { LoginResponse, User } from '../typefiles';

const user: User = {
  id: 9,
  email: 'login@example.com',
  name: 'Login Pilot',
  role: 'user',
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

function LoginHarness() {
  const { login, user: authenticatedUser } = useAuth();
  const [error, setError] = useState('');

  const submit = async () => {
    const result = await login('login@example.com', 'password-value');
    setError(result.error ?? '');
  };

  return (
    <div>
      <button type="button" onClick={submit}>Log in</button>
      <output aria-label="authenticated-user">{authenticatedUser?.email ?? ''}</output>
      <output aria-label="login-error">{error}</output>
    </div>
  );
}

describe('login request pilot', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('uses the typed request hook and persists a successful login', async () => {
    const response: LoginResponse = { success: true, token: 'pilot-token', user };
    const requestSpy = vi.spyOn(httpClient, 'request').mockResolvedValue(axiosResponse(response));

    render(
      <AuthProvider>
        <LoginHarness />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Log in' }));

    await waitFor(() => {
      expect(screen.getByLabelText('authenticated-user').textContent).toBe(user.email);
    });
    expect(window.localStorage.getItem('auth_token')).toBe('pilot-token');
    expect(JSON.parse(window.localStorage.getItem('auth_user') ?? '{}')).toEqual(user);
    expect(requestSpy).toHaveBeenCalledOnce();
    expect(requestSpy.mock.calls[0]?.[0]).toMatchObject({
      url: '/auth/login',
      method: 'post',
      data: { email: 'login@example.com', password: 'password-value' },
    });
  });

  it('returns a normalized inline error for rejected credentials', async () => {
    const error = new AxiosError(
      'Request failed with status code 401',
      'ERR_BAD_REQUEST',
      axiosConfig(),
      undefined,
      axiosResponse({ detail: 'Invalid email or password' }, 401),
    );
    vi.spyOn(httpClient, 'request').mockRejectedValue(error);

    render(
      <AuthProvider>
        <LoginHarness />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Log in' }));

    await waitFor(() => {
      expect(screen.getByLabelText('login-error').textContent).toBe('Invalid email or password');
    });
    expect(window.localStorage.getItem('auth_token')).toBeNull();
    expect(screen.getByLabelText('authenticated-user').textContent).toBe('');
  });
});
