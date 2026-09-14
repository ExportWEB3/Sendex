import { useCallback, useMemo, useReducer, useState } from 'react';
import { CACHE_KEYS } from '../../cache';
import { showToast } from '../../components/toast-store';
import { useConfirm } from '../../components/useConfirm';
import { getErrorMessage } from '../../http/api-error';
import { useApiQuery } from '../../hooks/useApiQuery';
import { useBulkSelection } from '../../hooks/useBulkSelection';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import { bulkDeleteWithFallback } from '../../utils/bulk-delete';
import { matchesPageSearch } from '../../utils/page-search';
import { getCachedInitial, hasCachedData, saveToCache } from '../../initial-cache';
import type {
  ImapDetectionResponse,
  ImapTestResponse,
  ReadyTestFailure,
  ResendConfig,
  SMTPAccount,
  SMTPAccountInput,
  SMTPAccountsEditorAction,
  SMTPAccountsEditorState,
  SMTPAccountsPageController,
  SMTPAccountsTestingAction,
  SMTPAccountsTestingState,
  SMTPDisplayStatus,
  SMTPFormAuthType,
  SMTPProviderType,
  SMTPTestResponse,
  SuccessResponse,
} from '../../../typefiles';

const INITIAL_EDITOR_STATE: SMTPAccountsEditorState = {
  createOpen: false,
  editingAccount: null,
  createProvider: 'smtp',
  editProvider: 'smtp',
  createAuthType: 'password',
  editAuthType: 'password',
  showPassword: false,
  showOauthSecret: false,
  showImapPassword: false,
};

const INITIAL_TESTING_STATE: SMTPAccountsTestingState = {
  testingReady: false,
  testingAccountIds: new Set<number>(),
  progress: null,
  failures: [],
};

function editorReducer(
  state: SMTPAccountsEditorState,
  action: SMTPAccountsEditorAction,
): SMTPAccountsEditorState {
  switch (action.type) {
    case 'create-opened':
      return {
        ...state,
        createOpen: true,
        createProvider: action.provider ?? 'smtp',
        createAuthType: 'password',
        showPassword: false,
        showOauthSecret: false,
        showImapPassword: false,
      };
    case 'create-closed':
      return { ...state, createOpen: false };
    case 'edit-opened':
      return {
        ...state,
        editingAccount: action.account,
        editProvider: action.account.provider_type || 'smtp',
        editAuthType: (action.account.auth_type as SMTPFormAuthType) || 'password',
        showPassword: false,
        showOauthSecret: false,
        showImapPassword: false,
      };
    case 'edit-closed':
      return { ...state, editingAccount: null };
    case 'provider-changed':
      return action.mode === 'create'
        ? { ...state, createProvider: action.provider }
        : { ...state, editProvider: action.provider };
    case 'auth-changed':
      return action.mode === 'create'
        ? { ...state, createAuthType: action.authType }
        : { ...state, editAuthType: action.authType };
    case 'secret-toggled':
      if (action.secret === 'password') return { ...state, showPassword: !state.showPassword };
      if (action.secret === 'oauth') return { ...state, showOauthSecret: !state.showOauthSecret };
      return { ...state, showImapPassword: !state.showImapPassword };
    default:
      return state;
  }
}

function testingReducer(
  state: SMTPAccountsTestingState,
  action: SMTPAccountsTestingAction,
): SMTPAccountsTestingState {
  switch (action.type) {
    case 'account-started':
      return {
        ...state,
        testingAccountIds: new Set(state.testingAccountIds).add(action.id),
      };
    case 'account-finished': {
      const testingAccountIds = new Set(state.testingAccountIds);
      testingAccountIds.delete(action.id);
      return { ...state, testingAccountIds };
    }
    case 'ready-started':
      return {
        testingReady: true,
        testingAccountIds: new Set<number>(),
        progress: { completed: 0, total: action.total, succeeded: 0, failed: 0 },
        failures: [],
      };
    case 'ready-progressed':
      return { ...state, progress: action.progress };
    case 'ready-finished':
      return { ...state, failures: action.failures };
    case 'ready-cleaned-up':
      return { ...state, testingReady: false, testingAccountIds: new Set<number>() };
    default:
      return state;
  }
}

