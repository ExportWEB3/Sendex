import { useRef, useEffect, useLayoutEffect, useCallback } from 'react';
import {
  AlertTriangle,
  Bot,
  Check,
  Clock3,
  FileArchive,
  FileUp,
  History,
  Eye,
  Loader2,
  MessageCircle,
  Plus,
  Send,
  Sparkles,
  Trash2,
  User,
  X,
} from 'lucide-react';
import { ImportPanel } from './ImportPanel';
import { MultiListImportPanel } from './MultiListImportPanel';
import { MultiTemplateImportPanel } from './MultiTemplateImportPanel';
import { AssistantTemplatePreview } from './AssistantTemplatePreview';
import { useFleetAssistant } from '../features/assistant/useFleetAssistant';
import type { AssistantMessage } from '../../typefiles';

const explainBundleIssue = (issue: string) => {
  const accountMatch = issue.trim().match(/^Confirm the sending account for template (.+?)\.?$/i);
  if (accountMatch) {
    return `I couldn't confidently identify the sender for the "${accountMatch[1]}" email. Please choose the sending account so I don't use the wrong address.`;
  }
  const templateMatch = issue.trim().match(/^Confirm which template belongs to (?:list )?(.+?)\.?$/i);
  if (templateMatch) {
    return `I couldn't confidently tell which email template should be used for the "${templateMatch[1]}" contact list. Please tell me the correct template before I continue.`;
  }
  if (issue.trim().toLowerCase() === 'one or more recipient lists do not have a campaign template') {
    return "I couldn't find an email template for one or more contact lists. Please tell me which template each list should use before I continue.";
  }
  if (issue.trim().toLowerCase().startsWith('semantic review:')) {
    return `I need your help confirming one detail before I continue: ${issue.split(':').slice(1).join(':').trim()}`;
  }
  return issue;
};

