import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react';
import { useApiQuery } from '../../hooks/useApiQuery';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import type {
  AiGoal,
  AiTone,
  CancelAutoReplyResponse,
  CheckRepliesRequest,
  CheckRepliesResponse,
  EngineRepliesResponse,
  EngineStats,
  ImapCheckScheduleResponse,
  ImapStatusResponse,
  ProcessAutoRepliesResponse,
  ReplyCampaignsResponse,
  ReplyDashboardController,
  ReplyDashboardWorkflowAction,
  ReplyDashboardWorkflowState,
  ReplyEngine,
} from '../../../typefiles';

const PER_PAGE = 10;

function readBoolean(key: string, fallback: boolean): boolean {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value === 'true';
  } catch {
    return fallback;
  }
}

function readString<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T | null) ?? fallback;
  } catch {
    return fallback;
  }
}

function createInitialState(): ReplyDashboardWorkflowState {
  return {
    page: 1,
    repliesByCampaign: {},
    loadingReplyIds: new Set<number>(),
    expandedCampaignIds: new Set<number>(),
    checking: false,
    processing: false,
    checkResult: null,
    processResult: null,
    cancellingReplyId: null,
    ai: {
      enabled: readBoolean('ai_useAI', true),
      tone: readString<AiTone>('ai_tone', 'professional'),
      goal: readString<AiGoal>('ai_goal', 'continue_conversation'),
      customGoal: readString('ai_customGoal', ''),
      context: readString('ai_context', ''),
      panelOpen: false,
    },
    suppressSent: readBoolean('suppressSent', true),
    secondsUntilCheck: null,
    checkingStuckSeconds: 0,
  };
}

function workflowReducer(
  state: ReplyDashboardWorkflowState,
  action: ReplyDashboardWorkflowAction,
): ReplyDashboardWorkflowState {
  switch (action.type) {
    case 'page-set':
      return { ...state, page: action.page };
    case 'campaign-toggled': {
      const expandedCampaignIds = new Set(state.expandedCampaignIds);
      if (expandedCampaignIds.has(action.campaignId)) expandedCampaignIds.delete(action.campaignId);
      else expandedCampaignIds.add(action.campaignId);
      return { ...state, expandedCampaignIds };
    }
    case 'replies-loading':
      return {
        ...state,
        loadingReplyIds: new Set(state.loadingReplyIds).add(action.campaignId),
      };
    case 'replies-loaded':
      return {
        ...state,
        repliesByCampaign: {
          ...state.repliesByCampaign,
          [action.campaignId]: action.replies,
        },
      };
    case 'replies-finished': {
      const loadingReplyIds = new Set(state.loadingReplyIds);
      loadingReplyIds.delete(action.campaignId);
      return { ...state, loadingReplyIds };
    }
    case 'check-started':
      return { ...state, checking: true, checkResult: null };
    case 'check-finished':
      return {
        ...state,
        checking: false,
        checkResult: action.result ?? state.checkResult,
      };
    case 'process-started':
      return { ...state, processing: true, processResult: null };
    case 'process-finished':
      return {
        ...state,
        processing: false,
        processResult: action.result ?? state.processResult,
      };
    case 'cancel-started':
      return { ...state, cancellingReplyId: action.replyId };
    case 'cancel-finished':
      return { ...state, cancellingReplyId: null };
    case 'ai-updated':
      return { ...state, ai: { ...state.ai, ...action.patch } };
    case 'ai-panel-toggled':
      return { ...state, ai: { ...state.ai, panelOpen: !state.ai.panelOpen } };
    case 'suppress-sent-toggled':
      return { ...state, suppressSent: !state.suppressSent };
    case 'schedule-synced':
      return {
        ...state,
        secondsUntilCheck: action.secondsUntilCheck,
        checkingStuckSeconds: action.secondsUntilCheck !== null && action.secondsUntilCheck > 0
          ? 0
          : state.checkingStuckSeconds,
      };
    case 'countdown-ticked':
      if (state.secondsUntilCheck === null) return state;
      if (state.secondsUntilCheck <= 0) {
        return { ...state, secondsUntilCheck: 0, checkingStuckSeconds: state.checkingStuckSeconds + 1 };
      }
      return { ...state, secondsUntilCheck: state.secondsUntilCheck - 1 };
    default:
      return state;
  }
}

