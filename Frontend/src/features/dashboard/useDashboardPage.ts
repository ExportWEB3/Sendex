import { useCallback, useMemo, useReducer } from 'react';
import { CACHE_KEYS } from '../../cache';
import { useApiQuery } from '../../hooks/useApiQuery';
import { getCachedInitial, hasCachedData, saveToCache } from '../../initial-cache';
import type {
  Campaign,
  DashboardPageController,
  Inbox,
  QueueStatus,
  RecipientList,
  WorkerStatusResponse,
} from '../../../typefiles';

const DASHBOARD_REFRESH_INTERVAL = 30_000;

export function useDashboardPage(): DashboardPageController {
  const hadInboxCache = useMemo(() => hasCachedData(CACHE_KEYS.INBOX_LIST), []);
  const cachedInboxes = useMemo(
    () => getCachedInitial<Inbox[]>(CACHE_KEYS.INBOX_LIST, []),
    [],
  );
  const cachedCampaigns = useMemo(
    () => getCachedInitial<Campaign[]>(CACHE_KEYS.CAMPAIGN_LIST, []),
    [],
  );
  const cachedLists = useMemo(
    () => getCachedInitial<RecipientList[]>(CACHE_KEYS.LIST_LIST, []),
    [],
  );
  const cachedQueue = useMemo(
    () => getCachedInitial<QueueStatus | null>(CACHE_KEYS.QUEUE_STATUS, null),
    [],
  );
  const [lastUpdated, markUpdated] = useReducer(() => new Date(), null as Date | null);

  const inboxesQuery = useApiQuery<Inbox[]>({
    cacheKey: CACHE_KEYS.INBOX_LIST,
    endpoint: 'inboxes/',
    fallbackData: hadInboxCache ? cachedInboxes : undefined,
    refreshInterval: DASHBOARD_REFRESH_INTERVAL,
    onSuccess: (inboxes) => {
      saveToCache(CACHE_KEYS.INBOX_LIST, inboxes);
      markUpdated();
    },
  });
  const campaignsQuery = useApiQuery<Campaign[]>({
    cacheKey: CACHE_KEYS.CAMPAIGN_LIST,
    endpoint: 'campaigns',
    pagination: { offsetParameter: 'offset', pageSize: 100 },
    fallbackData: hasCachedData(CACHE_KEYS.CAMPAIGN_LIST) ? cachedCampaigns : undefined,
    refreshInterval: DASHBOARD_REFRESH_INTERVAL,
    onSuccess: (campaigns) => {
      saveToCache(CACHE_KEYS.CAMPAIGN_LIST, campaigns);
      markUpdated();
    },
  });
  const listsQuery = useApiQuery<RecipientList[]>({
    cacheKey: CACHE_KEYS.LIST_LIST,
    endpoint: 'lists/',
    pagination: { offsetParameter: 'skip', pageSize: 100 },
    fallbackData: hasCachedData(CACHE_KEYS.LIST_LIST) ? cachedLists : undefined,
    refreshInterval: DASHBOARD_REFRESH_INTERVAL,
    onSuccess: (lists) => {
      saveToCache(CACHE_KEYS.LIST_LIST, lists);
      markUpdated();
    },
  });
  const queueQuery = useApiQuery<QueueStatus>({
    cacheKey: CACHE_KEYS.QUEUE_STATUS,
    endpoint: 'queue/worker/status',
    fallbackData: hasCachedData(CACHE_KEYS.QUEUE_STATUS) && cachedQueue
      ? cachedQueue
      : undefined,
    refreshInterval: DASHBOARD_REFRESH_INTERVAL,
    onSuccess: (queue) => {
      saveToCache(CACHE_KEYS.QUEUE_STATUS, queue);
      markUpdated();
    },
  });
  const workersQuery = useApiQuery<WorkerStatusResponse>({
    cacheKey: [CACHE_KEYS.DASHBOARD, 'workers'],
    endpoint: 'system/workers/status',
    refreshInterval: DASHBOARD_REFRESH_INTERVAL,
    onSuccess: () => markUpdated(),
  });

  const inboxes = inboxesQuery.data ?? cachedInboxes;
  const campaigns = campaignsQuery.data ?? cachedCampaigns;
  const lists = listsQuery.data ?? cachedLists;
  const queue = queueQuery.data ?? cachedQueue;
  const workers = workersQuery.data?.workers ?? [];

  const summary = useMemo(() => ({
    activeInboxCount: inboxes.filter((inbox) => inbox.is_active).length,
    warmingInboxCount: inboxes.filter((inbox) => inbox.state === 'warming_up').length,
    runningCampaignCount: campaigns.filter((campaign) => campaign.status === 'running').length,
    totalRecipients: lists.reduce(
      (total, list) => total + (list.recipient_count || 0),
      0,
    ),
  }), [campaigns, inboxes, lists]);

  const mutateInboxes = inboxesQuery.mutate;
  const mutateCampaigns = campaignsQuery.mutate;
  const mutateLists = listsQuery.mutate;
  const mutateQueue = queueQuery.mutate;
  const mutateWorkers = workersQuery.mutate;
  const refresh = useCallback(async () => {
    await Promise.allSettled([
      mutateInboxes(),
      mutateCampaigns(),
      mutateLists(),
      mutateQueue(),
      mutateWorkers(),
    ]);
  }, [mutateCampaigns, mutateInboxes, mutateLists, mutateQueue, mutateWorkers]);

  return {
    data: {
      inboxes,
      campaigns,
      lists,
      queue,
      userStats: queue?.user_stats ?? null,
      workers,
    },
    status: {
      loading: inboxesQuery.isValidating
        || campaignsQuery.isValidating
        || listsQuery.isValidating
        || queueQuery.isValidating
        || workersQuery.isValidating,
      initialLoad: !hadInboxCache && inboxesQuery.isLoading,
      lastUpdated,
    },
    summary,
    refresh,
  };
}
