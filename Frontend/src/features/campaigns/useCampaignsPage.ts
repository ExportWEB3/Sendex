import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from 'react';
import { showToast } from '../../components/toast-store';
import { useConfirm } from '../../components/useConfirm';
import { CACHE_KEYS } from '../../cache';
import { getErrorMessage } from '../../http/api-error';
import { useApiQuery } from '../../hooks/useApiQuery';
import { useBulkSelection } from '../../hooks/useBulkSelection';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import { bulkDeleteWithFallback } from '../../utils/bulk-delete';
import { getCachedInitial, hasCachedData, saveToCache } from '../../initial-cache';
import {
  campaignActivityReducer,
  campaignEditorReducer,
  campaignOperationsReducer,
  campaignRecipientsReducer,
  INITIAL_CAMPAIGN_ACTIVITY_STATE,
  INITIAL_CAMPAIGN_EDITOR_STATE,
  INITIAL_CAMPAIGN_OPERATIONS_STATE,
  INITIAL_CAMPAIGN_RECIPIENTS_STATE,
} from './campaigns-state';
import type {
  AttachmentMeta,
  AttachmentUploadResponse,
  Campaign,
  CampaignActivityResponse,
  CampaignInput,
  CampaignPauseAllResponse,
  CampaignRecipientPreview,
  CampaignRecipientsResponse,
  CampaignStartAllResponse,
  CampaignStats,
  CampaignsPageController,
  EmailTemplate,
  Inbox,
  RecipientList,
  SMTPAccount,
  StatusFilter,
  SuccessResponse,
  TemplateOption,
} from '../../../typefiles';

const CAMPAIGN_REFRESH_INTERVAL = 20_000;
const ACTIVITY_REFRESH_INTERVAL = 6_000;
const TERMINAL_TICK_INTERVAL = 1_000;
const RECIPIENTS_PER_PAGE = 100;

function collectTemplateData(formData: FormData): Record<string, unknown> {
  const templateData: Record<string, unknown> = {};
  const perTemplate: Record<string, Record<string, string>> = {};

  for (const [key, value] of formData.entries()) {
    if (typeof value !== 'string' || !value.trim()) continue;
    if (key.startsWith('tplvar_per_')) {
      const rest = key.replace('tplvar_per_', '');
      const lastUnderscore = rest.lastIndexOf('_');
      if (lastUnderscore > 0) {
        const variableName = rest.substring(0, lastUnderscore);
        const templateId = rest.substring(lastUnderscore + 1);
        if (!perTemplate[templateId]) perTemplate[templateId] = {};
        perTemplate[templateId][variableName] = value.trim();
      }
    } else if (key.startsWith('tplvar_')) {
      templateData[key.replace('tplvar_', '')] = value.trim();
    }
  }

  if (Object.keys(perTemplate).length > 0) {
    templateData.__per_template__ = perTemplate;
  }
  return templateData;
}

function parseInboxIds(formData: FormData): number[] {
  const inboxIds = String(formData.get('inbox_ids') ?? '');
  return inboxIds
    .split(',')
    .map((id) => Number.parseInt(id.trim(), 10))
    .filter((id) => !Number.isNaN(id));
}

