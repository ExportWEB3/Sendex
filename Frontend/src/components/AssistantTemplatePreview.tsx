import { useEffect, useMemo, useRef } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ChevronRight,
  Code2,
  Eye,
  FileText,
  Loader2,
  Paperclip,
  X,
} from 'lucide-react';
import { useAssistantTemplatePreview } from '../features/assistant/useAssistantTemplatePreview';
import { sanitizeEmailHtml } from '../utils/sanitize-html';
import type { AssistantTemplatePreviewProps } from '../../typefiles';

const SPECIAL_LABELS: Record<string, string> = {
  linkedin: 'LinkedIn',
  microsoft: 'Microsoft',
};

function formatLabel(value: string): string {
  const normalized = value.trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (SPECIAL_LABELS[normalized]) return SPECIAL_LABELS[normalized];
  return normalized
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function AssistantTemplatePreview({
  conversationId,
  category,
  onClose,
}: AssistantTemplatePreviewProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const {
    templates,
    selectedId,
    selectedIndex,
    selectedTemplate,
    renderedDocument: renderedPreview,
    showSource,
    listLoading,
    previewLoadingId,
    listError,
    previewError,
    actions: {
      select: selectTemplate,
      move: moveSelection,
      setShowSource,
      retryList,
      retryPreview,
    },
  } = useAssistantTemplatePreview(conversationId, category);

  const categoryLabel = formatLabel(category);
  const sanitizedRenderedPreview = useMemo(
    () => sanitizeEmailHtml(renderedPreview),
    [renderedPreview],
  );

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialogRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-80 flex items-center justify-center bg-black/80 p-3 backdrop-blur-sm sm:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="assistant-template-preview-title"
        tabIndex={-1}
        className="flex h-[min(850px,94vh)] w-full max-w-6xl flex-col overflow-hidden border border-[#345064] bg-[#070b0f] text-[#d7e6f2] shadow-2xl outline-none"
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-[#263746] bg-[#0a1118] px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[#45c7ef]">Variable context</p>
            <h2 id="assistant-template-preview-title" className="truncate text-base font-semibold text-white sm:text-lg">
              {categoryLabel} · Template previews
            </h2>
            <p className="mt-0.5 text-xs text-[#7891a5]">
              See where this value is used before replying with <span className="text-[#bfe9f6]">{categoryLabel}: value</span>.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center border border-[#345064] text-[#9db1c1] hover:border-[#45c7ef] hover:bg-[#12202a] hover:text-white"
            aria-label="Close template previews"
            title="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <aside className="flex max-h-48 w-full shrink-0 flex-col border-b border-[#263746] bg-[#080d12] md:max-h-none md:w-72 md:border-b-0 md:border-r">
            <div className="flex items-center justify-between border-b border-[#1d2b36] px-3 py-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-[#60798d]">Matching templates</span>
              <span className="border border-[#2d4252] bg-[#101820] px-1.5 py-0.5 text-[10px] text-[#9edcf0]">{templates.length}</span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
              {listLoading && (
                <div className="flex items-center justify-center gap-2 py-8 text-xs text-[#7891a5]">
                  <Loader2 className="h-4 w-4 animate-spin text-[#45c7ef]" /> Loading templates…
                </div>
              )}
              {!listLoading && templates.map((template, index) => (
                <button
                  key={template.id}
                  type="button"
                  onClick={() => selectTemplate(template.id)}
                  className={`mb-1 flex w-full items-start gap-2 border px-2.5 py-2 text-left transition-colors ${
                    template.id === selectedId
                      ? 'border-[#45c7ef] bg-[#10212b]'
                      : 'border-transparent bg-[#0a1118] hover:border-[#2d4252] hover:bg-[#101820]'
                  }`}
                >
                  <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center text-[10px] font-semibold ${
                    template.id === selectedId ? 'bg-[#138fb5] text-white' : 'bg-[#17232d] text-[#7891a5]'
                  }`}>
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-[#d7e6f2]">{template.name}</span>
                    <span className="mt-0.5 block truncate text-[10px] text-[#60798d]">{template.subject || 'No subject'}</span>
                    <span className={`mt-1 flex items-center gap-1 text-[10px] ${
                      template.resolved && !template.skipped_variables.length ? 'text-emerald-400' : 'text-amber-300'
                    }`}>
                      {template.pending_variables.length > 0
                        ? <><AlertTriangle className="h-3 w-3" /> {template.pending_variables.length} needed</>
                        : template.skipped_variables.length > 0
                          ? <><AlertTriangle className="h-3 w-3" /> {template.skipped_variables.length} left blank</>
                          : <><Check className="h-3 w-3" /> Value set</>}
                    </span>
                  </span>
                </button>
              ))}
              {!listLoading && templates.length === 0 && !listError && (
                <p className="px-3 py-8 text-center text-xs text-[#7891a5]">No matching templates.</p>
              )}
            </div>
          </aside>

          <main className="relative flex min-h-0 min-w-0 flex-1 flex-col bg-[#0b1117]">
            {selectedTemplate && (
              <>
                <div className="shrink-0 space-y-2 border-b border-[#263746] px-4 py-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-white">{selectedTemplate.name}</p>
                      <p className="text-[10px] uppercase tracking-wider text-[#60798d]">
                        Template {selectedIndex + 1} of {templates.length}
                      </p>
                    </div>
                    <div className="flex border border-[#2d4252] bg-[#070b0f]">
                      <button
                        type="button"
                        onClick={() => setShowSource(false)}
                        className={`flex items-center gap-1 px-2.5 py-1.5 text-[11px] ${
                          !showSource ? 'bg-[#138fb5] text-white' : 'text-[#7891a5] hover:text-white'
                        }`}
                      >
                        <Eye className="h-3.5 w-3.5" /> Preview
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowSource(true)}
                        className={`flex items-center gap-1 border-l border-[#2d4252] px-2.5 py-1.5 text-[11px] ${
                          showSource ? 'bg-[#138fb5] text-white' : 'text-[#7891a5] hover:text-white'
                        }`}
                      >
                        <Code2 className="h-3.5 w-3.5" /> Source
                      </button>
                    </div>
                  </div>

                  <div className="border border-[#263746] bg-[#070b0f] px-3 py-2">
                    <span className="block text-[9px] font-semibold uppercase tracking-wider text-[#60798d]">Subject</span>
                    <span className="mt-0.5 block wrap-break-word text-xs text-[#d7e6f2]">{selectedTemplate.subject || 'No subject'}</span>
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="mr-0.5 text-[10px] uppercase tracking-wide text-[#60798d]">Used as</span>
                    {selectedTemplate.variables.map((variable) => (
                      <span
                        key={variable}
                        className={`border px-1.5 py-0.5 font-mono text-[10px] ${
                          selectedTemplate.pending_variables.includes(variable)
                            ? 'border-amber-700 bg-amber-950/40 text-amber-200'
                            : selectedTemplate.skipped_variables.includes(variable)
                              ? 'border-orange-800 bg-orange-950/30 text-orange-300'
                            : 'border-emerald-800 bg-emerald-950/30 text-emerald-300'
                        }`}
                      >
                        {'{{'}{variable}{'}}'}
                      </span>
                    ))}
                    {selectedTemplate.attachment_names.length > 0 && (
                      <span className="ml-auto flex items-center gap-1 text-[10px] text-[#7891a5]" title={selectedTemplate.attachment_names.join(', ')}>
                        <Paperclip className="h-3 w-3" /> {selectedTemplate.attachment_names.length} attachment(s)
                      </span>
                    )}
                  </div>
                </div>

                <div className="relative min-h-0 flex-1 overflow-hidden bg-[#111923] p-2 sm:p-3">
                  {previewLoadingId === selectedId && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#0b1117]/90 text-xs text-[#9db1c1]">
                      <Loader2 className="mr-2 h-4 w-4 animate-spin text-[#45c7ef]" /> Loading preview…
                    </div>
                  )}
                  {typeof selectedTemplate.body === 'string' && (showSource ? (
                    <pre className="h-full overflow-auto border border-[#2d4252] bg-[#05080b] p-4 whitespace-pre-wrap wrap-break-word font-mono text-[11px] leading-relaxed text-[#b8cad7]">
                      {selectedTemplate.body || 'This template has no body content.'}
                    </pre>
                  ) : (
                    <iframe
                      srcDoc={sanitizedRenderedPreview}
                      title={`${selectedTemplate.name} email preview`}
                      className="h-full w-full border border-[#2d4252] bg-white"
                      sandbox=""
                    />
                  ))}
                </div>
              </>
            )}

            {!selectedTemplate && !listLoading && (
              <div className="flex min-h-0 flex-1 items-center justify-center p-6">
                <div className="max-w-sm text-center">
                  {listError ? <AlertTriangle className="mx-auto h-6 w-6 text-amber-400" /> : <FileText className="mx-auto h-6 w-6 text-[#486174]" />}
                  <p className="mt-2 text-sm text-[#9db1c1]">{listError || 'Choose a template to preview.'}</p>
                  {listError && (
                    <button
                      type="button"
                      onClick={() => void retryList()}
                      className="mt-3 border border-[#45c7ef] bg-[#10212b] px-3 py-1.5 text-xs text-[#9edcf0] hover:bg-[#17303e]"
                    >
                      Try again
                    </button>
                  )}
                </div>
              </div>
            )}

            {selectedTemplate && previewError && previewLoadingId === null && typeof selectedTemplate.body !== 'string' && (
              <div className="absolute inset-x-4 bottom-16 z-20 border border-amber-800 bg-amber-950 px-3 py-2 text-xs text-amber-200 shadow-lg">
                <div className="flex items-center justify-between gap-3">
                  <span>{previewError}</span>
                  <button
                    type="button"
                    onClick={() => void retryPreview()}
                    className="border border-amber-600 px-2 py-1 text-[10px] hover:bg-amber-900"
                  >
                    Retry
                  </button>
                </div>
              </div>
            )}

            {templates.length > 0 && (
              <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-[#263746] bg-[#080d12] px-3 py-2">
                <p className="min-w-0 truncate text-[10px] text-[#60798d]">
                  Review the wording, then close this window and enter the requested value in chat.
                </p>
                <div className="flex shrink-0 gap-1">
                  <button
                    type="button"
                    onClick={() => moveSelection(-1)}
                    disabled={templates.length < 2}
                    className="flex h-7 w-7 items-center justify-center border border-[#345064] text-[#9db1c1] hover:border-[#45c7ef] hover:text-white disabled:opacity-30"
                    aria-label="Previous template"
                    title="Previous template"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => moveSelection(1)}
                    disabled={templates.length < 2}
                    className="flex h-7 w-7 items-center justify-center border border-[#345064] text-[#9db1c1] hover:border-[#45c7ef] hover:text-white disabled:opacity-30"
                    aria-label="Next template"
                    title="Next template"
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </footer>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
