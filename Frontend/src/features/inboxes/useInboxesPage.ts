import { useCallback, useEffect, useMemo, useReducer } from 'react';
import { CACHE_KEYS } from '../../cache';
import { showToast } from '../../components/toast-store';
import { useConfirm } from '../../components/useConfirm';
import { getErrorMessage } from '../../http/api-error';
import { useApiQuery } from '../../hooks/useApiQuery';
import { useBulkSelection } from '../../hooks/useBulkSelection';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import { bulkDeleteWithFallback } from '../../utils/bulk-delete';
import { getCachedInitial, hasCachedData, saveToCache } from '../../initial-cache';
import type {
  FallbackReplyEmailResponse,
  FixImapResponse,
  FleetQuotaResponse,
  Inbox,
  InboxDiagnosticResponse,
  InboxGroup,
  InboxInput,
  InboxesPageController,
  InboxesWorkflowAction,
  InboxesWorkflowState,
  InboxSendTestRequest,
  InboxSendTestResponse,
  MessageResponse,
  MxStatus,
  SetupReplyToRequest,
  SetupReplyToResponse,
  SMTPAccount,
  SuccessResponse,
} from '../../../typefiles';

const INITIAL_WORKFLOW_STATE: InboxesWorkflowState = {
  mx: { statuses: {}, checking: {} },
  fix: {
    inboxId: null,
    state: 'idle',
    message: '',
    replyToEmail: '',
    replyToPassword: '',
  },
  diagnosis: {
    inboxId: null,
    loading: false,
    result: null,
    showAppPasswordSteps: false,
  },
  replyEditor: { inboxId: null, email: '', saving: false },
  creation: {
    open: false,
    creating: false,
    showImapOverride: false,
    selectedSmtpId: null,
  },
  testSend: { inbox: null, recipient: '', sending: false },
};

function workflowReducer(
  state: InboxesWorkflowState,
  action: InboxesWorkflowAction,
): InboxesWorkflowState {
  switch (action.type) {
    case 'mx-checking':
      return {
        ...state,
        mx: {
          ...state.mx,
          checking: { ...state.mx.checking, [action.inboxId]: action.checking },
        },
      };
    case 'mx-checked':
      return {
        ...state,
        mx: {
          ...state.mx,
          statuses: { ...state.mx.statuses, [action.inboxId]: action.status },
        },
      };
    case 'mx-cleared': {
      const statuses = { ...state.mx.statuses };
      delete statuses[action.inboxId];
      return { ...state, mx: { ...state.mx, statuses } };
    }
    case 'fix-opened':
      return {
        ...state,
        fix: {
          inboxId: action.inboxId,
          state: 'trying',
          message: 'Trying to fix IMAP automatically...',
          replyToEmail: '',
          replyToPassword: '',
        },
      };
    case 'fix-updated':
      return { ...state, fix: { ...state.fix, ...action.patch } };
    case 'fix-closed':
      return { ...state, fix: INITIAL_WORKFLOW_STATE.fix };
    case 'diagnosis-started':
      return {
        ...state,
        diagnosis: {
          ...state.diagnosis,
          inboxId: action.inboxId,
          loading: true,
          result: null,
        },
      };
    case 'diagnosis-finished':
      return {
        ...state,
        diagnosis: { ...state.diagnosis, loading: false, result: action.result },
      };
    case 'diagnosis-closed':
      return { ...state, diagnosis: INITIAL_WORKFLOW_STATE.diagnosis };
    case 'app-password-steps-toggled':
      return {
        ...state,
        diagnosis: {
          ...state.diagnosis,
          showAppPasswordSteps: !state.diagnosis.showAppPasswordSteps,
        },
      };
    case 'reply-edit-opened':
      return {
        ...state,
        replyEditor: { inboxId: action.inboxId, email: action.email, saving: false },
      };
    case 'reply-edit-updated':
      return { ...state, replyEditor: { ...state.replyEditor, email: action.email } };
    case 'reply-saving':
      return { ...state, replyEditor: { ...state.replyEditor, saving: action.saving } };
    case 'reply-edit-closed':
      return { ...state, replyEditor: INITIAL_WORKFLOW_STATE.replyEditor };
    case 'create-opened':
      return { ...state, creation: { ...INITIAL_WORKFLOW_STATE.creation, open: true } };
    case 'create-updated':
      return { ...state, creation: { ...state.creation, ...action.patch } };
    case 'create-closed':
      return { ...state, creation: INITIAL_WORKFLOW_STATE.creation };
    case 'test-opened':
      return { ...state, testSend: { inbox: action.inbox, recipient: '', sending: false } };
    case 'test-recipient-updated':
      return { ...state, testSend: { ...state.testSend, recipient: action.recipient } };
    case 'test-sending':
      return { ...state, testSend: { ...state.testSend, sending: action.sending } };
    case 'test-closed':
      return { ...state, testSend: INITIAL_WORKFLOW_STATE.testSend };
    default:
      return state;
  }
}

