import { useCallback, useMemo, useReducer, useState } from 'react';
import { CACHE_KEYS } from '../../cache';
import { showToast } from '../../components/toast-store';
import { useConfirm } from '../../components/useConfirm';
import { getErrorMessage } from '../../http/api-error';
import { useApiQuery } from '../../hooks/useApiQuery';
import { useBulkSelection } from '../../hooks/useBulkSelection';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import { matchesPageSearch } from '../../utils/page-search';
import { getCachedInitial, hasCachedData, saveToCache } from '../../initial-cache';
import type {
  AttachmentUploadResponse,
  EmailTemplate,
  SuccessResponse,
  TemplateEditorAction,
  TemplateEditorSource,
  TemplateEditorState,
  TemplateFormData,
  TemplateInput,
  TemplatesPageController,
} from '../../../typefiles';

const EMPTY_TEMPLATE_FORM: TemplateFormData = {
  name: '',
  description: '',
  category: 'custom',
  subject_line: '',
  html_content: '',
  template_type: 'html',
  available_variables: [],
};

const INITIAL_EDITOR_STATE: TemplateEditorState = {
  editorOpen: false,
  editingId: null,
  previewOpen: false,
  previewHtml: '',
  form: EMPTY_TEMPLATE_FORM,
  attachments: [],
  attachmentUploading: false,
};

function extractVariables(html: string, subject = ''): string[] {
  const expression = /\{\{\s*([a-zA-Z0-9_-]+)\s*(?:\|\s*([^}]*?)\s*)?\}\}/g;
  const variables = new Set<string>();
  let match: RegExpExecArray | null;
  const content = `${html} ${subject}`;
  while ((match = expression.exec(content)) !== null) {
    variables.add(match[1].trim().toLowerCase().replace(/-/g, '_'));
  }
  return Array.from(variables);
}

function formFromSource(source?: TemplateEditorSource): TemplateFormData {
  if (!source) return { ...EMPTY_TEMPLATE_FORM };
  return {
    name: source.name || '',
    description: source.description || '',
    category: source.category || 'custom',
    subject_line: source.subject_line || '',
    html_content: source.html_content || '',
    template_type: source.template_type || 'html',
    available_variables: extractVariables(
      source.html_content || '',
      source.subject_line || '',
    ),
  };
}

function templateEditorReducer(
  state: TemplateEditorState,
  action: TemplateEditorAction,
): TemplateEditorState {
  switch (action.type) {
    case 'editor-opened':
      return {
        ...state,
        editorOpen: true,
        editingId: action.source?.id ?? null,
        form: formFromSource(action.source),
        attachments: action.source?.attachments ?? [],
      };
    case 'editor-closed':
      return { ...state, editorOpen: false };
    case 'form-updated':
      return { ...state, form: { ...state.form, ...action.patch } };
    case 'html-updated':
      return {
        ...state,
        form: {
          ...state.form,
          html_content: action.html,
          available_variables: action.variables,
        },
      };
    case 'subject-updated':
      return {
        ...state,
        form: {
          ...state.form,
          subject_line: action.subject,
          available_variables: action.variables,
        },
      };
    case 'preview-opened':
      return { ...state, previewOpen: true, previewHtml: action.html };
    case 'preview-closed':
      return { ...state, previewOpen: false };
    case 'uploading':
      return { ...state, attachmentUploading: action.active };
    case 'attachment-added':
      return { ...state, attachments: [...state.attachments, action.attachment] };
    case 'attachment-removed':
      return {
        ...state,
        attachments: state.attachments.filter((_, index) => index !== action.index),
      };
    default:
      return state;
  }
}

