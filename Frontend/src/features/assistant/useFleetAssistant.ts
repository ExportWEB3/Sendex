import { useCallback, useEffect, useReducer, useRef } from 'react';
import { getErrorMessage } from '../../http/api-error';
import { useApiQuery } from '../../hooks/useApiQuery';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import type {
  AssistantAction,
  AssistantActionIdsRequest,
  AssistantChatRequest,
  AssistantConversation,
  AssistantConversationIdRequest,
  AssistantConversationResponse,
  AssistantConversationsResponse,
  AssistantMessage,
  AssistantPrepareDeleteRequest,
  AssistantReply,
  AssistantRunToolRequest,
  FleetAssistantAction,
  FleetAssistantController,
  FleetAssistantState,
  SuccessResponse,
} from '../../../typefiles';

const ACTIVE_CONVERSATION_KEY = 'fleet_assistant_conversation_id';
const EMPTY_CONVERSATIONS: AssistantConversation[] = [];
const WELCOME_MESSAGE = `Hi! I'm **Fleet Assistant** 👋

I remember your conversations, can guide you through FLEETCTRL-X, and can safely help delete campaigns, inboxes, sending accounts, recipient lists, or templates after you confirm.

What can I help you with?`;

function welcomeMessage(): AssistantMessage {
  return {
    id: 0,
    localId: 'welcome',
    role: 'assistant',
    content: WELCOME_MESSAGE,
    action: null,
  };
}

const INITIAL_STATE: FleetAssistantState = {
  isOpen: false,
  messages: [welcomeMessage()],
  activeConversationId: null,
  input: '',
  isLoading: false,
  conversationLoading: false,
  actionLoadingId: null,
  hasUnread: false,
  showBubble: false,
  bubbleDismissed: false,
  showImport: false,
  showHistory: false,
  fileToImport: null,
  filesToImport: [],
  templateFilesToImport: [],
  deleteSelections: {},
  importingBundle: false,
  previewVariableCategory: null,
};

function reconcileActionStatus(
  messages: AssistantMessage[],
  action?: AssistantAction | null,
): AssistantMessage[] {
  if (action && ['bundle_import_review', 'bundle_variables', 'create_campaigns_plan'].includes(action.type)) {
    return messages.map((message) => {
      const existingAction = message.action;
      if (!existingAction || ![
        'bundle_import_review',
        'bundle_variables',
        'create_campaigns_offer',
        'create_campaigns_plan',
      ].includes(existingAction.type)) return message;
      if (existingAction.status === 'completed') return message;
      return { ...message, action: { ...existingAction, status: 'cancelled' } };
    });
  }

  if (!action?.action_ids?.length) return messages;
  const actionKey = [...action.action_ids].sort().join(',');
  return messages.map((message) => {
    const existingAction = message.action;
    if (!existingAction?.action_ids?.length) return message;
    if ([...existingAction.action_ids].sort().join(',') !== actionKey) return message;
    return { ...message, action: { ...existingAction, status: action.status } };
  });
}

function resetConversationState(state: FleetAssistantState): FleetAssistantState {
  return {
    ...state,
    messages: [welcomeMessage()],
    activeConversationId: null,
    input: '',
    isLoading: false,
    conversationLoading: false,
    actionLoadingId: null,
    showHistory: false,
    showImport: false,
    fileToImport: null,
    filesToImport: [],
    templateFilesToImport: [],
    deleteSelections: {},
    previewVariableCategory: null,
  };
}