function diagnosticFailure(message: string): InboxDiagnosticResponse {
  return {
    inbox_id: 0,
    inbox_email: '',
    imap_host: null,
    imap_configured: false,
    reply_enabled: false,
    reply_to_email: null,
    emails_in_inbox: 0,
    reply_emails: 0,
    bounce_emails: 0,
    read_receipt_emails: 0,
    matched_to_campaigns: 0,
    issues: [message],
    email_samples: [],
    campaigns_using_inbox: [],
    all_campaign_recipients: [],
    mx_info: null,
    imap_connection: null,
  };
}

export function useInboxesPage(): InboxesPageController {
  const { fetchIt } = useHttpFetcher();
  const confirm = useConfirm();
  const hadCache = useMemo(() => hasCachedData(CACHE_KEYS.INBOX_LIST), []);
  const cachedInboxes = useMemo(
    () => getCachedInitial<Inbox[]>(CACHE_KEYS.INBOX_LIST, []),
    [],
  );
  const cachedSmtpAccounts = useMemo(
    () => getCachedInitial<SMTPAccount[]>(CACHE_KEYS.SMTP_LIST, []),
    [],
  );
  const [lastUpdated, markUpdated] = useReducer(() => new Date(), null as Date | null);
  const [workflow, dispatch] = useReducer(workflowReducer, INITIAL_WORKFLOW_STATE);

  const inboxesQuery = useApiQuery<Inbox[]>({
    cacheKey: CACHE_KEYS.INBOX_LIST,
    endpoint: 'inboxes/',
    fallbackData: hadCache ? cachedInboxes : undefined,
    onSuccess: (inboxes) => {
      saveToCache(CACHE_KEYS.INBOX_LIST, inboxes);
      markUpdated();
    },
    errorNotification: { enabled: true },
  });
  const smtpQuery = useApiQuery<SMTPAccount[]>({
    cacheKey: CACHE_KEYS.SMTP_LIST,
    endpoint: 'smtp/',
    fallbackData: hasCachedData(CACHE_KEYS.SMTP_LIST) ? cachedSmtpAccounts : undefined,
    onSuccess: (accounts) => saveToCache(CACHE_KEYS.SMTP_LIST, accounts),
  });
  const fleetQuery = useApiQuery<FleetQuotaResponse>({
    cacheKey: ['inboxes', 'fleet-quota'],
    endpoint: 'inboxes/fleet-quota',
  });

  const inboxes = inboxesQuery.data ?? cachedInboxes;
  const smtpAccounts = smtpQuery.data ?? cachedSmtpAccounts;
  const selectedSmtp = smtpAccounts.find(({ id }) => id === workflow.creation.selectedSmtpId);
  const selectedSmtpIsApi = selectedSmtp?.provider_type === 'ses_api'
    || selectedSmtp?.provider_type === 'brevo';
  const inboxIds = useMemo(() => inboxes.map(({ id }) => id), [inboxes]);
  const selection = useBulkSelection(inboxIds);

  const mutateInboxes = inboxesQuery.mutate;
  const mutateSmtp = smtpQuery.mutate;
  const mutateFleet = fleetQuery.mutate;
  const refresh = useCallback(async () => {
    await Promise.allSettled([mutateInboxes(), mutateSmtp(), mutateFleet()]);
  }, [mutateFleet, mutateInboxes, mutateSmtp]);

  const checkMx = useCallback(async (inboxId: number) => {
    dispatch({ type: 'mx-checking', inboxId, checking: true });
    try {
      const status = await fetchIt<MxStatus>({
        apiEndPoint: `inboxes/${inboxId}/check-mx`,
        httpMethod: 'post',
      });
      dispatch({ type: 'mx-checked', inboxId, status });
    } catch {
      // MX checks are advisory and must not block the page.
    } finally {
      dispatch({ type: 'mx-checking', inboxId, checking: false });
    }
  }, [fetchIt]);

  useEffect(() => {
    inboxes.forEach((inbox) => {
      if (!workflow.mx.statuses[inbox.id] && !workflow.mx.checking[inbox.id]) {
        void checkMx(inbox.id);
      }
    });
  }, [checkMx, inboxes, workflow.mx.checking, workflow.mx.statuses]);

  const openFix = useCallback(async (inboxId: number) => {
    dispatch({ type: 'fix-opened', inboxId });
    try {
      const result = await fetchIt<FixImapResponse>({
        apiEndPoint: `inboxes/${inboxId}/fix-imap`,
        httpMethod: 'post',
      });
      if (result.success) {
        dispatch({
          type: 'fix-updated',
          patch: {
            state: 'done',
            message: `Fixed! Now checking ${result.imap_host} for replies.`,
          },
        });
        await refresh();
        dispatch({ type: 'mx-cleared', inboxId });
      } else {
        dispatch({
          type: 'fix-updated',
          patch: { state: 'failed', message: result.message },
        });
      }
    } catch (error: unknown) {
      dispatch({
        type: 'fix-updated',
        patch: { state: 'failed', message: getErrorMessage(error) },
      });
    }

    try {
      const fallback = await fetchIt<FallbackReplyEmailResponse>({
        apiEndPoint: 'system/fallback-reply-email',
        httpMethod: 'get',
      });
      if (fallback.configured && fallback.email) {
        dispatch({ type: 'fix-updated', patch: { replyToEmail: fallback.email } });
      }
    } catch {
      // The user can enter a mailbox manually when no fallback is available.
    }
  }, [fetchIt, refresh]);

  const setupReplyTo = useCallback(async () => {
    const { inboxId, replyToEmail, replyToPassword } = workflow.fix;
    if (!inboxId || !replyToEmail || !replyToPassword) return;
    dispatch({
      type: 'fix-updated',
      patch: { state: 'saving', message: 'Setting up Reply-To and testing IMAP...' },
    });
    try {
      const requestData: SetupReplyToRequest = {
        reply_to_email: replyToEmail,
        reply_to_password: replyToPassword,
      };
      const result = await fetchIt<SetupReplyToResponse, SetupReplyToRequest>({
        apiEndPoint: `inboxes/${inboxId}/setup-reply-to`,
        httpMethod: 'post',
        reqData: requestData,
      });
      dispatch({
        type: 'fix-updated',
        patch: {
          state: result.success ? 'done' : 'reply-to',
          message: result.message,
        },
      });
      if (result.success) {
        showToast('Reply-To configured! Replies will now be detected.', 'success');
        await refresh();
        dispatch({ type: 'mx-cleared', inboxId });
      }
    } catch (error: unknown) {
      dispatch({
        type: 'fix-updated',
        patch: { state: 'reply-to', message: getErrorMessage(error) },
      });
    }
  }, [fetchIt, refresh, workflow.fix]);

  const diagnose = useCallback(async (inboxId: number) => {
    dispatch({ type: 'diagnosis-started', inboxId });
    try {
      const result = await fetchIt<InboxDiagnosticResponse>({
        apiEndPoint: `inboxes/${inboxId}/diagnose-replies`,
        httpMethod: 'post',
      });
      dispatch({ type: 'diagnosis-finished', result });
    } catch (error: unknown) {
      dispatch({
        type: 'diagnosis-finished',
        result: diagnosticFailure(getErrorMessage(error)),
      });
    }
  }, [fetchIt]);

  const saveReplyTo = useCallback(async (inboxId: number, emailOverride?: string) => {
    const email = (emailOverride ?? workflow.replyEditor.email).trim();
    dispatch({ type: 'reply-saving', saving: true });
    try {
      const requestData: InboxInput = { reply_to_email: email || null };
      await fetchIt<Inbox, InboxInput>({
        apiEndPoint: `inboxes/${inboxId}`,
        httpMethod: 'put',
        reqData: requestData,
      });
      showToast(email ? `Reply-To set to ${email}` : 'Reply-To removed', 'success');
      dispatch({ type: 'reply-edit-closed' });
      await refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      dispatch({ type: 'reply-saving', saving: false });
    }
  }, [fetchIt, refresh, workflow.replyEditor.email]);

  const closeTestSend = useCallback(() => {
    if (workflow.testSend.sending) return;
    dispatch({ type: 'test-closed' });
  }, [workflow.testSend.sending]);

  const submitTestSend = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const { inbox, recipient } = workflow.testSend;
    if (!inbox || !recipient.trim()) return;
    dispatch({ type: 'test-sending', sending: true });
    try {
      const requestData: InboxSendTestRequest = { to_email: recipient.trim() };
      const result = await fetchIt<InboxSendTestResponse, InboxSendTestRequest>({
        apiEndPoint: `inboxes/${inbox.id}/send-test`,
        httpMethod: 'post',
        reqData: requestData,
      });
      showToast(result.message, 'success');
      dispatch({ type: 'test-closed' });
      await refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      dispatch({ type: 'test-sending', sending: false });
    }
  }, [fetchIt, refresh, workflow.testSend]);

  const runWarmupAction = useCallback(async (
    id: number,
    action: 'pause' | 'resume',
  ) => {
    try {
      await fetchIt<MessageResponse>({
        apiEndPoint: `warmup/${action}/${id}`,
        httpMethod: 'post',
      });
      showToast(`Warm-up ${action === 'pause' ? 'paused' : 'resumed'}`, 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt, refresh]);

  const remove = useCallback(async (id: number) => {
    const confirmed = await confirm({
      title: 'Delete Inbox',
      message: 'Are you sure you want to delete this inbox? This action cannot be undone.',
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await fetchIt<SuccessResponse>({ apiEndPoint: `inboxes/${id}`, httpMethod: 'delete' });
      showToast('Inbox removed successfully', 'success');
      void refresh();
    } catch (error: unknown) {
      const message = getErrorMessage(error);
      if (!message.includes('linked') && !message.includes('Cannot delete')) {
        showToast(message, 'error');
        return;
      }
      const match = message.match(/(\d+)\s*linked/);
      const forceConfirmed = await confirm({
        title: 'Inbox Has Linked Data',
        message: `This inbox currently has ${match?.[1] ?? 'some'} linked records.\n\nDo you want to force delete and remove all linked data (emails, metrics, warmup threads, etc.)?`,
        confirmText: 'Force Delete',
        variant: 'danger',
      });
      if (!forceConfirmed) return;
      try {
        await fetchIt<SuccessResponse>({
          apiEndPoint: `inboxes/${id}`,
          httpMethod: 'delete',
          query: { force: true },
        });
        showToast('Inbox and linked data deleted', 'success');
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
      title: `Delete ${ids.length} Inbox${ids.length === 1 ? '' : 'es'}`,
      message: `Are you sure you want to delete ${ids.length} selected inbox${ids.length === 1 ? '' : 'es'}? Linked data (emails, metrics, warmup threads, etc.) will be removed as needed. This cannot be undone.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;

    selection.beginDelete();
    try {
      const { succeeded, failed } = await bulkDeleteWithFallback(ids, {
        delete: (id) => fetchIt<SuccessResponse>({
          apiEndPoint: `inboxes/${id}`,
          httpMethod: 'delete',
        }),
        forceDelete: (id) => fetchIt<SuccessResponse>({
          apiEndPoint: `inboxes/${id}`,
          httpMethod: 'delete',
          query: { force: true },
        }),
      });
      showToast(
        failed === 0
          ? `${succeeded} inbox${succeeded === 1 ? '' : 'es'} deleted`
          : `${succeeded} deleted, ${failed} failed`,
        failed === 0 ? 'success' : 'error',
      );
      selection.finishDelete(true);
      void refresh();
    } finally {
      selection.finishDelete();
    }
  }, [confirm, fetchIt, refresh, selection]);

  const create = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const smtpAccountId = Number.parseInt(String(formData.get('smtp_account_id')), 10);
    const smtpAccount = smtpAccounts.find(({ id }) => id === smtpAccountId);
    const isApi = smtpAccount?.provider_type === 'ses_api'
      || smtpAccount?.provider_type === 'brevo';
    const email = isApi
      ? smtpAccount?.from_email || ''
      : String(formData.get('email') ?? '');
    const imapHost = String(formData.get('imap_host') ?? '').trim();
    const imapUsername = String(formData.get('imap_username') ?? '').trim();
    const imapPassword = String(formData.get('imap_password') ?? '').trim();
    const replyToEmail = String(formData.get('reply_to_email') ?? '').trim();

    dispatch({ type: 'create-updated', patch: { creating: true } });
    try {
      const requestData: InboxInput = {
        email,
        smtp_account_id: smtpAccountId,
        group: formData.get('group') as InboxGroup,
        ...(imapHost ? { imap_host: imapHost } : {}),
        ...(imapHost ? {
          imap_port: Number.parseInt(String(formData.get('imap_port')), 10) || 993,
        } : {}),
        ...(imapUsername ? { imap_username: imapUsername } : {}),
        ...(imapPassword ? { imap_password: imapPassword } : {}),
        ...(replyToEmail ? { reply_to_email: replyToEmail } : {}),
      };
      const result = await fetchIt<Inbox, InboxInput>({
        apiEndPoint: 'inboxes/',
        httpMethod: 'post',
        reqData: requestData,
      });
      dispatch({ type: 'create-closed' });
      if (isApi || result.is_resend_inbox) {
        showToast('Inbox created! Sending via API — no SMTP ports needed.', 'success');
      } else if (result.imap_auto_detected) {
        const sourceLabels: Record<string, string> = {
          smtp_account: 'from SMTP account settings',
          mx_record: 'via MX record lookup',
          provider_map: 'via known provider',
          auto_discovery: 'via auto-discovery',
          mx_record_untested: 'via MX records (untested)',
        };
        const source = result.imap_detection_source;
        const label = source ? sourceLabels[source] || source : 'automatically';
        showToast(`Inbox created! IMAP auto-detected ${label}: ${result.imap_host}`, 'success');
      } else if (result.has_imap) {
        showToast('Inbox created with IMAP configured', 'success');
      } else {
        showToast('Inbox created (no IMAP - send only)', 'success');
      }
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      dispatch({ type: 'create-updated', patch: { creating: false } });
    }
  }, [fetchIt, refresh, smtpAccounts]);

  return {
    data: {
      inboxes,
      smtpAccounts,
      fleetQuota: fleetQuery.data ?? null,
      selectedSmtp,
      selectedSmtpIsApi,
    },
    status: {
      loading: inboxesQuery.isValidating || smtpQuery.isValidating || fleetQuery.isValidating,
      initialLoad: !hadCache && inboxesQuery.isLoading,
      lastUpdated,
    },
    mx: workflow.mx,
    fix: {
      ...workflow.fix,
      open: openFix,
      close: () => dispatch({ type: 'fix-closed' }),
      setEmail: (replyToEmail) => dispatch({
        type: 'fix-updated', patch: { replyToEmail },
      }),
      setPassword: (replyToPassword) => dispatch({
        type: 'fix-updated', patch: { replyToPassword },
      }),
      submit: setupReplyTo,
    },
    diagnosis: {
      ...workflow.diagnosis,
      run: diagnose,
      close: () => dispatch({ type: 'diagnosis-closed' }),
      toggleAppPasswordSteps: () => dispatch({ type: 'app-password-steps-toggled' }),
    },
    replyEditor: {
      ...workflow.replyEditor,
      open: (inboxId, email = '') => dispatch({ type: 'reply-edit-opened', inboxId, email }),
      close: () => dispatch({ type: 'reply-edit-closed' }),
      setEmail: (email) => dispatch({ type: 'reply-edit-updated', email }),
      save: saveReplyTo,
    },
    creation: {
      ...workflow.creation,
      openModal: () => dispatch({ type: 'create-opened' }),
      closeModal: () => dispatch({ type: 'create-closed' }),
      setSmtpId: (selectedSmtpId) => dispatch({
        type: 'create-updated', patch: { selectedSmtpId },
      }),
      toggleImapOverride: () => dispatch({
        type: 'create-updated',
        patch: { showImapOverride: !workflow.creation.showImapOverride },
      }),
    },
    testSend: {
      ...workflow.testSend,
      open: (inbox) => dispatch({ type: 'test-opened', inbox }),
      close: closeTestSend,
      setRecipient: (recipient) => dispatch({ type: 'test-recipient-updated', recipient }),
      submit: submitTestSend,
    },
    selection,
    actions: {
      refresh,
      pauseWarmup: (id) => runWarmupAction(id, 'pause'),
      resumeWarmup: (id) => runWarmupAction(id, 'resume'),
      remove,
      removeSelected,
      create,
    },
  };
}
