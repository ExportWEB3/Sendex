import { useCallback, useMemo, useReducer } from 'react';
import { getErrorMessage } from '../../http/api-error';
import { useApiQuery } from '../../hooks/useApiQuery';
import type {
  AssistantTemplatePreviewAction,
  AssistantTemplatePreviewController,
  AssistantTemplatePreviewState,
  AssistantVariableTemplatePreview,
  AssistantVariableTemplatePreviewsResponse,
} from '../../../typefiles';

const INITIAL_STATE: AssistantTemplatePreviewState = {
  selectedId: null,
  showSource: false,
};
const EMPTY_TEMPLATES: AssistantVariableTemplatePreview[] = [];

function previewReducer(
  state: AssistantTemplatePreviewState,
  action: AssistantTemplatePreviewAction,
): AssistantTemplatePreviewState {
  switch (action.type) {
    case 'list-loaded':
      return state.selectedId === null
        ? { ...state, selectedId: action.firstTemplateId }
        : state;
    case 'template-selected':
      return { selectedId: action.templateId, showSource: false };
    case 'selection-reset':
      return INITIAL_STATE;
    case 'source-set':
      return { ...state, showSource: action.visible };
    default:
      return state;
  }
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character] || character);
}

function createPreviewDocument(template: AssistantVariableTemplatePreview): string {
  const body = template.body || '';
  const policy = "default-src 'none'; img-src data: blob: https: http:; style-src 'unsafe-inline' https: http:; font-src data: https: http:; script-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'";
  const head = `<meta http-equiv="Content-Security-Policy" content="${policy}"><style>html{color-scheme:light}body{margin:0;padding:24px;background:#fff;color:#17212b;font-family:Arial,sans-serif;overflow-wrap:anywhere}img{max-width:100%;height:auto}</style>`;

  if (template.template_type === 'plain_text') {
    return `<!doctype html><html><head>${head}</head><body><div style="white-space:pre-wrap">${escapeHtml(body)}</div></body></html>`;
  }
  if (!body.trim()) {
    return `<!doctype html><html><head>${head}</head><body><p style="color:#64748b">This template has no body content.</p></body></html>`;
  }
  if (/<html[\s>]/i.test(body)) {
    if (/<head[\s>]/i.test(body)) return body.replace(/<head([^>]*)>/i, `<head$1>${head}`);
    return body.replace(/<html([^>]*)>/i, `<html$1><head>${head}</head>`);
  }
  return `<!doctype html><html><head>${head}</head><body>${body}</body></html>`;
}

export function useAssistantTemplatePreview(
  conversationId: number,
  category: string,
): AssistantTemplatePreviewController {
  const [state, dispatch] = useReducer(previewReducer, INITIAL_STATE);
  const listQuery = useApiQuery<AssistantVariableTemplatePreviewsResponse>({
    cacheKey: ['assistant', conversationId, 'bundle-template-previews', category],
    endpoint: `assistant/conversations/${conversationId}/bundle-template-previews`,
    query: { category },
    keepPreviousData: false,
    onSuccess: (response) => dispatch({
      type: 'list-loaded',
      firstTemplateId: response.templates[0]?.id ?? null,
    }),
  });
  const templates = listQuery.data?.templates ?? EMPTY_TEMPLATES;
  const selectedIndex = templates.findIndex((template) => template.id === state.selectedId);
  const selectedMetadata = selectedIndex >= 0 ? templates[selectedIndex] : undefined;
  const previewRequired = state.selectedId !== null
    && typeof selectedMetadata?.body !== 'string';
  const previewQuery = useApiQuery<AssistantVariableTemplatePreviewsResponse>({
    cacheKey: ['assistant', conversationId, 'bundle-template-preview', category, state.selectedId],
    endpoint: `assistant/conversations/${conversationId}/bundle-template-previews`,
    query: { category, template_id: state.selectedId },
    enabled: previewRequired,
    keepPreviousData: false,
  });
  const selectedTemplate = previewQuery.data?.templates[0] ?? selectedMetadata;
  const renderedDocument = useMemo(
    () => selectedTemplate && typeof selectedTemplate.body === 'string'
      ? createPreviewDocument(selectedTemplate)
      : '',
    [selectedTemplate],
  );

  const select = useCallback((templateId: number) => {
    dispatch({ type: 'template-selected', templateId });
  }, []);
  const move = useCallback((offset: number) => {
    if (templates.length < 2 || selectedIndex < 0) return;
    const nextIndex = (selectedIndex + offset + templates.length) % templates.length;
    dispatch({ type: 'template-selected', templateId: templates[nextIndex].id });
  }, [selectedIndex, templates]);
  const mutateList = listQuery.mutate;
  const mutatePreview = previewQuery.mutate;

  return {
    templates,
    selectedId: state.selectedId,
    selectedIndex,
    selectedTemplate,
    renderedDocument,
    showSource: state.showSource,
    listLoading: listQuery.isLoading,
    previewLoadingId: previewQuery.isLoading ? state.selectedId : null,
    listError: listQuery.error ? getErrorMessage(listQuery.error) : '',
    previewError: previewQuery.error ? getErrorMessage(previewQuery.error) : '',
    actions: {
      select,
      move,
      setShowSource: (visible) => dispatch({ type: 'source-set', visible }),
      retryList: async () => {
        dispatch({ type: 'selection-reset' });
        await mutateList();
      },
      retryPreview: async () => {
        await mutatePreview();
      },
    },
  };
}
