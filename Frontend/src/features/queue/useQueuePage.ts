import { useCallback, useMemo, useReducer } from 'react';
import { CACHE_KEYS } from '../../cache';
import { showToast } from '../../components/toast-store';
import { useConfirm } from '../../components/useConfirm';
import { getErrorMessage } from '../../http/api-error';
import { useApiQuery } from '../../hooks/useApiQuery';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import { getCachedInitial, hasCachedData, saveToCache } from '../../initial-cache';
import type {
  ImapStatusResponse,
  MessageResponse,
  ProcessQueueResponse,
  QueueOperationsAction,
  QueueOperationsState,
  QueuePageController,
  QueueStatus,
  SuccessResponse,
  SystemHealth,
  SystemResetRequest,
  SystemResetResponse,
  SystemResyncResponse,
  WorkerStatusResponse,
} from '../../../typefiles';

const QUEUE_REFRESH_INTERVAL = 15_000;
const INITIAL_OPERATIONS_STATE: QueueOperationsState = {
  resetting: false,
  resyncing: false,
};

function queueOperationsReducer(
  state: QueueOperationsState,
  action: QueueOperationsAction,
): QueueOperationsState {
  switch (action.type) {
    case 'resetting':
      return { ...state, resetting: action.active };
    case 'resyncing':
      return { ...state, resyncing: action.active };
    default:
      return state;
  }
}

export function useQueuePage(): QueuePageController {
  const { fetchIt } = useHttpFetcher();
  const confirm = useConfirm();
  const hadQueueCache = useMemo(() => hasCachedData(CACHE_KEYS.QUEUE_STATUS), []);
  const cachedQueue = useMemo(
    () => getCachedInitial<QueueStatus | null>(CACHE_KEYS.QUEUE_STATUS, null),
    [],
  );
  const [lastUpdated, markUpdated] = useReducer(() => new Date(), null as Date | null);
  const [operations, dispatchOperations] = useReducer(
    queueOperationsReducer,
    INITIAL_OPERATIONS_STATE,
  );

  const queueQuery = useApiQuery<QueueStatus>({
    cacheKey: CACHE_KEYS.QUEUE_STATUS,
    endpoint: 'queue/worker/status',
    fallbackData: hadQueueCache && cachedQueue ? cachedQueue : undefined,
    refreshInterval: QUEUE_REFRESH_INTERVAL,
    onSuccess: (queue) => {
      saveToCache(CACHE_KEYS.QUEUE_STATUS, queue);
      markUpdated();
    },
    errorNotification: { enabled: true },
  });
  const healthQuery = useApiQuery<SystemHealth>({
    cacheKey: ['queue-page', 'health'],
    endpoint: 'system/health',
    refreshInterval: QUEUE_REFRESH_INTERVAL,
    onSuccess: () => markUpdated(),
  });
  const workersQuery = useApiQuery<WorkerStatusResponse>({
    cacheKey: [CACHE_KEYS.DASHBOARD, 'workers'],
    endpoint: 'system/workers/status',
    refreshInterval: QUEUE_REFRESH_INTERVAL,
    onSuccess: () => markUpdated(),
  });
  const imapQuery = useApiQuery<ImapStatusResponse>({
    cacheKey: ['queue-page', 'imap-status'],
    endpoint: 'smtp/imap-status/check',
  });

  const mutateQueue = queueQuery.mutate;
  const mutateHealth = healthQuery.mutate;
  const mutateWorkers = workersQuery.mutate;
  const refresh = useCallback(async () => {
    await Promise.allSettled([
      mutateQueue(),
      mutateHealth(),
      mutateWorkers(),
    ]);
  }, [mutateHealth, mutateQueue, mutateWorkers]);

  const runWorkerAction = useCallback(async (
    endpoint: string,
    successMessage: string,
  ) => {
    try {
      await fetchIt<SuccessResponse>({ apiEndPoint: endpoint, httpMethod: 'post' });
      showToast(successMessage, 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt, refresh]);

  const startWorker = useCallback(
    () => runWorkerAction('queue/worker/start', 'Worker started successfully'),
    [runWorkerAction],
  );
  const stopWorker = useCallback(
    () => runWorkerAction('queue/worker/stop', 'Worker stopped'),
    [runWorkerAction],
  );

  const processNow = useCallback(async () => {
    try {
      const result = await fetchIt<ProcessQueueResponse>({
        apiEndPoint: 'queue/worker/process-now',
        httpMethod: 'post',
        query: { batch_size: 10 },
      });
      showToast(`${result.processed} items processed`, 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt, refresh]);

  const resetSystem = useCallback(async () => {
    const confirmed = await confirm({
      title: 'System Reset',
      message: 'This will:\n\n• Clear all email queues\n• Reset stuck recipients\n• Reset pending auto-replies\n\nAre you sure you want to proceed?',
      confirmText: 'Reset System',
      variant: 'danger',
    });
    if (!confirmed) return;

    dispatchOperations({ type: 'resetting', active: true });
    try {
      const requestData: SystemResetRequest = {
        clear_queue: true,
        reset_pending_recipients: true,
        reset_auto_replies: true,
      };
      const result = await fetchIt<SystemResetResponse, SystemResetRequest>({
        apiEndPoint: 'system/reset',
        httpMethod: 'post',
        reqData: requestData,
      });
      showToast(
        result.success
          ? `System reset complete: ${result.actions_taken.join(', ')}`
          : `Reset had errors: ${result.errors.join(', ')}`,
        result.success ? 'success' : 'error',
      );
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      dispatchOperations({ type: 'resetting', active: false });
    }
  }, [confirm, fetchIt, refresh]);

  const resetStats = useCallback(async () => {
    const confirmed = await confirm({
      title: 'Reset Stats',
      message: 'This will reset all cumulative stats, clear the dead letter queue, and zero out all counters.\n\nAre you sure?',
      confirmText: 'Reset Stats',
      variant: 'danger',
    });
    if (!confirmed) return;

    try {
      await fetchIt<MessageResponse>({
        apiEndPoint: 'queue/stats/reset',
        httpMethod: 'delete',
        query: { confirm: true },
      });
      showToast('All stats reset to zero', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [confirm, fetchIt, refresh]);

  const resync = useCallback(async () => {
    dispatchOperations({ type: 'resyncing', active: true });
    try {
      const result = await fetchIt<SystemResyncResponse>({
        apiEndPoint: 'system/resync',
        httpMethod: 'post',
      });
      showToast(
        result.success
          ? `Resync complete: ${result.recipients_requeued} recipients requeued, ${result.orphaned_processing_cleared} orphans cleared`
          : `Resync had errors: ${result.errors.join(', ')}`,
        result.success ? 'success' : 'error',
      );
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      dispatchOperations({ type: 'resyncing', active: false });
    }
  }, [fetchIt, refresh]);

  const queue = queueQuery.data ?? cachedQueue;
  return {
    data: {
      queue,
      health: healthQuery.data ?? null,
      userStats: queue?.user_stats ?? null,
      imapActive: imapQuery.data?.imap_active ?? (imapQuery.error ? false : null),
      workers: workersQuery.data?.workers ?? [],
    },
    status: {
      loading: queueQuery.isValidating || healthQuery.isValidating || workersQuery.isValidating,
      initialLoad: !hadQueueCache && queueQuery.isLoading,
      lastUpdated,
      ...operations,
    },
    actions: {
      refresh,
      startWorker,
      stopWorker,
      processNow,
      resetSystem,
      resetStats,
      resync,
    },
  };
}
