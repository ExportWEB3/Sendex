import { useCallback, useMemo, useReducer } from 'react';
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
  AddRecipientInput,
  AddRecipientsRequest,
  AddRecipientsResponse,
  CreateRecipientListRequest,
  ListRecipientsResponse,
  ListsPageController,
  ListsWorkflowAction,
  ListsWorkflowState,
  RecipientList,
  SuccessResponse,
} from '../../../typefiles';

const PAGE_SIZE = 50;
const INITIAL_WORKFLOW_STATE: ListsWorkflowState = {
  createOpen: false,
  addRecipientsListId: null,
  recipientView: null,
  recipients: [],
  recipientTotal: 0,
  recipientPage: 0,
  recipientLoading: false,
  recipientSearch: '',
};

function listsWorkflowReducer(
  state: ListsWorkflowState,
  action: ListsWorkflowAction,
): ListsWorkflowState {
  switch (action.type) {
    case 'create-opened':
      return { ...state, createOpen: true };
    case 'create-closed':
      return { ...state, createOpen: false };
    case 'add-opened':
      return { ...state, addRecipientsListId: action.listId };
    case 'add-closed':
      return { ...state, addRecipientsListId: null };
    case 'recipients-opened':
      return {
        ...state,
        recipientView: action.view,
        recipientPage: 0,
        recipientSearch: '',
      };
    case 'recipients-closed':
      return { ...state, recipientView: null };
    case 'recipients-load-started':
      return { ...state, recipientLoading: true };
    case 'recipients-loaded':
      return {
        ...state,
        recipients: action.recipients,
        recipientTotal: action.total,
        recipientPage: action.page,
        recipientLoading: false,
      };
    case 'recipients-load-finished':
      return { ...state, recipientLoading: false };
    case 'recipient-search-changed':
      return { ...state, recipientSearch: action.search };
    default:
      return state;
  }
}

function parseRecipients(value: string): AddRecipientInput[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line): AddRecipientInput | null => {
      const [email, firstName, lastName] = line.split(',').map((part) => part.trim());
      if (!email?.includes('@')) return null;
      return {
        email: email.toLowerCase(),
        first_name: firstName || undefined,
        last_name: lastName || undefined,
      };
    })
    .filter((recipient): recipient is AddRecipientInput => recipient !== null);
}

