import { useCallback, useMemo, useReducer } from 'react';
import { showToast } from '../../components/toast-store';
import { useConfirm } from '../../components/useConfirm';
import { useAuth } from '../../contexts/useAuth';
import { getErrorMessage } from '../../http/api-error';
import { useApiQuery } from '../../hooks/useApiQuery';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import type {
  ActivationCodesResponse,
  CreateActivationCodeRequest,
  CreateActivationCodeResponse,
  FallbackReplyEmailRequest,
  FallbackReplyEmailResponse,
  FallbackReplyInfo,
  SetFallbackReplyEmailResponse,
  SettingsPageController,
  SettingsWorkflowAction,
  SettingsWorkflowState,
  SuccessResponse,
  UpdateUserRoleRequest,
  UserDetails,
  UserRole,
  UsersResponse,
} from '../../../typefiles';

const INITIAL_WORKFLOW_STATE: SettingsWorkflowState = {
  selectedUserId: null,
  creatingCode: false,
  savingFallback: false,
};

function workflowReducer(
  state: SettingsWorkflowState,
  action: SettingsWorkflowAction,
): SettingsWorkflowState {
  switch (action.type) {
    case 'user-details-opened':
      return { ...state, selectedUserId: action.userId };
    case 'user-details-closed':
      return { ...state, selectedUserId: null };
    case 'code-creation-started':
      return { ...state, creatingCode: true };
    case 'code-creation-finished':
      return { ...state, creatingCode: false };
    case 'fallback-save-started':
      return { ...state, savingFallback: true };
    case 'fallback-save-finished':
      return { ...state, savingFallback: false };
    default:
      return state;
  }
}