export function useCampaignsPage(): CampaignsPageController {
  const { fetchIt } = useHttpFetcher();
  const confirm = useConfirm();

  const cachedCampaigns = useMemo(
    () => getCachedInitial<Campaign[]>(CACHE_KEYS.CAMPAIGN_LIST, []),
    [],
  );
  const cachedLists = useMemo(
    () => getCachedInitial<RecipientList[]>(CACHE_KEYS.LIST_LIST, []),
    [],
  );
  const cachedInboxes = useMemo(
    () => getCachedInitial<Inbox[]>(CACHE_KEYS.INBOX_LIST, []),
    [],
  );
  const cachedSmtpAccounts = useMemo(
    () => getCachedInitial<SMTPAccount[]>(CACHE_KEYS.SMTP_LIST, []),
    [],
  );
  const hadCampaignCache = useMemo(() => hasCachedData(CACHE_KEYS.CAMPAIGN_LIST), []);
  const [lastUpdated, markUpdated] = useReducer(() => new Date(), null as Date | null);

  const cacheCampaigns = useCallback((campaigns: Campaign[]) => {
    saveToCache(CACHE_KEYS.CAMPAIGN_LIST, campaigns);
    markUpdated();
  }, []);
  const cacheLists = useCallback((lists: RecipientList[]) => {
    saveToCache(CACHE_KEYS.LIST_LIST, lists);
    markUpdated();
  }, []);
  const cacheInboxes = useCallback((inboxes: Inbox[]) => {
    saveToCache(CACHE_KEYS.INBOX_LIST, inboxes);
    markUpdated();
  }, []);
  const cacheSmtpAccounts = useCallback((accounts: SMTPAccount[]) => {
    saveToCache(CACHE_KEYS.SMTP_LIST, accounts);
    markUpdated();
  }, []);

  const campaignsQuery = useApiQuery<Campaign[]>({
    cacheKey: CACHE_KEYS.CAMPAIGN_LIST,
    endpoint: 'campaigns',
    fallbackData: hadCampaignCache ? cachedCampaigns : undefined,
    refreshInterval: (latestCampaigns) => (
      latestCampaigns?.some((campaign) => (
        campaign.status === 'running'
        || campaign.status === 'paused'
        || campaign.effective_status === 'cooldown'
      ))
        ? CAMPAIGN_REFRESH_INTERVAL
        : 0
    ),
    onSuccess: cacheCampaigns,
    errorNotification: { enabled: true },
  });
  const listsQuery = useApiQuery<RecipientList[]>({
    cacheKey: CACHE_KEYS.LIST_LIST,
    endpoint: 'lists/',
    fallbackData: hasCachedData(CACHE_KEYS.LIST_LIST) ? cachedLists : undefined,
    onSuccess: cacheLists,
  });
  const inboxesQuery = useApiQuery<Inbox[]>({
    cacheKey: CACHE_KEYS.INBOX_LIST,
    endpoint: 'inboxes/',
    fallbackData: hasCachedData(CACHE_KEYS.INBOX_LIST) ? cachedInboxes : undefined,
    onSuccess: cacheInboxes,
  });
  const smtpAccountsQuery = useApiQuery<SMTPAccount[]>({
    cacheKey: CACHE_KEYS.SMTP_LIST,
    endpoint: 'smtp/',
    fallbackData: hasCachedData(CACHE_KEYS.SMTP_LIST) ? cachedSmtpAccounts : undefined,
    onSuccess: cacheSmtpAccounts,
  });
  const templatesQuery = useApiQuery<EmailTemplate[]>({
    cacheKey: CACHE_KEYS.TEMPLATES,
    endpoint: 'email-templates',
    onSuccess: (loadedTemplates) => saveToCache(CACHE_KEYS.TEMPLATES, loadedTemplates),
  });

  const mutateCampaigns = campaignsQuery.mutate;
  const mutateLists = listsQuery.mutate;
  const mutateInboxes = inboxesQuery.mutate;
  const mutateSmtpAccounts = smtpAccountsQuery.mutate;
  const mutateTemplates = templatesQuery.mutate;

  const campaigns = campaignsQuery.data ?? cachedCampaigns;
  const lists = listsQuery.data ?? cachedLists;
  const inboxes = inboxesQuery.data ?? cachedInboxes;
  const smtpAccounts = smtpAccountsQuery.data ?? cachedSmtpAccounts;
  const templates = useMemo<TemplateOption[]>(
    () => templatesQuery.data ?? [],
    [templatesQuery.data],
  );

  const refresh = useCallback(async () => {
    try {
      await Promise.all([
        mutateCampaigns(),
        mutateLists(),
        mutateInboxes(),
        mutateSmtpAccounts(),
        mutateTemplates(),
      ]);
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [
    mutateCampaigns,
    mutateInboxes,
    mutateLists,
    mutateSmtpAccounts,
    mutateTemplates,
  ]);

  const patchCampaign = useCallback((id: number, patch: Partial<Campaign>) => {
    void mutateCampaigns((currentCampaigns) => {
      const updated = (currentCampaigns ?? campaigns).map((campaign) => (
        campaign.id === id ? { ...campaign, ...patch } : campaign
      ));
      saveToCache(CACHE_KEYS.CAMPAIGN_LIST, updated);
      return updated;
    }, { revalidate: false });
  }, [campaigns, mutateCampaigns]);

  const [editorState, editorDispatch] = useReducer(
    campaignEditorReducer,
    INITIAL_CAMPAIGN_EDITOR_STATE,
  );
  const [recipientState, recipientDispatch] = useReducer(
    campaignRecipientsReducer,
    INITIAL_CAMPAIGN_RECIPIENTS_STATE,
  );
  const [activityState, activityDispatch] = useReducer(
    campaignActivityReducer,
    INITIAL_CAMPAIGN_ACTIVITY_STATE,
  );
  const [operationsState, operationsDispatch] = useReducer(
    campaignOperationsReducer,
    INITIAL_CAMPAIGN_OPERATIONS_STATE,
  );

  const campaignIds = useMemo(() => campaigns.map((campaign) => campaign.id), [campaigns]);
  const selection = useBulkSelection(campaignIds);

  const terminalElementsRef = useRef<Record<number, HTMLDivElement | null>>({});
  const terminalIntervalsRef = useRef<Record<number, ReturnType<typeof setInterval>>>({});
  const terminalTickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const serverTimeOffsetRef = useRef(0);

  const fetchActivity = useCallback(async (campaignId: number) => {
    try {
      const data = await fetchIt<CampaignActivityResponse>({
        apiEndPoint: `campaigns/${campaignId}/activity`,
        httpMethod: 'get',
      });
      activityDispatch({
        type: 'updated',
        campaignId,
        data: {
          logs: data.logs,
          status: data.status,
          schedule: data.schedule,
        },
      });
      serverTimeOffsetRef.current = data.server_time - Date.now() / 1000;
      patchCampaign(campaignId, {
        total_sent: data.total_sent,
        total_failed: data.total_failed,
        total_recipients: data.total_recipients,
      });
      requestAnimationFrame(() => {
        const terminal = terminalElementsRef.current[campaignId];
        if (terminal) terminal.scrollTop = terminal.scrollHeight;
      });
    } catch {
      // A terminal is supplemental telemetry; normal page polling remains authoritative.
    }
  }, [fetchIt, patchCampaign]);

  const closeTerminal = useCallback((campaignId: number) => {
    activityDispatch({ type: 'closed', campaignId });
    if (terminalIntervalsRef.current[campaignId]) {
      clearInterval(terminalIntervalsRef.current[campaignId]);
      delete terminalIntervalsRef.current[campaignId];
    }
    terminalElementsRef.current[campaignId] = null;
  }, []);

  const toggleTerminal = useCallback((campaignId: number) => {
    if (activityState.terminals[campaignId]) {
      closeTerminal(campaignId);
      return;
    }

    activityDispatch({ type: 'opened', campaignId });
    void fetchActivity(campaignId);
    terminalIntervalsRef.current[campaignId] = setInterval(
      () => void fetchActivity(campaignId),
      ACTIVITY_REFRESH_INTERVAL,
    );
  }, [activityState.terminals, closeTerminal, fetchActivity]);

  const openTerminalCount = Object.keys(activityState.terminals).length;
  useEffect(() => {
    if (openTerminalCount > 0 && !terminalTickRef.current) {
      terminalTickRef.current = setInterval(
        () => activityDispatch({ type: 'tick' }),
        TERMINAL_TICK_INTERVAL,
      );
    } else if (openTerminalCount === 0 && terminalTickRef.current) {
      clearInterval(terminalTickRef.current);
      terminalTickRef.current = null;
    }
  }, [openTerminalCount]);

  useEffect(() => () => {
    Object.values(terminalIntervalsRef.current).forEach(clearInterval);
    if (terminalTickRef.current) clearInterval(terminalTickRef.current);
  }, []);

  const setTerminalElement = useCallback((
    campaignId: number,
    element: HTMLDivElement | null,
  ) => {
    terminalElementsRef.current[campaignId] = element;
  }, []);

  const uploadAttachment = useCallback(async (file: File): Promise<AttachmentMeta> => {
    const data = new FormData();
    data.append('file', file);
    return fetchIt<AttachmentUploadResponse, FormData>({
      apiEndPoint: 'campaigns/upload-attachment',
      httpMethod: 'post',
      reqData: data,
    });
  }, [fetchIt]);

  const selectFiles = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) return;

    editorDispatch({ type: 'set-uploading', uploading: true });
    try {
      for (const file of files) {
        const attachment = await uploadAttachment(file);
        editorDispatch({ type: 'append-attachment', attachment });
      }
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      editorDispatch({ type: 'set-uploading', uploading: false });
      event.target.value = '';
    }
  }, [uploadAttachment]);

  const uploadPendingFiles = useCallback(async (): Promise<AttachmentMeta[] | null> => {
    const attachments = [...editorState.uploadedAttachments];
    editorDispatch({ type: 'set-uploading', uploading: true });
    try {
      for (const file of editorState.pendingFiles) {
        attachments.push(await uploadAttachment(file));
      }
      return attachments;
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
      return null;
    } finally {
      editorDispatch({ type: 'set-uploading', uploading: false });
    }
  }, [editorState.pendingFiles, editorState.uploadedAttachments, uploadAttachment]);

  const buildCampaignInput = useCallback((
    formData: FormData,
    attachments: AttachmentMeta[],
    includeTimezone: boolean,
  ): CampaignInput => {
    const replyTo = String(formData.get('reply_to_email') ?? '').trim();
    const templateData = collectTemplateData(formData);
    return {
      name: String(formData.get('name') ?? ''),
      subject: String(formData.get('subject') ?? ''),
      body_html: String(formData.get('body_html') ?? ''),
      list_id: Number.parseInt(String(formData.get('list_id')), 10),
      inbox_ids: parseInboxIds(formData),
      ...(includeTimezone
        ? { send_timezone: String(formData.get('send_timezone') || 'US/Eastern') }
        : {}),
      ...(replyTo ? { reply_to_email: replyTo } : {}),
      ...(attachments.length > 0 ? { attachments } : {}),
      ...(Object.keys(templateData).length > 0 ? { template_data: templateData } : {}),
      ...(editorState.multiTemplateIds.length >= 1
        ? { template_ids: editorState.multiTemplateIds }
        : {}),
    };
  }, [editorState.multiTemplateIds]);

  const submit = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const attachments = await uploadPendingFiles();
    if (!attachments) return;

    if (!editorState.editingCampaign && editorState.multiTemplateIds.length === 1) {
      const template = templates.find(({ id }) => id === editorState.multiTemplateIds[0]);
      for (const attachment of template?.attachments ?? []) {
        if (!attachments.some(({ stored_name }) => stored_name === attachment.stored_name)) {
          attachments.push(attachment);
        }
      }
    }

    const editingCampaign = editorState.editingCampaign;
    const requestData = buildCampaignInput(formData, attachments, !editingCampaign);
    try {
      await fetchIt<Campaign, CampaignInput>({
        apiEndPoint: editingCampaign ? `campaigns/${editingCampaign.id}` : 'campaigns',
        httpMethod: editingCampaign ? 'put' : 'post',
        reqData: requestData,
      });
      editorDispatch({ type: 'save-complete' });
      showToast(
        editingCampaign ? 'Campaign updated successfully' : 'Campaign created successfully',
        'success',
      );
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [
    buildCampaignInput,
    editorState.editingCampaign,
    editorState.multiTemplateIds,
    fetchIt,
    refresh,
    templates,
    uploadPendingFiles,
  ]);

  const loadRecipients = useCallback(async (
    campaignId: number,
    filter: StatusFilter = '',
    page = 1,
  ) => {
    recipientDispatch({ type: 'load-started' });
    try {
      const data = await fetchIt<CampaignRecipientsResponse>({
        apiEndPoint: `campaigns/${campaignId}/recipients`,
        httpMethod: 'get',
        query: { status_filter: filter || undefined, page },
      });
      recipientDispatch({
        type: 'load-succeeded',
        recipients: data.recipients,
        total: data.total,
        statusCounts: data.status_counts,
        page,
      });
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      recipientDispatch({ type: 'load-finished' });
    }
  }, [fetchIt]);

  const openRecipients = useCallback(async (campaign: Campaign) => {
    recipientDispatch({ type: 'open', campaign });
    await loadRecipients(campaign.id, '', 1);
  }, [loadRecipients]);

  const changeRecipientFilter = useCallback(async (filter: StatusFilter) => {
    recipientDispatch({ type: 'filter-changed', filter });
    if (recipientState.campaign) {
      await loadRecipients(recipientState.campaign.id, filter, 1);
    }
  }, [loadRecipients, recipientState.campaign]);

  const changeRecipientPage = useCallback(async (page: number) => {
    if (!recipientState.campaign) return;
    recipientDispatch({ type: 'page-changed', page });
    await loadRecipients(recipientState.campaign.id, recipientState.statusFilter, page);
  }, [loadRecipients, recipientState.campaign, recipientState.statusFilter]);

  const visibleRecipients = useMemo(() => {
    const normalizedSearch = recipientState.emailSearch.trim().toLowerCase();
    if (!normalizedSearch) return recipientState.recipients;
    return recipientState.recipients.filter((recipient) => (
      recipient.email.toLowerCase().includes(normalizedSearch)
      || recipient.first_name?.toLowerCase().includes(normalizedSearch)
    ));
  }, [recipientState.emailSearch, recipientState.recipients]);

  const start = useCallback(async (id: number) => {
    try {
      await fetchIt<Campaign>({ apiEndPoint: `campaigns/${id}/start`, httpMethod: 'post' });
      showToast('Campaign started successfully', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt, refresh]);

  const pause = useCallback(async (id: number) => {
    try {
      await fetchIt<Campaign>({ apiEndPoint: `campaigns/${id}/pause`, httpMethod: 'post' });
      showToast('Campaign paused', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt, refresh]);

  const resume = useCallback(async (id: number) => {
    try {
      await fetchIt<Campaign>({ apiEndPoint: `campaigns/${id}/resume`, httpMethod: 'post' });
      showToast('Campaign resumed', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt, refresh]);

  const cancel = useCallback(async (id: number) => {
    const confirmed = await confirm({
      title: 'Cancel Campaign',
      message: 'Are you sure you want to cancel this campaign? This will stop all pending emails.',
      confirmText: 'Cancel Campaign',
      variant: 'warning',
    });
    if (!confirmed) return;

    try {
      await fetchIt<Campaign>({ apiEndPoint: `campaigns/${id}/cancel`, httpMethod: 'post' });
      showToast('Campaign cancelled', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [confirm, fetchIt, refresh]);

  const remove = useCallback(async (id: number) => {
    const confirmed = await confirm({
      title: 'Delete Campaign',
      message: 'Are you sure you want to delete this campaign? This action cannot be undone.',
      confirmText: 'Delete Campaign',
      variant: 'danger',
    });
    if (!confirmed) return;

    try {
      await fetchIt<SuccessResponse>({
        apiEndPoint: `campaigns/${id}`,
        httpMethod: 'delete',
        query: { force: true },
      });
      showToast('Campaign deleted successfully', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [confirm, fetchIt, refresh]);

  const removeSelected = useCallback(async () => {
    const ids = Array.from(selection.selectedIds);
    if (ids.length === 0) return;
    const confirmed = await confirm({
      title: `Delete ${ids.length} Campaign${ids.length === 1 ? '' : 's'}`,
      message: `Are you sure you want to delete ${ids.length} selected campaign${ids.length === 1 ? '' : 's'}? This cannot be undone.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;

    selection.beginDelete();
    try {
      const { succeeded, failed } = await bulkDeleteWithFallback(ids, {
        delete: (id) => fetchIt<SuccessResponse>({
          apiEndPoint: `campaigns/${id}`,
          httpMethod: 'delete',
        }),
        forceDelete: (id) => fetchIt<SuccessResponse>({
          apiEndPoint: `campaigns/${id}`,
          httpMethod: 'delete',
          query: { force: true },
        }),
      });
      showToast(
        failed === 0
          ? `${succeeded} campaign${succeeded === 1 ? '' : 's'} deleted`
          : `${succeeded} deleted, ${failed} failed`,
        failed === 0 ? 'success' : 'error',
      );
      selection.finishDelete(true);
      void refresh();
    } finally {
      selection.finishDelete();
    }
  }, [confirm, fetchIt, refresh, selection]);

  const activeRunningCount = useMemo(
    () => campaigns.filter((campaign) => campaign.status === 'running').length,
    [campaigns],
  );
  const eligibleStartCount = useMemo(
    () => campaigns.filter((campaign) => ['draft', 'scheduled'].includes(campaign.status)).length,
    [campaigns],
  );

  const pauseAll = useCallback(async () => {
    if (activeRunningCount < 2 || operationsState.pausingAll) return;
    const confirmed = await confirm({
      title: `Pause ${activeRunningCount} Campaigns`,
      message: `This will pause ${activeRunningCount} running campaigns. Queued emails are returned to pending and will resume when you resume each campaign.`,
      confirmText: 'Pause All',
      variant: 'warning',
    });
    if (!confirmed) return;

    operationsDispatch({ type: 'pausing-all', active: true });
    try {
      const response = await fetchIt<CampaignPauseAllResponse>({
        apiEndPoint: 'campaigns/pause-all',
        httpMethod: 'post',
      });
      showToast(
        response.failed_count === 0
          ? `${response.paused_count} campaign${response.paused_count === 1 ? '' : 's'} paused`
          : `${response.paused_count} paused · ${response.failed_count} failed`,
        response.failed_count === 0 ? 'success' : 'error',
      );
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      operationsDispatch({ type: 'pausing-all', active: false });
    }
  }, [activeRunningCount, confirm, fetchIt, operationsState.pausingAll, refresh]);

  const startAll = useCallback(async () => {
    if (eligibleStartCount === 0 || operationsState.startingAll) return;
    const confirmed = await confirm({
      title: `Start ${eligibleStartCount} Campaign${eligibleStartCount === 1 ? '' : 's'}`,
      message: `This will start ${eligibleStartCount} campaign${eligibleStartCount === 1 ? '' : 's'} at once. First batches are staggered automatically so they don't all fire at the same time.`,
      confirmText: 'Start All',
      variant: 'warning',
    });
    if (!confirmed) return;

    operationsDispatch({ type: 'starting-all', active: true });
    try {
      const response = await fetchIt<CampaignStartAllResponse>({
        apiEndPoint: 'campaigns/start-all',
        httpMethod: 'post',
      });
      showToast(
        response.failed_count === 0
          ? `${response.started_count} campaign${response.started_count === 1 ? '' : 's'} started`
          : `${response.started_count} started · ${response.failed_count} failed`,
        response.failed_count === 0 ? 'success' : 'error',
      );
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      operationsDispatch({ type: 'starting-all', active: false });
    }
  }, [confirm, eligibleStartCount, fetchIt, operationsState.startingAll, refresh]);

  const viewStats = useCallback(async (id: number) => {
    try {
      const stats = await fetchIt<CampaignStats>({
        apiEndPoint: `campaigns/${id}/stats`,
        httpMethod: 'get',
      });
      operationsDispatch({ type: 'stats-opened', stats });
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt]);

  const viewRecipientPreview = useCallback(async (
    campaignId: number,
    campaignRecipientId: number,
  ) => {
    operationsDispatch({ type: 'recipient-preview-loading', active: true });
    try {
      const preview = await fetchIt<CampaignRecipientPreview>({
        apiEndPoint: `campaigns/${campaignId}/recipient/${campaignRecipientId}/preview`,
        httpMethod: 'get',
      });
      operationsDispatch({ type: 'recipient-preview-opened', preview });
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      operationsDispatch({ type: 'recipient-preview-loading', active: false });
    }
  }, [fetchIt]);

  const loading = campaignsQuery.isValidating
    || listsQuery.isValidating
    || inboxesQuery.isValidating
    || smtpAccountsQuery.isValidating
    || templatesQuery.isValidating;

  return {
    data: { campaigns, lists, inboxes, smtpAccounts, templates },
    status: {
      loading,
      initialLoad: !hadCampaignCache && campaignsQuery.isLoading,
      lastUpdated,
      startingAll: operationsState.startingAll,
      pausingAll: operationsState.pausingAll,
    },
    editor: {
      ...editorState,
      openCreate: () => editorDispatch({ type: 'open-create' }),
      openEdit: (campaign) => editorDispatch({ type: 'open-edit', campaign }),
      close: () => editorDispatch({ type: 'close' }),
      toggleTemplate: (templateId) => editorDispatch({ type: 'toggle-template', templateId }),
      selectFiles,
      removeAttachment: (index) => editorDispatch({ type: 'remove-attachment', index }),
    },
    recipients: {
      ...recipientState,
      visibleRecipients,
      totalPages: Math.ceil(recipientState.total / RECIPIENTS_PER_PAGE),
      open: openRecipients,
      close: () => recipientDispatch({ type: 'close' }),
      changeFilter: changeRecipientFilter,
      changePage: changeRecipientPage,
      setSearch: (search) => recipientDispatch({ type: 'search-changed', search }),
    },
    selection,
    activity: {
      ...activityState,
      toggle: toggleTerminal,
      close: closeTerminal,
      setTerminalElement,
      serverNow: () => Date.now() / 1000 + serverTimeOffsetRef.current,
    },
    stats: {
      selected: operationsState.stats,
      close: () => operationsDispatch({ type: 'stats-closed' }),
    },
    preview: {
      selected: operationsState.recipientPreview,
      loading: operationsState.recipientPreviewLoading,
      open: viewRecipientPreview,
      close: () => operationsDispatch({ type: 'recipient-preview-closed' }),
    },
    actions: {
      refresh,
      start,
      pause,
      resume,
      cancel,
      remove,
      removeSelected,
      startAll,
      pauseAll,
      submit,
      viewStats,
    },
  };
}