export function useListsPage(): ListsPageController {
  const { fetchIt } = useHttpFetcher();
  const confirm = useConfirm();
  const hadCache = useMemo(() => hasCachedData(CACHE_KEYS.LIST_LIST), []);
  const cachedLists = useMemo(
    () => getCachedInitial<RecipientList[]>(CACHE_KEYS.LIST_LIST, []),
    [],
  );
  const [lastUpdated, markUpdated] = useReducer(() => new Date(), null as Date | null);
  const [workflow, dispatch] = useReducer(listsWorkflowReducer, INITIAL_WORKFLOW_STATE);

  const listsQuery = useApiQuery<RecipientList[]>({
    cacheKey: CACHE_KEYS.LIST_LIST,
    endpoint: 'lists/',
    fallbackData: hadCache ? cachedLists : undefined,
    onSuccess: (lists) => {
      saveToCache(CACHE_KEYS.LIST_LIST, lists);
      markUpdated();
    },
    errorNotification: { enabled: true },
  });
  const lists = listsQuery.data ?? cachedLists;
  const mutateLists = listsQuery.mutate;
  const refresh = useCallback(async () => {
    await mutateLists();
  }, [mutateLists]);

  const listIds = useMemo(() => lists.map(({ id }) => id), [lists]);
  const selection = useBulkSelection(listIds);

  const loadRecipients = useCallback(async (listId: number, page: number) => {
    dispatch({ type: 'recipients-load-started' });
    try {
      const data = await fetchIt<ListRecipientsResponse>({
        apiEndPoint: `lists/${listId}/recipients`,
        httpMethod: 'get',
        query: { skip: page * PAGE_SIZE, limit: PAGE_SIZE },
      });
      dispatch({
        type: 'recipients-loaded',
        recipients: data.recipients,
        total: data.total,
        page,
      });
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      dispatch({ type: 'recipients-load-finished' });
    }
  }, [fetchIt]);

  const openRecipients = useCallback((listId: number, listName: string) => {
    dispatch({ type: 'recipients-opened', view: { listId, listName } });
    void loadRecipients(listId, 0);
  }, [loadRecipients]);

  const changePage = useCallback((page: number) => {
    if (!workflow.recipientView) return;
    void loadRecipients(workflow.recipientView.listId, page);
  }, [loadRecipients, workflow.recipientView]);

  const create = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const requestData: CreateRecipientListRequest = {
      name: String(formData.get('name') ?? ''),
      description: String(formData.get('description') ?? '') || undefined,
    };
    try {
      await fetchIt<RecipientList, CreateRecipientListRequest>({
        apiEndPoint: 'lists/',
        httpMethod: 'post',
        reqData: requestData,
      });
      dispatch({ type: 'create-closed' });
      showToast('List created successfully', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt, refresh]);

  const remove = useCallback(async (id: number) => {
    const confirmed = await confirm({
      title: 'Delete List',
      message: 'Are you sure you want to delete this recipient list? All recipients in this list will be removed.',
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;

    try {
      await fetchIt<SuccessResponse>({ apiEndPoint: `lists/${id}`, httpMethod: 'delete' });
      showToast('List removed successfully', 'success');
      void refresh();
    } catch (error: unknown) {
      const message = getErrorMessage(error);
      const hasLinkedData = ['campaign', 'linked', 'Cannot delete', 'foreign key', 'referenced']
        .some((fragment) => message.includes(fragment));
      if (!hasLinkedData) {
        showToast(message, 'error');
        return;
      }

      const forceConfirmed = await confirm({
        title: 'List Has Linked Data',
        message: 'This list is referenced by campaigns or other data.\n\nDo you want to force delete and remove all linked data?',
        confirmText: 'Force Delete',
        variant: 'danger',
      });
      if (!forceConfirmed) return;
      try {
        await fetchIt<SuccessResponse>({
          apiEndPoint: `lists/${id}`,
          httpMethod: 'delete',
          query: { force: true },
        });
        showToast('List and linked data deleted', 'success');
        void refresh();
      } catch (forceError: unknown) {
        showToast(getErrorMessage(forceError), 'error');
      }
    }
  }, [confirm, fetchIt, refresh]);

  const removeSelected = useCallback(async () => {
    const ids = Array.from(selection.selectedIds);
    if (ids.length === 0) return;
    const confirmed = await confirm({
      title: `Delete ${ids.length} List${ids.length === 1 ? '' : 's'}`,
      message: `Are you sure you want to delete ${ids.length} selected list${ids.length === 1 ? '' : 's'}? All recipients and any linked data will be removed. This cannot be undone.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;

    selection.beginDelete();
    try {
      const { succeeded, failed } = await bulkDeleteWithFallback(ids, {
        delete: (id) => fetchIt<SuccessResponse>({
          apiEndPoint: `lists/${id}`,
          httpMethod: 'delete',
        }),
        forceDelete: (id) => fetchIt<SuccessResponse>({
          apiEndPoint: `lists/${id}`,
          httpMethod: 'delete',
          query: { force: true },
        }),
      });
      showToast(
        failed === 0
          ? `${succeeded} list${succeeded === 1 ? '' : 's'} deleted`
          : `${succeeded} deleted, ${failed} failed`,
        failed === 0 ? 'success' : 'error',
      );
      selection.finishDelete(true);
      void refresh();
    } finally {
      selection.finishDelete();
    }
  }, [confirm, fetchIt, refresh, selection]);

  const addRecipients = useCallback(async (
    event: React.FormEvent<HTMLFormElement>,
    listId: number,
  ) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const recipients = parseRecipients(String(formData.get('recipients') ?? ''));
    if (recipients.length === 0) {
      showToast('No valid email addresses found', 'error');
      return;
    }

    try {
      const requestData: AddRecipientsRequest = { recipients };
      const result = await fetchIt<AddRecipientsResponse, AddRecipientsRequest>({
        apiEndPoint: `lists/${listId}/recipients`,
        httpMethod: 'post',
        reqData: requestData,
      });
      dispatch({ type: 'add-closed' });
      showToast(`${result.added} recipients added`, 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [fetchIt, refresh]);

  const visibleRecipients = useMemo(() => {
    const search = workflow.recipientSearch.trim().toLowerCase();
    if (!search) return workflow.recipients;
    return workflow.recipients.filter((recipient) => (
      recipient.email.toLowerCase().includes(search)
      || recipient.first_name?.toLowerCase().includes(search)
      || recipient.last_name?.toLowerCase().includes(search)
    ));
  }, [workflow.recipientSearch, workflow.recipients]);

  return {
    data: { lists },
    status: {
      loading: listsQuery.isValidating,
      initialLoad: !hadCache && listsQuery.isLoading,
      lastUpdated,
    },
    dialogs: {
      createOpen: workflow.createOpen,
      addRecipientsListId: workflow.addRecipientsListId,
      openCreate: () => dispatch({ type: 'create-opened' }),
      closeCreate: () => dispatch({ type: 'create-closed' }),
      openAddRecipients: (listId) => dispatch({ type: 'add-opened', listId }),
      closeAddRecipients: () => dispatch({ type: 'add-closed' }),
    },
    recipients: {
      view: workflow.recipientView,
      visible: visibleRecipients,
      total: workflow.recipientTotal,
      page: workflow.recipientPage,
      loading: workflow.recipientLoading,
      search: workflow.recipientSearch,
      pageSize: PAGE_SIZE,
      open: openRecipients,
      close: () => dispatch({ type: 'recipients-closed' }),
      changePage,
      setSearch: (search) => dispatch({ type: 'recipient-search-changed', search }),
    },
    selection,
    actions: {
      refresh,
      create,
      remove,
      removeSelected,
      addRecipients,
    },
  };
}