export function useSettingsPage(): SettingsPageController {
  const { isAdmin, token, user } = useAuth();
  const { fetchIt } = useHttpFetcher();
  const confirm = useConfirm();
  const [workflow, dispatch] = useReducer(workflowReducer, INITIAL_WORKFLOW_STATE);
  const adminQueriesEnabled = isAdmin && Boolean(token);

  const codesQuery = useApiQuery<ActivationCodesResponse>({
    cacheKey: ['settings', 'activation-codes'],
    endpoint: 'auth/activation-codes',
    enabled: adminQueriesEnabled,
    errorNotification: { enabled: true },
  });
  const usersQuery = useApiQuery<UsersResponse>({
    cacheKey: ['settings', 'users'],
    endpoint: 'auth/users',
    enabled: adminQueriesEnabled,
    errorNotification: { enabled: true },
  });
  const fallbackQuery = useApiQuery<FallbackReplyEmailResponse>({
    cacheKey: ['settings', 'fallback-reply-email'],
    endpoint: 'system/fallback-reply-email',
  });
  const userDetailsQuery = useApiQuery<UserDetails>({
    cacheKey: ['settings', 'user-details', workflow.selectedUserId],
    endpoint: `auth/users/${workflow.selectedUserId ?? 0}/details`,
    enabled: Boolean(token) && workflow.selectedUserId !== null,
    keepPreviousData: false,
    errorNotification: { enabled: true },
  });

  const activationCodes = codesQuery.data?.codes ?? [];
  const users = usersQuery.data?.users ?? [];
  const fallbackConfigured = fallbackQuery.data?.configured ?? false;
  const fallbackInfo = useMemo<FallbackReplyInfo | null>(() => {
    if (!fallbackQuery.data?.configured) return null;
    return {
      email: fallbackQuery.data.email,
      smtp_host: fallbackQuery.data.smtp_host,
      is_active: fallbackQuery.data.is_active,
    };
  }, [fallbackQuery.data]);

  const mutateCodes = codesQuery.mutate;
  const mutateUsers = usersQuery.mutate;
  const mutateFallback = fallbackQuery.mutate;
  const mutateUserDetails = userDetailsQuery.mutate;

  const refreshCodes = useCallback(
    () => mutateCodes(),
    [mutateCodes],
  );
  const refreshUsers = useCallback(
    () => mutateUsers(),
    [mutateUsers],
  );
  const refreshFallback = useCallback(
    () => mutateFallback(),
    [mutateFallback],
  );

  const refresh = useCallback(async () => {
    const refreshes: Promise<unknown>[] = [refreshFallback()];
    if (adminQueriesEnabled) {
      refreshes.push(refreshCodes(), refreshUsers());
    }
    await Promise.allSettled(refreshes);
  }, [adminQueriesEnabled, refreshCodes, refreshFallback, refreshUsers]);

  const createCode = useCallback(async (durationDays: number, note: string) => {
    if (!token || !isAdmin) return false;
    dispatch({ type: 'code-creation-started' });
    try {
      const requestData: CreateActivationCodeRequest = {
        duration_days: durationDays,
        note: note || undefined,
      };
      await fetchIt<CreateActivationCodeResponse, CreateActivationCodeRequest>({
        apiEndPoint: 'auth/activation-codes',
        httpMethod: 'post',
        reqData: requestData,
      });
      showToast('Activation code created!', 'success');
      await refreshCodes();
      return true;
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
      return false;
    } finally {
      dispatch({ type: 'code-creation-finished' });
    }
  }, [fetchIt, isAdmin, refreshCodes, token]);

  const deleteCode = useCallback(async (codeId: number) => {
    const confirmed = await confirm({
      title: 'Delete Activation Code',
      message: 'Are you sure you want to delete this activation code?',
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed || !token || !isAdmin) return;
    try {
      await fetchIt<SuccessResponse>({
        apiEndPoint: `auth/activation-codes/${codeId}`,
        httpMethod: 'delete',
      });
      showToast('Code deleted', 'success');
      await refreshCodes();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [confirm, fetchIt, isAdmin, refreshCodes, token]);

  const copyCode = useCallback(async (code: string) => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(code);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = code;
        textarea.style.position = 'fixed';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      showToast('Code copied to clipboard', 'success');
    } catch {
      showToast('Failed to copy code', 'error');
    }
  }, []);

  const openUserDetails = useCallback((userId: number) => {
    if (workflow.selectedUserId === userId) {
      void mutateUserDetails();
      return;
    }
    dispatch({ type: 'user-details-opened', userId });
  }, [mutateUserDetails, workflow.selectedUserId]);

  const toggleRole = useCallback(async (userId: number, currentRole: string) => {
    if (!token || !isAdmin) return;
    const role: UserRole = currentRole === 'admin' ? 'user' : 'admin';
    const confirmed = await confirm({
      title: 'Change User Role',
      message: `Are you sure you want to change this user's role to ${role}?`,
      confirmText: 'Change Role',
      variant: 'warning',
    });
    if (!confirmed) return;
    try {
      const requestData: UpdateUserRoleRequest = { role };
      await fetchIt<SuccessResponse, UpdateUserRoleRequest>({
        apiEndPoint: `auth/users/${userId}/role`,
        httpMethod: 'put',
        reqData: requestData,
      });
      showToast(`User role updated to ${role}`, 'success');
      await refreshUsers();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [confirm, fetchIt, isAdmin, refreshUsers, token]);

  const saveFallback = useCallback(async (email: string, password: string) => {
    if (!email || !password) {
      showToast('Email and app password are required', 'error');
      return false;
    }
    dispatch({ type: 'fallback-save-started' });
    try {
      const requestData: FallbackReplyEmailRequest = {
        email,
        app_password: password,
      };
      const result = await fetchIt<SetFallbackReplyEmailResponse, FallbackReplyEmailRequest>({
        apiEndPoint: 'system/fallback-reply-email',
        httpMethod: 'post',
        reqData: requestData,
      });
      if (!result.success) return false;
      showToast('Fallback reply email saved!', 'success');
      await mutateFallback({
        configured: true,
        email: result.email,
        smtp_host: result.smtp_host,
        smtp_port: result.smtp_port,
        imap_host: result.imap_host ?? undefined,
        imap_port: result.imap_port,
        is_active: true,
      }, { revalidate: false });
      return true;
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
      return false;
    } finally {
      dispatch({ type: 'fallback-save-finished' });
    }
  }, [fetchIt, mutateFallback]);

  const removeFallback = useCallback(async () => {
    const confirmed = await confirm({
      title: 'Remove Fallback Email',
      message: 'Are you sure? Auto-replies will fail if inbox SMTP has issues.',
      confirmText: 'Remove',
      variant: 'danger',
    });
    if (!confirmed) return false;
    try {
      await fetchIt<SuccessResponse>({
        apiEndPoint: 'system/fallback-reply-email',
        httpMethod: 'delete',
      });
      showToast('Fallback email removed', 'success');
      await mutateFallback({ configured: false }, { revalidate: false });
      return true;
    } catch {
      showToast('Failed to remove', 'error');
      return false;
    }
  }, [confirm, fetchIt, mutateFallback]);

  return {
    permissions: {
      isAdmin,
      currentUserId: user?.id ?? null,
    },
    data: {
      activationCodes,
      users,
      selectedUserDetails: workflow.selectedUserId === null
        ? null
        : userDetailsQuery.data ?? null,
      fallbackConfigured,
      fallbackInfo,
    },
    status: {
      initialLoad: fallbackQuery.isLoading
        || (adminQueriesEnabled && (codesQuery.isLoading || usersQuery.isLoading)),
      loadingCodes: codesQuery.isValidating,
      loadingUsers: usersQuery.isValidating,
      loadingUserDetails: workflow.selectedUserId !== null && userDetailsQuery.isLoading,
      loadingFallback: fallbackQuery.isValidating,
      creatingCode: workflow.creatingCode,
      savingFallback: workflow.savingFallback,
    },
    admin: {
      refreshCodes,
      refreshUsers,
      createCode,
      deleteCode,
      copyCode,
      openUserDetails,
      closeUserDetails: () => dispatch({ type: 'user-details-closed' }),
      toggleRole,
    },
    fallback: {
      refresh: refreshFallback,
      save: saveFallback,
      remove: removeFallback,
    },
    actions: { refresh },
  };
}