function assistantReducer(
  state: FleetAssistantState,
  action: FleetAssistantAction,
): FleetAssistantState {
  switch (action.type) {
    case 'assistant-opened':
      return {
        ...state,
        isOpen: true,
        hasUnread: false,
        showBubble: false,
        bubbleDismissed: true,
      };
    case 'assistant-closed':
      return { ...state, isOpen: false, previewVariableCategory: null };
    case 'bubble-shown':
      return { ...state, showBubble: action.visible };
    case 'bubble-dismissed':
      return { ...state, showBubble: false, bubbleDismissed: true };
    case 'input-updated':
      return { ...state, input: action.input };
    case 'history-set':
      return {
        ...state,
        showHistory: action.visible,
        showImport: action.visible ? false : state.showImport,
      };
    case 'conversation-loading':
      return { ...state, conversationLoading: true };
    case 'conversation-loaded':
      return {
        ...state,
        activeConversationId: action.conversationId,
        messages: action.messages.length ? action.messages : [welcomeMessage()],
        conversationLoading: false,
        showHistory: false,
        showImport: false,
        fileToImport: null,
        filesToImport: [],
        templateFilesToImport: [],
        deleteSelections: {},
        previewVariableCategory: null,
      };
    case 'conversation-load-failed':
      return {
        ...state,
        activeConversationId: null,
        messages: [welcomeMessage()],
        conversationLoading: false,
      };
    case 'conversation-remembered':
      return { ...state, activeConversationId: action.conversationId };
    case 'conversation-started':
      return resetConversationState(state);
    case 'send-started':
      return {
        ...state,
        messages: [
          ...state.messages.filter((message) => message.localId !== 'welcome'),
          action.message,
        ],
        input: action.clearInput ? '' : state.input,
        isLoading: true,
      };
    case 'assistant-reply-appended': {
      const reconciled = reconcileActionStatus(state.messages, action.reply.action);
      return {
        ...state,
        activeConversationId: action.reply.conversation_id,
        messages: [
          ...reconciled.filter((message) => message.localId !== 'welcome'),
          {
            id: action.reply.message_id,
            role: 'assistant',
            content: action.reply.reply,
            action: action.reply.action,
          },
        ],
        hasUnread: state.hasUnread || action.markUnread,
      };
    }
    case 'local-error-appended':
      return { ...state, messages: [...state.messages, action.message] };
    case 'send-finished':
      return { ...state, isLoading: false };
    case 'action-started':
      return { ...state, actionLoadingId: action.actionId };
    case 'action-finished':
      return { ...state, actionLoadingId: null };
    case 'action-dismissed':
      return {
        ...state,
        messages: state.messages.map((message) => message.id === action.messageId
          ? {
              ...message,
              action: message.action ? { ...message.action, status: 'cancelled' } : null,
            }
          : message),
      };
    case 'selection-toggled': {
      const existing = state.deleteSelections[action.messageId] || [];
      const selected = existing.includes(action.resourceId)
        ? existing.filter((id) => id !== action.resourceId)
        : [...existing, action.resourceId];
      return {
        ...state,
        deleteSelections: { ...state.deleteSelections, [action.messageId]: selected },
      };
    }
    case 'selection-cleared': {
      const next = { ...state.deleteSelections };
      delete next[action.messageId];
      return { ...state, deleteSelections: next };
    }
    case 'import-opened':
      return {
        ...state,
        showImport: true,
        showHistory: false,
        fileToImport: action.singleFile,
        filesToImport: action.listFiles,
        templateFilesToImport: action.templateFiles,
      };
    case 'import-closed':
      return {
        ...state,
        showImport: false,
        fileToImport: null,
        filesToImport: [],
        templateFilesToImport: [],
      };
    case 'bundle-import-started':
      return { ...state, importingBundle: true, isLoading: true };
    case 'bundle-import-finished':
      return { ...state, importingBundle: false, isLoading: false };
    case 'preview-opened':
      return { ...state, previewVariableCategory: action.category };
    case 'preview-closed':
      return { ...state, previewVariableCategory: null };
    default:
      return state;
  }
}

async function looksLikeTemplateFile(file: File): Promise<boolean> {
  const text = await file.text();
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    return /^subject:\s*/i.test(line.trim());
  }
  return false;
}

function storeConversationId(conversationId: number): void {
  localStorage.setItem(ACTIVE_CONVERSATION_KEY, String(conversationId));
}

function forgetConversationId(): void {
  localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
}