export function FleetAssistant() {
  const { ui, conversation, imports, workflow, preview } = useFleetAssistant();
  const {
    isOpen,
    hasUnread,
    showBubble,
    bubbleDismissed,
    open: openAssistant,
    close: closeAssistant,
    dismissBubble,
  } = ui;
  const {
    messages,
    conversations,
    activeId: activeConversationId,
    input,
    isLoading,
    historyLoading,
    showHistory,
    setInput,
    send: sendMessage,
    load: loadConversation,
    startNew: startNewConversation,
    archive: archiveConversation,
    toggleHistory,
  } = conversation;
  const {
    show: showImport,
    file: fileToImport,
    listFiles: filesToImport,
    templateFiles: templateFilesToImport,
    importingBundle,
    selectFiles,
    importBundle: handleImportBundle,
    close: closeImportPanel,
  } = imports;
  const {
    actionLoadingId,
    deleteSelections,
    dismissAction: dismissActionLocally,
    prepareCampaigns: handlePrepareCampaigns,
    confirmCampaigns: handleConfirmCampaigns,
    toggleSelection,
    prepareDelete,
    runSelectedTool,
    resolveAction,
  } = workflow;
  const {
    category: previewVariableCategory,
    open: openTemplatePreview,
    close: closeTemplatePreview,
  } = preview;
  const bundleInputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const importFileRef = useRef<HTMLInputElement>(null);
  const wasOpenRef = useRef(false);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  }, []);

  useLayoutEffect(() => {
    if (!isOpen || showHistory) return;
    const justOpened = !wasOpenRef.current;
    wasOpenRef.current = true;
    scrollToBottom(justOpened ? 'auto' : 'smooth');
  }, [messages, showHistory, isOpen, scrollToBottom]);

  useEffect(() => {
    if (!isOpen) wasOpenRef.current = false;
  }, [isOpen]);

  useEffect(() => {
    if (isOpen) {
      window.setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  };

  const handleStartNewConversation = () => {
    startNewConversation();
    window.setTimeout(() => inputRef.current?.focus(), 50);
  };

  const handleImportFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files || []);
    event.target.value = '';
    await selectFiles(selectedFiles);
  };

  const renderContent = (text: string) => {
    const lines = text.split('\n');
    return lines.map((line, index) => {
      const numberedPrefix = line.match(/^(\d+\.\s)/)?.[1];
      const content = numberedPrefix ? line.slice(numberedPrefix.length) : line;
      const parts = content.split(/(\*\*.+?\*\*)/g);
      const indented = line.startsWith('• ') || line.startsWith('- ');

      return (
        <span key={index}>
          <span className={indented ? 'ml-2' : undefined}>
            {numberedPrefix && <strong>{numberedPrefix}</strong>}
            {parts.map((part, partIndex) => (
              part.startsWith('**') && part.endsWith('**')
                ? <strong key={partIndex}>{part.slice(2, -2)}</strong>
                : <span key={partIndex}>{part}</span>
            ))}
          </span>
          {index < lines.length - 1 && <br />}
        </span>
      );
    });
  };

  const renderAction = (message: AssistantMessage) => {
    const action = message.action;
    if (!action) return null;

    if (action.type === 'bundle_import_review' || action.type === 'bundle_variables') {
      if (action.status === 'cancelled') return null;
      const pendingGroups = (action.variable_groups || []).filter((group) => !group.resolved);
      const hasIssues = !!action.issues?.length;
      const ready = action.status === 'ready' && (action.unresolved_count ?? pendingGroups.length) === 0;
      const countRows = [
        ['Accounts', action.import_counts?.accounts],
        ['Inboxes', action.import_counts?.inboxes],
        ['Lists', action.import_counts?.lists],
        ['Templates', action.import_counts?.templates],
      ] as const;
      return (
        <div className="mt-3 space-y-2.5 border-t border-[#263746] pt-3">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-xs font-semibold text-[#9edcf0]">
              <FileArchive className="h-3.5 w-3.5" /> Bundle review
            </span>
            <span className={`border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
              ready
                ? 'border-emerald-700 bg-emerald-950/50 text-emerald-300'
                : 'border-amber-700 bg-amber-950/40 text-amber-300'
            }`}>
              {ready ? 'Ready' : hasIssues ? 'Needs review' : `${action.unresolved_count ?? pendingGroups.length} needed`}
            </span>
          </div>

          {action.import_counts && (
            <div className="grid grid-cols-2 gap-1">
              {countRows.map(([label, counts]) => counts && (
                <div key={label} className="border border-[#263746] bg-[#070d12] px-2 py-1.5">
                  <span className="block text-[10px] uppercase tracking-wide text-[#60798d]">{label}</span>
                  <span className="text-[11px] text-[#bdd0de]">
                    {counts.created} new · {counts.reused} reusable{counts.updated ? ` · ${counts.updated} update` : ''}{counts.failed ? ` · ${counts.failed} failed` : ''}
                  </span>
                </div>
              ))}
            </div>
          )}

          {!!action.mapping_count && (
            <p className="text-[11px] text-[#7891a5]">
              {action.account_count ?? 0} accounts · {action.list_count ?? 0} lists · {action.template_count ?? 0} templates
            </p>
          )}

          {action.asset_summary && (
            <div className="flex flex-wrap gap-x-2 gap-y-1 text-[10px] text-[#7891a5]">
              <span>{action.asset_summary.attachment ?? 0} attachment</span>
              <span>{action.asset_summary.link ?? 0} link</span>
              <span>{action.asset_summary.mixed ?? 0} mixed</span>
              <span>{action.asset_summary.plain ?? 0} plain</span>
            </div>
          )}

          {!!action.inferred_name_count && (
            <p className="text-[10px] text-[#7891a5]">
              {action.inferred_name_count} recipient name(s) inferred conservatively from email prefixes.
            </p>
          )}

          {!!action.skipped_count && (
            <div className="flex items-start gap-2 border border-amber-800 bg-amber-950/30 px-2.5 py-2 text-[11px] leading-relaxed text-amber-200">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                {action.skipped_count} custom value(s) intentionally left blank
                {!!action.skipped_categories?.length && `: ${action.skipped_categories.map((item) => item.replace(/_/g, ' ')).join(', ')}`}.
              </span>
            </div>
          )}

          {!!action.mapping_rows?.length && (
            <div className="max-h-40 space-y-1 overflow-y-auto">
              {action.mapping_rows.map((row, index) => (
                <div key={`${row.list}-${row.template}-${index}`} className="border border-[#263746] bg-[#070d12] px-2 py-1.5">
                  <div className="flex items-start justify-between gap-2 text-[11px]">
                    <span className="min-w-0 truncate text-[#d7e6f2]">Use {row.template} for {row.list}</span>
                    <span className="shrink-0 text-[#60798d]">{row.recipients ?? 0} recipients</span>
                  </div>
                  <p className="truncate text-[10px] text-[#7891a5]" title={row.account}>
                    {row.account === 'Automatic inbox selection'
                      ? 'A sending account will be chosen before sending'
                      : row.account}
                  </p>
                </div>
              ))}
            </div>
          )}

          {pendingGroups.length > 0 && (
            <div className="space-y-1">
              {pendingGroups.map((group) => (
                <button
                  key={group.category}
                  type="button"
                  onClick={() => openTemplatePreview(group.category)}
                  disabled={!activeConversationId}
                  className="group w-full border border-amber-900/60 bg-amber-950/20 px-2.5 py-2 text-left transition-colors hover:border-amber-500 hover:bg-amber-950/40 focus:border-[#45c7ef] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
                  aria-label={`Preview templates needing ${group.category.replace(/_/g, ' ')}`}
                  title={`Preview: ${group.templates.join(', ')}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium capitalize text-amber-200">
                      {group.category.replace(/_/g, ' ')}
                    </span>
                    <span className="flex items-center gap-1 text-[10px] text-amber-400/80">
                      {group.count - group.resolved_count} value(s) <Eye className="h-3 w-3" />
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-[10px] text-[#8ca2b3]" title={group.templates.join(', ')}>
                    {group.templates.join(', ')}
                  </p>
                  <span className="mt-1 block text-[9px] uppercase tracking-wide text-amber-500/70 group-hover:text-amber-300">
                    Click to preview matching templates
                  </span>
                </button>
              ))}
              <p className="text-[11px] leading-relaxed text-[#9db1c1]">
                Reply with the item name followed by its value, for example{' '}
                <span className="text-[#d7e6f2]">LinkedIn: your link</span>. If it belongs in several emails,
                say all or name those emails.
              </p>
              {!hasIssues && (
                <button
                  type="button"
                  onClick={() => void sendMessage('Continue without the remaining custom values')}
                  disabled={isLoading || actionLoadingId !== null}
                  className="mt-2 flex w-full items-center justify-center gap-1.5 border border-amber-700 bg-amber-950/30 px-2.5 py-2 text-xs font-semibold text-amber-200 transition-colors hover:border-amber-400 hover:bg-amber-950/60 disabled:cursor-not-allowed disabled:opacity-50"
                  title="Leave every unresolved custom variable blank and prepare the campaign review"
                >
                  <AlertTriangle className="h-3.5 w-3.5" />
                  Continue with blanks
                </button>
              )}
              {!hasIssues && (
                <p className="text-[10px] leading-relaxed text-amber-500/80">
                  Blank placeholders render as empty text. Drafts still require final confirmation.
                </p>
              )}
            </div>
          )}

          {!!action.issues?.length && (
            <div className="space-y-1.5 border border-amber-800/70 bg-amber-950/25 px-2.5 py-2 text-[11px] text-amber-200">
              <p className="flex items-center gap-1.5 font-semibold">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" /> I need your help before I continue
              </p>
              {action.issues.slice(0, 3).map((issue, index) => (
                <p key={`${issue}-${index}`}>{explainBundleIssue(issue)}</p>
              ))}
              <p className="text-[10px] text-amber-400/80">
                I will wait for your answer instead of guessing and using the wrong email or sender.
              </p>
            </div>
          )}

          {ready && (
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => dismissActionLocally(message.id)}
                disabled={actionLoadingId !== null}
                className="flex-1 border border-[#344654] bg-[#101820] px-2 py-2 text-xs text-[#9db1c1] hover:bg-[#17232d] disabled:opacity-50"
              >
                Later
              </button>
              <button
                type="button"
                onClick={() => void handlePrepareCampaigns()}
                disabled={actionLoadingId !== null}
                className="flex flex-1 items-center justify-center gap-1.5 border border-[#45c7ef] bg-[#138fb5] px-2 py-2 text-xs font-semibold text-white hover:bg-[#19a8d2] disabled:opacity-50"
              >
                {actionLoadingId === -1 ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                {action.materialized ? 'Review campaigns' : 'Approve import & review'}
              </button>
            </div>
          )}
        </div>
      );
    }

    if (action.type === 'create_campaigns_offer') {
      const status = action.status || 'pending';
      if (status !== 'pending') return null;
      return (
        <div className="mt-3 space-y-2 border-t border-[#263746] pt-3">
          <p className="text-xs text-[#9db1c1]">
            Want me to create campaigns from these {action.template_count ?? '?'} templates and {action.list_count ?? '?'} lists?
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => dismissActionLocally(message.id)}
              disabled={actionLoadingId !== null}
              className="flex-1 border border-[#344654] bg-[#101820] px-2 py-2 text-xs text-[#9db1c1] hover:bg-[#17232d] disabled:opacity-50"
            >
              Later
            </button>
            <button
              type="button"
              onClick={() => void handlePrepareCampaigns()}
              disabled={actionLoadingId !== null}
              className="flex flex-1 items-center justify-center gap-1.5 border border-[#45c7ef] bg-[#138fb5] px-2 py-2 text-xs font-semibold text-white hover:bg-[#19a8d2] disabled:opacity-50"
            >
              {actionLoadingId === -1 ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
              Create campaigns
            </button>
          </div>
        </div>
      );
    }

    if (action.type === 'create_campaigns_plan') {
      const status = action.status || 'pending';
      if (status === 'completed') {
        return (
          <div className="mt-3 flex items-center gap-2 border-t border-[#263746] pt-2 text-xs text-green-400">
            <Check className="h-3.5 w-3.5" />
            {action.created ?? 0} campaign(s) created
            {!!action.reused && ` · ${action.reused} reused`}
            {!!action.failed && ` · ${action.failed} failed`}
          </div>
        );
      }
      if (status !== 'pending') return null;
      return (
        <div className="mt-3 space-y-2 border-t border-[#263746] pt-3">
          {!!action.skipped_count && (
            <div className="flex items-start gap-2 border border-amber-800 bg-amber-950/30 px-2.5 py-2 text-[11px] leading-relaxed text-amber-200">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                {action.skipped_count} custom value(s) will remain blank
                {!!action.skipped_categories?.length && `: ${action.skipped_categories.map((item) => item.replace(/_/g, ' ')).join(', ')}`}.
              </span>
            </div>
          )}
          <div className="max-h-40 space-y-1 overflow-y-auto">
            {(action.rows || []).map((row: { template: string; list: string; recipients?: number; inboxes?: number }, index: number) => (
              <div key={index} className="flex items-start justify-between gap-2 border border-[#263746] bg-[#0a1118] px-2.5 py-1.5 text-[11px]">
                <span className="min-w-0 truncate text-[#d7e6f2]">{row.list} ← {row.template}</span>
                <span className="flex-shrink-0 text-[#7891a5]">{row.recipients ?? 0} rcpt</span>
              </div>
            ))}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => dismissActionLocally(message.id)}
              disabled={actionLoadingId !== null}
              className="flex-1 border border-[#344654] bg-[#101820] px-2 py-2 text-xs text-[#9db1c1] hover:bg-[#17232d] disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void handleConfirmCampaigns()}
              disabled={actionLoadingId !== null}
              className="flex flex-1 items-center justify-center gap-1.5 border border-emerald-500 bg-emerald-600 px-2 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {actionLoadingId === -1 ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
              Confirm & Create Drafts
            </button>
          </div>
        </div>
      );
    }

    if (action.type === 'delete_selection' || action.type === 'action_selection') {
      const selectionKey = message.id;
      const selectedIds = deleteSelections[selectionKey] || [];
      const hasCandidates = (action.candidates || []).length > 0;
      return (
        <div className="mt-3 space-y-2 border-t border-[#263746] pt-3">
          <p className="text-xs text-[#9db1c1]">Select one or more items, then click Proceed.</p>
          <div className="space-y-1.5">
            {(action.candidates || []).map((candidate) => (
              <label
                key={candidate.id}
                className="flex cursor-pointer items-start gap-2 border border-[#263746] bg-[#0a1118] px-2.5 py-2 transition-colors hover:border-[#45c7ef] hover:bg-[#101d27]"
              >
                <input
                  type="checkbox"
                  className="mt-0.5 h-3.5 w-3.5 accent-[#45c7ef]"
                  checked={selectedIds.includes(candidate.id)}
                  onChange={() => toggleSelection(selectionKey, candidate.id)}
                  disabled={actionLoadingId !== null}
                />
                <span className="min-w-0">
                  <span className="block truncate text-xs font-medium text-[#d7e6f2]">{candidate.label}</span>
                  <span className="block truncate text-[11px] text-[#7891a5]">{candidate.detail}</span>
                </span>
              </label>
            ))}
          </div>
          <button
            type="button"
            disabled={!hasCandidates || selectedIds.length === 0 || actionLoadingId !== null}
            onClick={() => void (action.type === 'action_selection'
              ? runSelectedTool(action, selectedIds, selectionKey)
              : prepareDelete(action, selectedIds, selectionKey))}
            className="flex w-full items-center justify-center gap-1.5 border border-[#45c7ef] bg-[#138fb5] px-2 py-2 text-xs font-semibold text-white transition-colors hover:bg-[#19a8d2] disabled:cursor-not-allowed disabled:border-[#263746] disabled:bg-[#17232d] disabled:text-[#60798d]"
          >
            {actionLoadingId !== null
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Check className="h-3.5 w-3.5" />}
            Proceed
          </button>
        </div>
      );
    }

    const status = action.status || 'pending';
    if (status !== 'pending') {
      return (
        <div className={`mt-3 flex items-center gap-2 border-t border-[#263746] pt-2 text-xs ${
          status === 'completed' ? 'text-green-400' : 'text-[#7891a5]'
        }`}>
          {status === 'completed' ? <Check className="h-3.5 w-3.5" /> : <X className="h-3.5 w-3.5" />}
          Deletion {status}
        </div>
      );
    }

    return (
      <div className="mt-3 space-y-2 border-t border-red-900/60 pt-3">
        <div className="flex items-start gap-2 text-[11px] leading-relaxed text-red-300">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          <span>{action.warning}</span>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void resolveAction(action, 'cancel')}
            disabled={actionLoadingId !== null}
            className="flex-1 border border-[#344654] bg-[#101820] px-2 py-2 text-xs text-[#9db1c1] hover:bg-[#17232d] disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void resolveAction(action, 'confirm')}
            disabled={actionLoadingId !== null}
            className="flex flex-1 items-center justify-center gap-1.5 border border-red-500 bg-red-600 px-2 py-2 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50"
          >
            {actionLoadingId === action.action_ids?.[0]
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Trash2 className="h-3.5 w-3.5" />}
            Confirm delete
          </button>
        </div>
      </div>
    );
  };

  const formatHistoryTime = (value?: string | null) => {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  };

  return (
    <>
      {isOpen && (
        <div className="fixed bottom-3 right-5 top-3 z-50 flex w-96 max-w-[calc(100vw-2rem)] flex-col overflow-hidden border border-[#263746] bg-[#070b0f] shadow-2xl animate-in slide-in-from-bottom-4">
          <div className="flex flex-shrink-0 items-center justify-between bg-gradient-to-r from-indigo-600 to-purple-600 px-4 py-3">
            <div className="flex min-w-0 items-center gap-2">
              <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center bg-white/20">
                <Sparkles className="h-4 w-4 text-white" />
              </div>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-white">Fleet Assistant</h3>
                <p className="truncate text-xs text-white/70">Persistent workspace guide</p>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={handleStartNewConversation}
                title="New conversation"
                aria-label="New conversation"
                className="p-1.5 text-white/80 transition-colors hover:bg-white/10 hover:text-white"
              >
                <Plus className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={toggleHistory}
                title="Conversation history"
                aria-label="Conversation history"
                className={`p-1.5 text-white/80 transition-colors hover:bg-white/10 hover:text-white ${showHistory ? 'bg-white/20 text-white' : ''}`}
              >
                <History className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={closeAssistant}
                title="Close assistant"
                aria-label="Close assistant"
                className="p-1.5 text-white/80 transition-colors hover:bg-white/10 hover:text-white"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
          </div>

          {showImport && (
            <div className="max-h-[52vh] flex-shrink-0 overflow-y-auto border-b border-[#263746] bg-[#040507] p-4">
              <div className="mb-3 flex items-center justify-between">
                <p className="text-sm font-medium text-[#a8c5da]">Bulk import</p>
                <button
                  type="button"
                  onClick={closeImportPanel}
                  title="Close bulk import"
                  aria-label="Close bulk import"
                  className="p-1 text-[#7f9cb5] transition-colors hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              {filesToImport.length > 0 && <MultiListImportPanel files={filesToImport} />}
              {templateFilesToImport.length > 0 && (
                <div className={filesToImport.length > 0 ? 'mt-4 border-t border-[#263746] pt-4' : ''}>
                  <MultiTemplateImportPanel files={templateFilesToImport} />
                </div>
              )}
              {filesToImport.length === 0 && templateFilesToImport.length === 0 && (
                <ImportPanel fileToImport={fileToImport} />
              )}
            </div>
          )}

          {showHistory ? (
            <div className="flex min-h-0 flex-1 flex-col bg-[#070b0f]">
              <div className="flex items-center justify-between border-b border-[#263746] px-4 py-3">
                <div>
                  <p className="text-sm font-semibold text-[#d7e6f2]">Conversation history</p>
                  <p className="text-xs text-[#7891a5]">Resume any previous chat</p>
                </div>
                <button
                  type="button"
                  onClick={handleStartNewConversation}
                  className="flex items-center gap-1 border border-[#345064] bg-[#101820] px-2.5 py-1.5 text-xs text-[#9edcf0] hover:border-[#45c7ef]"
                >
                  <Plus className="h-3.5 w-3.5" /> New
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto p-2">
                {historyLoading && (
                  <div className="flex items-center justify-center gap-2 py-10 text-xs text-[#7891a5]">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading history…
                  </div>
                )}
                {!historyLoading && conversations.length === 0 && (
                  <div className="flex flex-col items-center gap-2 py-12 text-center">
                    <Clock3 className="h-6 w-6 text-[#486174]" />
                    <p className="text-sm text-[#9db1c1]">No saved conversations yet</p>
                    <p className="max-w-[240px] text-xs text-[#60798d]">Your first message starts a conversation automatically.</p>
                  </div>
                )}
                {!historyLoading && conversations.map((conversation) => (
                  <div
                    key={conversation.id}
                    className={`mb-1 flex border transition-colors ${
                      activeConversationId === conversation.id
                        ? 'border-[#45c7ef] bg-[#10212b]'
                        : 'border-transparent bg-[#0a1118] hover:border-[#263746] hover:bg-[#101820]'
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => void loadConversation(conversation.id)}
                      className="min-w-0 flex-1 px-3 py-2.5 text-left"
                    >
                      <span className="block truncate text-sm text-[#d7e6f2]">{conversation.title}</span>
                      <span className="mt-0.5 block text-[11px] text-[#60798d]">{formatHistoryTime(conversation.updated_at)}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => void archiveConversation(conversation.id)}
                      title="Remove from history"
                      aria-label={`Remove ${conversation.title} from history`}
                      className="px-2 text-[#60798d] hover:bg-red-950/30 hover:text-red-400"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto bg-[#080d12] p-4">
              {historyLoading && messages.length === 1 && messages[0].localId === 'welcome' && (
                <div className="flex justify-center py-4">
                  <Loader2 className="h-5 w-5 animate-spin text-[#45c7ef]" />
                </div>
              )}
              {messages.map((message) => (
                <div
                  key={message.id || message.localId}
                  className={`flex gap-2 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  {message.role === 'assistant' && (
                    <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center bg-[#102a38]">
                      <Bot className="h-4 w-4 text-[#45c7ef]" />
                    </div>
                  )}
                  <div
                    className={`max-w-[80%] border px-3.5 py-2.5 text-sm leading-relaxed ${
                      message.role === 'user'
                        ? 'border-[#45c7ef] bg-[#138fb5] text-white'
                        : 'border-[#263746] bg-[#0a1118] text-[#d7e6f2] shadow-sm'
                    }`}
                  >
                    {renderContent(message.content)}
                    {message.role === 'assistant' && renderAction(message)}
                  </div>
                  {message.role === 'user' && (
                    <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center bg-[#138fb5]">
                      <User className="h-4 w-4 text-white" />
                    </div>
                  )}
                </div>
              ))}
              {isLoading && (
                <div className="flex justify-start gap-2">
                  <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center bg-[#102a38]">
                    <Bot className="h-4 w-4 text-[#45c7ef]" />
                  </div>
                  <div className="border border-[#263746] bg-[#0a1118] px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      {[0, 150, 300].map((delay) => (
                        <div
                          key={delay}
                          className="h-2 w-2 animate-bounce bg-[#45c7ef]"
                          style={{ animationDelay: `${delay}ms` }}
                        />
                      ))}
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}

          {!showHistory && (
            <div className="flex-shrink-0 border-t border-[#263746] bg-[#0a1118] p-3">
              <div className="flex items-center gap-2">
                <input
                  ref={importFileRef}
                  type="file"
                  accept=".csv,.xlsx,.txt,.pdf,.json"
                  multiple
                  onChange={handleImportFileSelect}
                  className="hidden"
                />
                <input
                  ref={inputRef}
                  type="text"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask, or provide a tagged bundle value…"
                  className="min-w-0 flex-1 border border-[#263746] bg-[#070b0f] px-3 py-2.5 text-sm text-[#d7e6f2] placeholder-[#60798d] focus:border-[#45c7ef] focus:outline-none"
                  disabled={isLoading}
                />
                <input
                  ref={bundleInputRef}
                  type="file"
                  accept=".zip"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = '';
                    if (file) void handleImportBundle(file);
                  }}
                />
                <button
                  type="button"
                  onClick={() => bundleInputRef.current?.click()}
                  disabled={isLoading || importingBundle}
                  title="Import a bundle (zip with accounts, lists, templates and attachments)"
                  aria-label="Import a bundle (zip with accounts, lists, templates and attachments)"
                  className="flex h-10 w-10 flex-shrink-0 items-center justify-center border border-[#345064] bg-[#101820] text-[#45c7ef] transition-colors hover:border-[#45c7ef] hover:bg-[#17232d] disabled:opacity-50"
                >
                  {importingBundle ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileArchive className="h-4 w-4" />}
                </button>
                <button
                  type="button"
                  onClick={() => importFileRef.current?.click()}
                  disabled={isLoading}
                  title="Upload one file to bulk-import, or select multiple files to create one list/template per file"
                  aria-label="Upload one file to bulk-import, or select multiple files to create one list/template per file"
                  className="flex h-10 w-10 flex-shrink-0 items-center justify-center border border-[#345064] bg-[#101820] text-[#45c7ef] transition-colors hover:border-[#45c7ef] hover:bg-[#17232d] disabled:opacity-50"
                >
                  <FileUp className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => void sendMessage()}
                  disabled={!input.trim() || isLoading}
                  className="flex h-10 w-10 flex-shrink-0 items-center justify-center border border-[#45c7ef] bg-[#138fb5] text-white transition-colors hover:bg-[#19a8d2] disabled:border-[#263746] disabled:bg-[#17232d] disabled:text-[#60798d]"
                >
                  {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {isOpen && previewVariableCategory && activeConversationId && (
        <AssistantTemplatePreview
          key={previewVariableCategory}
          conversationId={activeConversationId}
          category={previewVariableCategory}
          onClose={closeTemplatePreview}
        />
      )}

      {showBubble && !isOpen && !bubbleDismissed && (
        <div className="fixed bottom-[84px] right-5 z-50 animate-in slide-in-from-bottom-2 fade-in duration-300">
          <div className="relative max-w-[220px] border border-[#263746] bg-[#0a1118] px-4 py-2.5 shadow-lg">
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                dismissBubble();
              }}
              className="absolute -right-2 -top-2 flex h-5 w-5 items-center justify-center bg-[#465d6d] text-xs leading-none text-white hover:bg-[#587286]"
            >
              ×
            </button>
            <p className="text-sm text-[#d7e6f2]">👋 <strong>Need help?</strong> Your conversations are saved.</p>
          </div>
        </div>
      )}

      {!isOpen && (
        <button
          type="button"
          onClick={openAssistant}
          className="fixed bottom-5 right-5 z-50 flex h-14 w-14 items-center justify-center bg-gradient-to-r from-indigo-600 to-purple-600 shadow-lg transition-all duration-200 hover:scale-105 hover:from-indigo-700 hover:to-purple-700"
        >
          <MessageCircle className="h-6 w-6 text-white" />
          {hasUnread && <span className="absolute -right-1 -top-1 h-4 w-4 border-2 border-white bg-red-500" />}
        </button>
      )}
    </>
  );
}