function getSmtpStatus(account: SMTPAccount): SMTPDisplayStatus {
  if (!account.last_used_at && account.is_active) return 'ready';
  return account.is_active ? 'active' : 'inactive';
}

export function useSmtpAccountsPage(): SMTPAccountsPageController {
  const { fetchIt } = useHttpFetcher();
  const confirm = useConfirm();
  const hadCache = useMemo(() => hasCachedData(CACHE_KEYS.SMTP_LIST), []);
  const cachedAccounts = useMemo(
    () => getCachedInitial<SMTPAccount[]>(CACHE_KEYS.SMTP_LIST, []),
    [],
  );
  const cachedConfig = useMemo(
    () => getCachedInitial<ResendConfig | null>(CACHE_KEYS.RESEND_CONFIG, null),
    [],
  );
  const [lastUpdated, markUpdated] = useReducer(() => new Date(), null as Date | null);
  const [editor, dispatchEditor] = useReducer(editorReducer, INITIAL_EDITOR_STATE);
  const [testing, dispatchTesting] = useReducer(testingReducer, INITIAL_TESTING_STATE);
  const [accountSearch, setAccountSearch] = useState('');

  const accountsQuery = useApiQuery<SMTPAccount[]>({
    cacheKey: CACHE_KEYS.SMTP_LIST,
    endpoint: 'smtp/',
    fallbackData: hadCache ? cachedAccounts : undefined,
    onSuccess: (accounts) => {
      saveToCache(CACHE_KEYS.SMTP_LIST, accounts);
      markUpdated();
    },
    errorNotification: { enabled: true },
  });
  const configQuery = useApiQuery<ResendConfig>({
    cacheKey: CACHE_KEYS.RESEND_CONFIG,
    endpoint: 'smtp/resend-config',
    fallbackData: cachedConfig ?? undefined,
    onSuccess: (config) => saveToCache(CACHE_KEYS.RESEND_CONFIG, config),
  });
  const accounts = accountsQuery.data ?? cachedAccounts;
  const resendConfig = configQuery.data ?? cachedConfig;
  const readyAccounts = useMemo(
    () => accounts.filter((account) => getSmtpStatus(account) === 'ready'),
    [accounts],
  );
  const visibleAccounts = useMemo(
    () => accounts.filter((account) => matchesPageSearch(accountSearch, [
      account.name,
      account.from_email,
      account.from_name,
      account.host,
      account.username,
      account.provider_type,
      account.auth_type,
      getSmtpStatus(account),
      account.has_imap ? 'imap' : null,
    ])),
    [accountSearch, accounts],
  );
  const accountIds = useMemo(
    () => visibleAccounts.map(({ id }) => id),
    [visibleAccounts],
  );
  const selection = useBulkSelection(accountIds);
  const resetSelection = selection.reset;
  const updateAccountSearch = useCallback((value: string) => {
    setAccountSearch(value);
    resetSelection();
  }, [resetSelection]);

  const mutateAccounts = accountsQuery.mutate;
  const mutateConfig = configQuery.mutate;
  const refresh = useCallback(async () => {
    await Promise.allSettled([mutateAccounts(), mutateConfig()]);
  }, [mutateAccounts, mutateConfig]);

  const test = useCallback(async (id: number) => {
    if (testing.testingReady || testing.testingAccountIds.has(id)) return;
    dispatchTesting({ type: 'account-started', id });
    try {
      showToast('Testing connection...', 'info');
      const result = await fetchIt<SMTPTestResponse>({
        apiEndPoint: `smtp/${id}/test`,
        httpMethod: 'post',
      });
      showToast(
        result.success ? (result.message || 'Connection successful!') : 'Connection failed',
        result.success ? 'success' : 'error',
      );
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      dispatchTesting({ type: 'account-finished', id });
      void refresh();
    }
  }, [fetchIt, refresh, testing.testingAccountIds, testing.testingReady]);

  const detectImap = useCallback(async (id: number) => {
    try {
      showToast('Detecting IMAP settings...', 'info');
      const result = await fetchIt<ImapDetectionResponse>({
        apiEndPoint: `smtp/${id}/detect-imap`,
        httpMethod: 'post',
      });
      showToast(
        result.success
          ? `IMAP detected: ${result.imap_host}:${result.imap_port}${result.inboxes_updated ? ` (${result.inboxes_updated} inboxes updated)` : ''}`
          : result.message || 'Could not detect IMAP',
        result.success ? 'success' : 'error',
      );
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt, refresh]);

  const testImap = useCallback(async (id: number) => {
    try {
      showToast('Testing IMAP connection...', 'info');
      const result = await fetchIt<ImapTestResponse>({
        apiEndPoint: `smtp/${id}/test-imap`,
        httpMethod: 'post',
      });
      showToast(
        result.success ? `IMAP OK: ${result.host}:${result.port}` : 'IMAP test failed',
        result.success ? 'success' : 'error',
      );
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt]);

  const remove = useCallback(async (id: number) => {
    const confirmed = await confirm({
      title: 'Delete Account',
      message: 'Are you sure you want to delete this account? This action cannot be undone.',
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;

    try {
      await fetchIt<SuccessResponse>({ apiEndPoint: `smtp/${id}`, httpMethod: 'delete' });
      showToast('Account removed successfully', 'success');
      void refresh();
    } catch (error: unknown) {
      const message = getErrorMessage(error);
      if (!message.includes('inbox') && !message.includes('linked')) {
        showToast(message, 'error');
        return;
      }
      const match = message.match(/(\d+)\s*(inbox|linked)/i);
      const forceConfirmed = await confirm({
        title: 'Account Has Linked Inboxes',
        message: `This account currently has ${match?.[1] ?? 'some'} linked inboxes.\n\nDo you want to force delete and remove all linked inboxes and their data?`,
        confirmText: 'Force Delete',
        variant: 'danger',
      });
      if (!forceConfirmed) return;
      try {
        await fetchIt<SuccessResponse>({
          apiEndPoint: `smtp/${id}`,
          httpMethod: 'delete',
          query: { force: true },
        });
        showToast('Account and linked inboxes deleted', 'success');
        void refresh();
      } catch {
        showToast('Force delete failed', 'error');
      }
    }
  }, [confirm, fetchIt, refresh]);

  const removeSelected = useCallback(async () => {
    const ids = Array.from(selection.selectedIds);
    if (ids.length === 0) return;
    const confirmed = await confirm({
      title: `Delete ${ids.length} Account${ids.length === 1 ? '' : 's'}`,
      message: `Are you sure you want to delete ${ids.length} selected account${ids.length === 1 ? '' : 's'}? Linked inboxes and their data will be removed as needed. This cannot be undone.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;

    selection.beginDelete();
    try {
      const { succeeded, failed } = await bulkDeleteWithFallback(ids, {
        delete: (id) => fetchIt<SuccessResponse>({
          apiEndPoint: `smtp/${id}`,
          httpMethod: 'delete',
        }),
        forceDelete: (id) => fetchIt<SuccessResponse>({
          apiEndPoint: `smtp/${id}`,
          httpMethod: 'delete',
          query: { force: true },
        }),
      });
      showToast(
        failed === 0
          ? `${succeeded} account${succeeded === 1 ? '' : 's'} deleted`
          : `${succeeded} deleted, ${failed} failed`,
        failed === 0 ? 'success' : 'error',
      );
      selection.finishDelete(true);
      void refresh();
    } finally {
      selection.finishDelete();
    }
  }, [confirm, fetchIt, refresh, selection]);

  const testAllReady = useCallback(async () => {
    if (testing.testingReady) return;
    const snapshot = [...readyAccounts];
    if (snapshot.length === 0) {
      showToast('No Ready accounts to test', 'info');
      return;
    }

    dispatchTesting({ type: 'ready-started', total: snapshot.length });
    let nextIndex = 0;
    let completed = 0;
    let succeeded = 0;
    const failures: ReadyTestFailure[] = [];
    const worker = async () => {
      while (nextIndex < snapshot.length) {
        const account = snapshot[nextIndex];
        nextIndex += 1;
        dispatchTesting({ type: 'account-started', id: account.id });
        try {
          const result = await fetchIt<SMTPTestResponse>({
            apiEndPoint: `smtp/${account.id}/test`,
            httpMethod: 'post',
          });
          if (result.success) succeeded += 1;
          else failures.push({
            id: account.id,
            name: account.name,
            message: result.message || 'Connection failed',
          });
        } catch (error: unknown) {
          failures.push({
            id: account.id,
            name: account.name,
            message: error instanceof Error ? error.message : 'Connection failed',
          });
        } finally {
          completed += 1;
          dispatchTesting({ type: 'account-finished', id: account.id });
          dispatchTesting({
            type: 'ready-progressed',
            progress: {
              completed,
              total: snapshot.length,
              succeeded,
              failed: failures.length,
            },
          });
        }
      }
    };

    try {
      await Promise.all(
        Array.from({ length: Math.min(3, snapshot.length) }, () => worker()),
      );
      dispatchTesting({ type: 'ready-finished', failures: [...failures] });
      showToast(
        failures.length === 0
          ? `${succeeded} Ready account${succeeded === 1 ? '' : 's'} tested successfully`
          : `${succeeded} passed, ${failures.length} failed`,
        failures.length === 0 ? 'success' : 'error',
      );
      await refresh();
    } finally {
      dispatchTesting({ type: 'ready-cleaned-up' });
    }
  }, [fetchIt, readyAccounts, refresh, testing.testingReady]);

  const create = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const provider = formData.get('provider_type') as SMTPProviderType;
    try {
      const data: SMTPAccountInput = {
        name: String(formData.get('name') ?? ''),
        from_name: String(formData.get('from_name') ?? '') || undefined,
        provider_type: provider,
      };
      if (provider === 'brevo') {
        const domain = resendConfig?.resend_verified_domain || '';
        if (domain) {
          const prefix = String(formData.get('resend_email_prefix') ?? '').trim();
          if (!prefix) {
            showToast('Enter an email prefix', 'error');
            return;
          }
          data.from_email = `${prefix}@${domain}`;
        } else {
          data.from_email = String(formData.get('from_email') ?? '');
        }
        data.host = 'resend-api';
        data.port = 443;
        data.username = 'resend';
        data.password = 'resend';
        data.encryption = 'tls';
      } else {
        data.from_email = String(formData.get('from_email') ?? '');
        data.host = String(formData.get('host') ?? '');
        data.port = Number.parseInt(String(formData.get('port')), 10);
        data.username = String(formData.get('username') ?? '');
        data.encryption = formData.get('encryption') as 'none' | 'ssl' | 'tls';
        data.auth_type = editor.createAuthType;
        if (editor.createAuthType === 'oauth2') {
          data.oauth2_client_id = String(formData.get('oauth2_client_id') ?? '');
          data.oauth2_client_secret = String(formData.get('oauth2_client_secret') ?? '');
          data.oauth2_tenant_id = String(formData.get('oauth2_tenant_id') ?? '');
          data.password = 'oauth2-managed';
        } else {
          data.password = String(formData.get('password') ?? '');
        }
        data.imap_host = String(formData.get('imap_host') ?? '') || undefined;
        data.imap_port = formData.get('imap_port')
          ? Number.parseInt(String(formData.get('imap_port')), 10)
          : undefined;
        data.imap_username = String(formData.get('imap_username') ?? '') || undefined;
        data.imap_password = String(formData.get('imap_password') ?? '') || undefined;
      }
      await fetchIt<SMTPAccount, SMTPAccountInput>({
        apiEndPoint: 'smtp/',
        httpMethod: 'post',
        reqData: data,
      });
      dispatchEditor({ type: 'create-closed' });
      showToast('Account added successfully', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [editor.createAuthType, fetchIt, refresh, resendConfig]);

  const update = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editor.editingAccount) return;
    const formData = new FormData(event.currentTarget);
    const provider = formData.get('provider_type') as SMTPProviderType;
    const password = String(formData.get('password') ?? '');
    try {
      const data: SMTPAccountInput = {
        name: String(formData.get('name') ?? ''),
        from_email: String(formData.get('from_email') ?? ''),
        from_name: String(formData.get('from_name') ?? '') || undefined,
        provider_type: provider,
        hourly_limit: Number.parseInt(String(formData.get('hourly_limit')), 10) || 100,
        daily_limit: Number.parseInt(String(formData.get('daily_limit')), 10) || 1000,
        is_active: formData.get('is_active') === 'true',
      };
      if (provider !== 'ses_api' && provider !== 'brevo') {
        data.host = String(formData.get('host') ?? '');
        data.port = Number.parseInt(String(formData.get('port')), 10);
        data.username = String(formData.get('username') ?? '');
        data.encryption = formData.get('encryption') as 'none' | 'ssl' | 'tls';
        data.auth_type = editor.editAuthType;
        if (editor.editAuthType === 'oauth2') {
          const clientId = String(formData.get('oauth2_client_id') ?? '');
          const clientSecret = String(formData.get('oauth2_client_secret') ?? '');
          const tenantId = String(formData.get('oauth2_tenant_id') ?? '');
          if (clientId) data.oauth2_client_id = clientId;
          if (clientSecret) data.oauth2_client_secret = clientSecret;
          if (tenantId) data.oauth2_tenant_id = tenantId;
        } else if (password.trim()) {
          data.password = password;
        }
        data.imap_host = String(formData.get('imap_host') ?? '') || undefined;
        data.imap_port = formData.get('imap_port')
          ? Number.parseInt(String(formData.get('imap_port')), 10)
          : undefined;
        data.imap_username = String(formData.get('imap_username') ?? '') || undefined;
        data.imap_password = String(formData.get('imap_password') ?? '') || undefined;
      }
      await fetchIt<SMTPAccount, SMTPAccountInput>({
        apiEndPoint: `smtp/${editor.editingAccount.id}`,
        httpMethod: 'put',
        reqData: data,
      });
      dispatchEditor({ type: 'edit-closed' });
      showToast('Account updated successfully', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [editor.editAuthType, editor.editingAccount, fetchIt, refresh]);

  return {
    data: { accounts, visibleAccounts, resendConfig, readyAccounts },
    status: {
      loading: accountsQuery.isValidating || configQuery.isValidating,
      initialLoad: !hadCache && accountsQuery.isLoading,
      lastUpdated,
    },
    editor: {
      ...editor,
      openCreate: (provider) => dispatchEditor({ type: 'create-opened', provider }),
      closeCreate: () => dispatchEditor({ type: 'create-closed' }),
      openEdit: (account) => dispatchEditor({ type: 'edit-opened', account }),
      closeEdit: () => dispatchEditor({ type: 'edit-closed' }),
      setCreateProvider: (provider) => dispatchEditor({
        type: 'provider-changed', mode: 'create', provider,
      }),
      setEditProvider: (provider) => dispatchEditor({
        type: 'provider-changed', mode: 'edit', provider,
      }),
      setCreateAuthType: (authType) => dispatchEditor({
        type: 'auth-changed', mode: 'create', authType,
      }),
      setEditAuthType: (authType) => dispatchEditor({
        type: 'auth-changed', mode: 'edit', authType,
      }),
      togglePassword: () => dispatchEditor({ type: 'secret-toggled', secret: 'password' }),
      toggleOauthSecret: () => dispatchEditor({ type: 'secret-toggled', secret: 'oauth' }),
      toggleImapPassword: () => dispatchEditor({ type: 'secret-toggled', secret: 'imap' }),
    },
    testing,
    search: {
      value: accountSearch,
      resultCount: visibleAccounts.length,
      totalCount: accounts.length,
      setValue: updateAccountSearch,
    },
    selection,
    actions: {
      refresh,
      test,
      detectImap,
      testImap,
      remove,
      removeSelected,
      testAllReady,
      create,
      update,
    },
  };
}