export function useFleetAssistant(): FleetAssistantController {
  const { fetchIt } = useHttpFetcher();
  const [state, dispatch] = useReducer(assistantReducer, INITIAL_STATE);
  const localMessageIdRef = useRef(-Date.now());
  const conversationsQuery = useApiQuery<AssistantConversationsResponse>({
    cacheKey: ['assistant', 'conversations'],
    endpoint: 'assistant/conversations',
    enabled: state.showHistory,
    keepPreviousData: false,
  });
  const conversations = conversationsQuery.data?.conversations ?? EMPTY_CONVERSATIONS;
  const mutateConversations = conversationsQuery.mutate;

  const createLocalMessage = useCallback((content: string, prefix: string): AssistantMessage => {
    localMessageIdRef.current -= 1;
    return {
      id: localMessageIdRef.current,
      localId: `${prefix}-${Math.abs(localMessageIdRef.current)}`,
      role: prefix === 'user' ? 'user' : 'assistant',
      content,
      action: null,
    };
  }, []);

  const rememberConversation = useCallback((conversationId: number) => {
    storeConversationId(conversationId);
    dispatch({ type: 'conversation-remembered', conversationId });
  }, []);

  const appendAssistantReply = useCallback((reply: AssistantReply, markUnread = false) => {
    storeConversationId(reply.conversation_id);
    dispatch({ type: 'assistant-reply-appended', reply, markUnread });
  }, []);

  const appendLocalError = useCallback((error: unknown, prefix = 'error') => {
    dispatch({
      type: 'local-error-appended',
      message: createLocalMessage(getErrorMessage(error), prefix),
    });
  }, [createLocalMessage]);

  const loadConversation = useCallback(async (conversationId: number) => {
    dispatch({ type: 'conversation-loading' });
    try {
      const data = await fetchIt<AssistantConversationResponse>({
        apiEndPoint: `assistant/conversations/${conversationId}`,
        httpMethod: 'get',
      });
      storeConversationId(conversationId);
      dispatch({
        type: 'conversation-loaded',
        conversationId,
        messages: data.messages,
      });
    } catch {
      forgetConversationId();
      dispatch({ type: 'conversation-load-failed' });
    }
  }, [fetchIt]);

  useEffect(() => {
    const savedId = Number(localStorage.getItem(ACTIVE_CONVERSATION_KEY));
    if (Number.isInteger(savedId) && savedId > 0) {
      void loadConversation(savedId);
    }
  }, [loadConversation]);

  useEffect(() => {
    if (state.bubbleDismissed || state.isOpen) return;
    const show = window.setTimeout(() => dispatch({ type: 'bubble-shown', visible: true }), 3000);
    const hide = window.setTimeout(() => dispatch({ type: 'bubble-shown', visible: false }), 11000);
    return () => {
      window.clearTimeout(show);
      window.clearTimeout(hide);
    };
  }, [state.bubbleDismissed, state.isOpen]);

  const startNew = useCallback(() => {
    forgetConversationId();
    dispatch({ type: 'conversation-started' });
  }, []);

  const send = useCallback(async (messageOverride?: string) => {
    const text = (messageOverride ?? state.input).trim();
    if (!text || state.isLoading) return;

    dispatch({
      type: 'send-started',
      message: createLocalMessage(text, 'user'),
      clearInput: messageOverride === undefined,
    });
    try {
      const reqData: AssistantChatRequest = {
        message: text,
        conversation_id: state.activeConversationId,
      };
      const data = await fetchIt<AssistantReply, AssistantChatRequest>({
        apiEndPoint: 'assistant/chat',
        httpMethod: 'post',
        reqData,
      });
      appendAssistantReply(data, !state.isOpen);
      void mutateConversations();
    } catch (error) {
      appendLocalError(error);
    } finally {
      dispatch({ type: 'send-finished' });
    }
  }, [
    appendAssistantReply,
    appendLocalError,
    createLocalMessage,
    fetchIt,
    mutateConversations,
    state.activeConversationId,
    state.input,
    state.isLoading,
    state.isOpen,
  ]);

  const importBundle = useCallback(async (file: File) => {
    if (!file || state.importingBundle) return;
    dispatch({ type: 'bundle-import-started' });
    try {
      let conversationId = state.activeConversationId;
      if (!conversationId) {
        const conversation = await fetchIt<AssistantConversation>({
          apiEndPoint: 'assistant/conversations',
          httpMethod: 'post',
        });
        conversationId = conversation.id;
        rememberConversation(conversation.id);
      }
      const reqData = new FormData();
      reqData.append('file', file);
      reqData.append('conversation_id', String(conversationId));
      const data = await fetchIt<AssistantReply, FormData>({
        apiEndPoint: 'assistant/import-bundle',
        httpMethod: 'post',
        reqData,
      });
      appendAssistantReply(data);
      void mutateConversations();
    } catch (error) {
      appendLocalError(error);
    } finally {
      dispatch({ type: 'bundle-import-finished' });
    }
  }, [
    appendAssistantReply,
    appendLocalError,
    fetchIt,
    mutateConversations,
    rememberConversation,
    state.activeConversationId,
    state.importingBundle,
  ]);

  const prepareCampaigns = useCallback(async () => {
    if (!state.activeConversationId || state.actionLoadingId !== null) return;
    dispatch({ type: 'action-started', actionId: -1 });
    try {
      const reqData: AssistantConversationIdRequest = {
        conversation_id: state.activeConversationId,
      };
      const data = await fetchIt<AssistantReply, AssistantConversationIdRequest>({
        apiEndPoint: 'assistant/campaigns/prepare',
        httpMethod: 'post',
        reqData,
      });
      appendAssistantReply(data);
    } catch (error) {
      appendLocalError(error);
    } finally {
      dispatch({ type: 'action-finished' });
    }
  }, [
    appendAssistantReply,
    appendLocalError,
    fetchIt,
    state.actionLoadingId,
    state.activeConversationId,
  ]);

  const confirmCampaigns = useCallback(async () => {
    if (!state.activeConversationId || state.actionLoadingId !== null) return;
    dispatch({ type: 'action-started', actionId: -1 });
    try {
      const reqData: AssistantConversationIdRequest = {
        conversation_id: state.activeConversationId,
      };
      const data = await fetchIt<AssistantReply, AssistantConversationIdRequest>({
        apiEndPoint: 'assistant/campaigns/confirm',
        httpMethod: 'post',
        reqData,
      });
      appendAssistantReply(data);
    } catch (error) {
      appendLocalError(error);
    } finally {
      dispatch({ type: 'action-finished' });
    }
  }, [
    appendAssistantReply,
    appendLocalError,
    fetchIt,
    state.actionLoadingId,
    state.activeConversationId,
  ]);

  const prepareDelete = useCallback(async (
    action: AssistantAction,
    resourceIds: number[],
    messageId?: number,
  ) => {
    if (!state.activeConversationId
      || !action.resource_type
      || state.actionLoadingId !== null
      || resourceIds.length === 0) return;
    dispatch({ type: 'action-started', actionId: messageId ?? resourceIds[0] });
    try {
      const reqData: AssistantPrepareDeleteRequest = {
        conversation_id: state.activeConversationId,
        resource_type: action.resource_type,
        resource_ids: resourceIds,
      };
      const data = await fetchIt<AssistantReply, AssistantPrepareDeleteRequest>({
        apiEndPoint: 'assistant/actions/prepare-delete',
        httpMethod: 'post',
        reqData,
      });
      appendAssistantReply(data);
      if (messageId !== undefined) dispatch({ type: 'selection-cleared', messageId });
    } catch (error) {
      appendLocalError(error, 'action-error');
    } finally {
      dispatch({ type: 'action-finished' });
    }
  }, [
    appendAssistantReply,
    appendLocalError,
    fetchIt,
    state.actionLoadingId,
    state.activeConversationId,
  ]);

  const runSelectedTool = useCallback(async (
    action: AssistantAction,
    resourceIds: number[],
    messageId?: number,
  ) => {
    if (!state.activeConversationId
      || state.actionLoadingId !== null
      || resourceIds.length === 0
      || !action.tool_name) return;
    dispatch({ type: 'action-started', actionId: messageId ?? resourceIds[0] });
    try {
      const reqData: AssistantRunToolRequest = {
        conversation_id: state.activeConversationId,
        tool_name: action.tool_name,
        resource_ids: resourceIds,
      };
      const data = await fetchIt<AssistantReply, AssistantRunToolRequest>({
        apiEndPoint: 'assistant/actions/run-tool',
        httpMethod: 'post',
        reqData,
      });
      appendAssistantReply(data);
      if (messageId !== undefined) dispatch({ type: 'selection-cleared', messageId });
    } catch (error) {
      appendLocalError(error, 'action-error');
    } finally {
      dispatch({ type: 'action-finished' });
    }
  }, [
    appendAssistantReply,
    appendLocalError,
    fetchIt,
    state.actionLoadingId,
    state.activeConversationId,
  ]);

  const resolveAction = useCallback(async (
    action: AssistantAction,
    resolution: 'confirm' | 'cancel',
  ) => {
    if (!action.action_ids?.length || state.actionLoadingId !== null) return;
    dispatch({ type: 'action-started', actionId: action.action_ids[0] });
    try {
      const reqData: AssistantActionIdsRequest = { action_ids: action.action_ids };
      const data = await fetchIt<AssistantReply, AssistantActionIdsRequest>({
        apiEndPoint: `assistant/actions/${resolution}`,
        httpMethod: 'post',
        reqData,
      });
      appendAssistantReply(data);
      void mutateConversations();
    } catch (error) {
      appendLocalError(error, 'action-error');
    } finally {
      dispatch({ type: 'action-finished' });
    }
  }, [
    appendAssistantReply,
    appendLocalError,
    fetchIt,
    mutateConversations,
    state.actionLoadingId,
  ]);

  const archive = useCallback(async (conversationId: number) => {
    try {
      await fetchIt<SuccessResponse>({
        apiEndPoint: `assistant/conversations/${conversationId}`,
        httpMethod: 'delete',
      });
      await mutateConversations((current) => current
        ? {
            ...current,
            conversations: current.conversations.filter((item) => item.id !== conversationId),
          }
        : current, { revalidate: false });
      if (state.activeConversationId === conversationId) startNew();
    } catch (error) {
      appendLocalError(error, 'archive-error');
      dispatch({ type: 'history-set', visible: false });
    }
  }, [
    appendLocalError,
    fetchIt,
    mutateConversations,
    startNew,
    state.activeConversationId,
  ]);

  const selectFiles = useCallback(async (selectedFiles: File[]) => {
    if (selectedFiles.length === 0) return;
    if (selectedFiles.length === 1) {
      const isTemplate = await looksLikeTemplateFile(selectedFiles[0]);
      dispatch({
        type: 'import-opened',
        singleFile: isTemplate ? null : selectedFiles[0],
        listFiles: [],
        templateFiles: isTemplate ? selectedFiles : [],
      });
      return;
    }

    const classified = await Promise.all(
      selectedFiles.map(async (file) => ({ file, isTemplate: await looksLikeTemplateFile(file) })),
    );
    dispatch({
      type: 'import-opened',
      singleFile: null,
      listFiles: classified.filter((item) => !item.isTemplate).map((item) => item.file),
      templateFiles: classified.filter((item) => item.isTemplate).map((item) => item.file),
    });
  }, []);

  return {
    ui: {
      isOpen: state.isOpen,
      hasUnread: state.hasUnread,
      showBubble: state.showBubble,
      bubbleDismissed: state.bubbleDismissed,
      open: () => dispatch({ type: 'assistant-opened' }),
      close: () => dispatch({ type: 'assistant-closed' }),
      dismissBubble: () => dispatch({ type: 'bubble-dismissed' }),
    },
    conversation: {
      messages: state.messages,
      conversations,
      activeId: state.activeConversationId,
      input: state.input,
      isLoading: state.isLoading,
      historyLoading: state.conversationLoading
        || (state.showHistory && (conversationsQuery.isLoading || conversationsQuery.isValidating)),
      showHistory: state.showHistory,
      setInput: (input) => dispatch({ type: 'input-updated', input }),
      send,
      load: loadConversation,
      startNew,
      archive,
      toggleHistory: () => dispatch({ type: 'history-set', visible: !state.showHistory }),
    },
    imports: {
      show: state.showImport,
      file: state.fileToImport,
      listFiles: state.filesToImport,
      templateFiles: state.templateFilesToImport,
      importingBundle: state.importingBundle,
      selectFiles,
      importBundle,
      close: () => dispatch({ type: 'import-closed' }),
    },
    workflow: {
      actionLoadingId: state.actionLoadingId,
      deleteSelections: state.deleteSelections,
      dismissAction: (messageId) => dispatch({ type: 'action-dismissed', messageId }),
      prepareCampaigns,
      confirmCampaigns,
      toggleSelection: (messageId, resourceId) => dispatch({
        type: 'selection-toggled',
        messageId,
        resourceId,
      }),
      prepareDelete,
      runSelectedTool,
      resolveAction,
    },
    preview: {
      category: state.previewVariableCategory,
      open: (category) => dispatch({ type: 'preview-opened', category }),
      close: () => dispatch({ type: 'preview-closed' }),
    },
  };
}