export function useReplyDashboard(engine: ReplyEngine): ReplyDashboardController {
  const { fetchIt } = useHttpFetcher();
  const [workflow, dispatch] = useReducer(
    workflowReducer,
    undefined,
    createInitialState,
  );
  const [lastUpdated, markUpdated] = useReducer(() => new Date(), null as Date | null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const campaignsQuery = useApiQuery<ReplyCampaignsResponse>({
    cacheKey: ['replies', engine, 'campaigns', workflow.page],
    endpoint: `replies/${engine}/campaigns`,
    query: { page: workflow.page, per_page: PER_PAGE },
    refreshInterval: 30_000,
    onSuccess: markUpdated,
  });
  const statsQuery = useApiQuery<EngineStats>({
    cacheKey: ['replies', engine, 'stats'],
    endpoint: `replies/${engine}/stats`,
    refreshInterval: 30_000,
    onSuccess: markUpdated,
  });
  const scheduleQuery = useApiQuery<ImapCheckScheduleResponse>({
    cacheKey: ['smtp', 'imap-check-schedule'],
    endpoint: 'smtp/imap-check-schedule',
    refreshInterval: 30_000,
    onSuccess: (schedule) => dispatch({
      type: 'schedule-synced',
      secondsUntilCheck: schedule.seconds_until_next === null
        || schedule.seconds_until_next === undefined
        ? null
        : Math.max(0, schedule.seconds_until_next),
    }),
  });
  const imapQuery = useApiQuery<ImapStatusResponse>({
    cacheKey: ['smtp', 'imap-status'],
    endpoint: 'smtp/imap-status/check',
  });

  useEffect(() => {
    countdownRef.current = setInterval(
      () => dispatch({ type: 'countdown-ticked' }),
      1_000,
    );
    return () => {
      if (countdownRef.current) clearInterval(countdownRef.current);
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem('ai_useAI', String(workflow.ai.enabled));
      localStorage.setItem('ai_tone', workflow.ai.tone);
      localStorage.setItem('ai_goal', workflow.ai.goal);
      localStorage.setItem('ai_customGoal', workflow.ai.customGoal);
      localStorage.setItem('ai_context', workflow.ai.context);
      localStorage.setItem('suppressSent', String(workflow.suppressSent));
    } catch {
      // Local storage may be unavailable.
    }
  }, [workflow.ai, workflow.suppressSent]);

  const loadCampaignReplies = useCallback(async (campaignId: number) => {
    dispatch({ type: 'replies-loading', campaignId });
    try {
      const data = await fetchIt<EngineRepliesResponse>({
        apiEndPoint: `replies/${engine}/campaign/${campaignId}`,
        httpMethod: 'get',
      });
      dispatch({ type: 'replies-loaded', campaignId, replies: data.replies });
    } catch (error: unknown) {
      console.error(error);
    } finally {
      dispatch({ type: 'replies-finished', campaignId });
    }
  }, [engine, fetchIt]);

  const toggleCampaign = useCallback((campaignId: number) => {
    const isExpanded = workflow.expandedCampaignIds.has(campaignId);
    dispatch({ type: 'campaign-toggled', campaignId });
    if (
      !isExpanded
      && !workflow.repliesByCampaign[campaignId]
      && !workflow.loadingReplyIds.has(campaignId)
    ) {
      void loadCampaignReplies(campaignId);
    }
  }, [loadCampaignReplies, workflow.expandedCampaignIds, workflow.loadingReplyIds, workflow.repliesByCampaign]);

  const mutateCampaigns = campaignsQuery.mutate;
  const mutateStats = statsQuery.mutate;
  const refresh = useCallback(async () => {
    await Promise.allSettled([mutateCampaigns(), mutateStats()]);
  }, [mutateCampaigns, mutateStats]);

  const check = useCallback(async () => {
    dispatch({ type: 'check-started' });
    try {
      const requestData: CheckRepliesRequest = {
        enabled: true,
        min_delay_minutes: 2,
        max_delay_minutes: 15,
        use_ai: workflow.ai.enabled,
        ai_tone: workflow.ai.tone,
        ai_goal: workflow.ai.goal === 'custom' ? 'custom' : workflow.ai.goal,
        custom_goal: workflow.ai.goal === 'custom' ? workflow.ai.customGoal : undefined,
        custom_message: workflow.ai.context || undefined,
      };
      const result = await fetchIt<CheckRepliesResponse, CheckRepliesRequest>({
        apiEndPoint: `replies/${engine}/check`,
        httpMethod: 'post',
        reqData: requestData,
      });
      dispatch({ type: 'check-finished', result });
      await refresh();
      await Promise.allSettled(
        Array.from(workflow.expandedCampaignIds, loadCampaignReplies),
      );
    } catch (error: unknown) {
      console.error(error);
      dispatch({ type: 'check-finished' });
    }
  }, [engine, fetchIt, loadCampaignReplies, refresh, workflow.ai, workflow.expandedCampaignIds]);

  const process = useCallback(async () => {
    dispatch({ type: 'process-started' });
    try {
      const result = await fetchIt<ProcessAutoRepliesResponse>({
        apiEndPoint: 'campaigns/process-auto-replies',
        httpMethod: 'post',
      });
      dispatch({ type: 'process-finished', result });
      await refresh();
    } catch (error: unknown) {
      console.error(error);
      dispatch({ type: 'process-finished' });
    }
  }, [fetchIt, refresh]);

  const cancel = useCallback(async (replyId: number) => {
    dispatch({ type: 'cancel-started', replyId });
    try {
      await fetchIt<CancelAutoReplyResponse>({
        apiEndPoint: `campaigns/auto-replies/${replyId}/cancel`,
        httpMethod: 'post',
      });
      const campaignId = Object.entries(workflow.repliesByCampaign)
        .find(([, replies]) => replies.some((reply) => reply.id === replyId))?.[0];
      if (campaignId) await loadCampaignReplies(Number(campaignId));
    } catch (error: unknown) {
      console.error('Failed to cancel reply', error);
    } finally {
      dispatch({ type: 'cancel-finished' });
    }
  }, [fetchIt, loadCampaignReplies, workflow.repliesByCampaign]);

  const visibleRepliesByCampaign = useMemo(() => {
    if (!workflow.suppressSent) return workflow.repliesByCampaign;
    return Object.fromEntries(
      Object.entries(workflow.repliesByCampaign).map(([campaignId, replies]) => [
        campaignId,
        replies.filter((reply) => reply.status !== 'sent'),
      ]),
    );
  }, [workflow.repliesByCampaign, workflow.suppressSent]);

  const totalPages = campaignsQuery.data?.total_pages ?? 1;
  const goTo = useCallback((page: number) => {
    dispatch({ type: 'page-set', page: Math.max(1, Math.min(totalPages, page)) });
  }, [totalPages]);

  const secondsUntilCheck = workflow.secondsUntilCheck;
  const schedulerActive = scheduleQuery.error
    ? false
    : scheduleQuery.data?.scheduler_active ?? false;

  return {
    data: {
      campaigns: campaignsQuery.data?.campaigns ?? [],
      stats: statsQuery.data ?? null,
      repliesByCampaign: visibleRepliesByCampaign,
    },
    status: {
      loading: campaignsQuery.isValidating || statsQuery.isValidating,
      initialLoad: campaignsQuery.isLoading || statsQuery.isLoading,
      lastUpdated,
      checking: workflow.checking,
      processing: workflow.processing,
      cancellingReplyId: workflow.cancellingReplyId,
      imapActive: imapQuery.error ? false : imapQuery.data?.imap_active ?? null,
    },
    pagination: {
      page: workflow.page,
      totalPages,
      total: campaignsQuery.data?.total ?? 0,
      perPage: PER_PAGE,
      goTo,
      previous: () => goTo(workflow.page - 1),
      next: () => goTo(workflow.page + 1),
    },
    replies: {
      loadingIds: workflow.loadingReplyIds,
      expandedIds: workflow.expandedCampaignIds,
      toggleCampaign,
    },
    ai: {
      ...workflow.ai,
      setEnabled: (enabled) => dispatch({ type: 'ai-updated', patch: { enabled } }),
      setTone: (tone) => dispatch({ type: 'ai-updated', patch: { tone } }),
      setGoal: (goal) => dispatch({ type: 'ai-updated', patch: { goal } }),
      setCustomGoal: (customGoal) => dispatch({ type: 'ai-updated', patch: { customGoal } }),
      setContext: (context) => dispatch({ type: 'ai-updated', patch: { context } }),
      togglePanel: () => dispatch({ type: 'ai-panel-toggled' }),
    },
    scheduler: {
      active: schedulerActive,
      lastCheck: scheduleQuery.data?.last_check ?? null,
      secondsUntilCheck,
      checkingStuckSeconds: workflow.checkingStuckSeconds,
      progress: secondsUntilCheck !== null && schedulerActive
        ? Math.max(0, Math.min(100, ((300 - secondsUntilCheck) / 300) * 100))
        : 0,
    },
    filters: {
      suppressSent: workflow.suppressSent,
      toggleSuppressSent: () => dispatch({ type: 'suppress-sent-toggled' }),
    },
    results: {
      check: workflow.checkResult,
      process: workflow.processResult,
    },
    actions: { refresh, check, process, cancel },
  };
}