export function useTemplatesPage(): TemplatesPageController {
  const { fetchIt } = useHttpFetcher();
  const confirm = useConfirm();
  const hadCache = useMemo(() => hasCachedData(CACHE_KEYS.TEMPLATES), []);
  const cachedTemplates = useMemo(
    () => getCachedInitial<EmailTemplate[]>(CACHE_KEYS.TEMPLATES, []),
    [],
  );
  const [lastUpdated, markUpdated] = useReducer(() => new Date(), null as Date | null);
  const [editor, dispatchEditor] = useReducer(templateEditorReducer, INITIAL_EDITOR_STATE);
  const [templateSearch, setTemplateSearch] = useState('');

  const templatesQuery = useApiQuery<EmailTemplate[]>({
    cacheKey: CACHE_KEYS.TEMPLATES,
    endpoint: 'email-templates',
    pagination: { offsetParameter: 'offset', pageSize: 100 },
    fallbackData: hadCache ? cachedTemplates : undefined,
    onSuccess: (templates) => {
      saveToCache(CACHE_KEYS.TEMPLATES, templates);
      markUpdated();
    },
    errorNotification: { enabled: true },
  });
  const templates = templatesQuery.data ?? cachedTemplates;
  const mutateTemplates = templatesQuery.mutate;
  const refresh = useCallback(async () => {
    await mutateTemplates();
  }, [mutateTemplates]);

  const visibleTemplates = useMemo(
    () => templates.filter((template) => matchesPageSearch(templateSearch, [
      template.name,
      template.description,
      template.category,
      template.subject_line,
      template.template_type,
    ])),
    [templateSearch, templates],
  );
  const templateIds = useMemo(
    () => visibleTemplates.map(({ id }) => id),
    [visibleTemplates],
  );
  const selection = useBulkSelection(templateIds);
  const resetSelection = selection.reset;
  const updateTemplateSearch = useCallback((value: string) => {
    setTemplateSearch(value);
    resetSelection();
  }, [resetSelection]);

  const updateHtml = useCallback((html: string) => {
    dispatchEditor({
      type: 'html-updated',
      html,
      variables: extractVariables(html, editor.form.subject_line),
    });
  }, [editor.form.subject_line]);

  const updateSubject = useCallback((subject: string) => {
    dispatchEditor({
      type: 'subject-updated',
      subject,
      variables: extractVariables(editor.form.html_content, subject),
    });
  }, [editor.form.html_content]);

  const save = useCallback(async (event: React.FormEvent) => {
    event.preventDefault();
    const payload: TemplateInput = {
      ...editor.form,
      attachments: editor.attachments.length > 0 ? editor.attachments : null,
    };
    if (editor.form.template_type === 'plain_text') {
      payload.text_fallback = editor.form.html_content;
    }

    try {
      await fetchIt<EmailTemplate, TemplateInput>({
        apiEndPoint: editor.editingId
          ? `email-templates/${editor.editingId}`
          : 'email-templates',
        httpMethod: editor.editingId ? 'put' : 'post',
        reqData: payload,
      });
      showToast(editor.editingId ? 'Template updated' : 'Template created', 'success');
      dispatchEditor({ type: 'editor-closed' });
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [editor.attachments, editor.editingId, editor.form, fetchIt, refresh]);

  const remove = useCallback(async (id: number) => {
    const confirmed = await confirm({
      title: 'Delete Template',
      message: 'Are you sure you want to delete this template? This cannot be undone.',
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;

    try {
      await fetchIt<SuccessResponse>({
        apiEndPoint: `email-templates/${id}`,
        httpMethod: 'delete',
      });
      showToast('Template deleted', 'success');
      void refresh();
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    }
  }, [confirm, fetchIt, refresh]);

  const removeSelected = useCallback(async () => {
    const ids = Array.from(selection.selectedIds);
    if (ids.length === 0) return;
    const confirmed = await confirm({
      title: `Delete ${ids.length} Template${ids.length === 1 ? '' : 's'}`,
      message: `Are you sure you want to delete ${ids.length} selected template${ids.length === 1 ? '' : 's'}? This cannot be undone.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;

    selection.beginDelete();
    try {
      const results = await Promise.allSettled(ids.map((id) => fetchIt<SuccessResponse>({
        apiEndPoint: `email-templates/${id}`,
        httpMethod: 'delete',
      })));
      const succeeded = results.filter(({ status }) => status === 'fulfilled').length;
      const failed = results.length - succeeded;
      showToast(
        failed === 0
          ? `${succeeded} template${succeeded === 1 ? '' : 's'} deleted`
          : `${succeeded} deleted, ${failed} failed`,
        failed === 0 ? 'success' : 'error',
      );
      selection.finishDelete(true);
      void refresh();
    } finally {
      selection.finishDelete();
    }
  }, [confirm, fetchIt, refresh, selection]);

  const uploadAttachments = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) return;

    dispatchEditor({ type: 'uploading', active: true });
    try {
      for (const file of files) {
        const requestData = new FormData();
        requestData.append('file', file);
        const attachment = await fetchIt<AttachmentUploadResponse, FormData>({
          apiEndPoint: 'email-templates/upload-attachment',
          httpMethod: 'post',
          reqData: requestData,
        });
        dispatchEditor({ type: 'attachment-added', attachment });
      }
    } catch (error: unknown) {
      showToast(getErrorMessage(error), 'error');
    } finally {
      dispatchEditor({ type: 'uploading', active: false });
      event.target.value = '';
    }
  }, [fetchIt]);

  return {
    data: { templates, visibleTemplates },
    status: {
      loading: templatesQuery.isValidating,
      lastUpdated,
    },
    editor: {
      ...editor,
      open: (source) => dispatchEditor({ type: 'editor-opened', source }),
      close: () => dispatchEditor({ type: 'editor-closed' }),
      update: (patch) => dispatchEditor({ type: 'form-updated', patch }),
      updateHtml,
      updateSubject,
      uploadAttachments,
      removeAttachment: (index) => dispatchEditor({ type: 'attachment-removed', index }),
    },
    preview: {
      open: editor.previewOpen,
      html: editor.previewHtml,
      show: (html) => dispatchEditor({ type: 'preview-opened', html }),
      close: () => dispatchEditor({ type: 'preview-closed' }),
    },
    search: {
      value: templateSearch,
      resultCount: visibleTemplates.length,
      totalCount: templates.length,
      setValue: updateTemplateSearch,
    },
    selection,
    actions: {
      refresh,
      save,
      remove,
      removeSelected,
    },
  };
}
