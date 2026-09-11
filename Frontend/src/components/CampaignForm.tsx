import { useEffect, useMemo, useRef } from 'react';
import { Shuffle, Paperclip, RefreshCw, Trash2, Eye, Code, CheckCircle2, SplitSquareHorizontal, Merge } from 'lucide-react';
import type { CampaignFormProps, TemplateOption } from '../../typefiles';
import { useCampaignForm } from '../features/campaigns/useCampaignForm';
import {
  renderTemplatePreview,
  templateVariableLabel,
} from '../features/campaigns/campaign-template-utils';
import { sanitizeEmailHtml, sanitizeInlineHtml } from '../utils/sanitize-html';
import { formatFileSize, getFileIcon, isImageType } from './attachment-utils';

const EMPTY_TEMPLATES: TemplateOption[] = [];
const EMPTY_TEMPLATE_IDS: number[] = [];

export function CampaignForm({
  editingCampaign,
  templates = EMPTY_TEMPLATES,
  multiTemplateIds = EMPTY_TEMPLATE_IDS,
  onMultiTemplateToggle,
  lists,
  inboxes,
  uploadedAttachments,
  uploading,
  onFileSelect,
  onRemoveAttachment,
  onSubmit,
  onCancel,
}: CampaignFormProps) {
  const isEditing = !!editingCampaign;
  const {
    values: {
      bodyHtml,
      subject: subjectValue,
      showRawHtml,
      customValues: customVarValues,
      perTemplateValues: perTemplateVarValues,
      splitVariables: splitVars,
      selectedInboxIds,
    },
    selection: {
      mode: templateMode,
      selectedTemplates,
      templateAttachments,
    },
    variables: {
      all: allVars,
      custom: customVars,
      standard: standardVarsInUse,
      rotationCustom: rotationCustomVars,
      rotationStandard: rotationStandardVars,
      templateMap: varTemplateMap,
      rotationPreviewValues,
    },
    preview: {
      html: previewHtml,
      subject: previewSubject,
      isHtml: isHtmlTemplate,
    },
    actions: {
      toggleTemplate: handleTemplateToggle,
      setBodyHtml,
      setSubject: setSubjectValue,
      setRawHtml: setShowRawHtml,
      setCustomValue,
      setTemplateValue,
      toggleSplitVariable,
      toggleInbox,
    },
  } = useCampaignForm({
    editingCampaign,
    templates,
    multiTemplateIds,
    onMultiTemplateToggle,
  });
  const hiddenBodyRef = useRef<HTMLInputElement>(null);
  const sanitizedPreviewSubject = useMemo(
    () => sanitizeInlineHtml(previewSubject),
    [previewSubject],
  );
  const sanitizedPreviewDocument = useMemo(
    () => sanitizeEmailHtml(previewHtml),
    [previewHtml],
  );
  const sanitizedPlainPreview = useMemo(
    () => sanitizeEmailHtml(previewHtml.replace(/\n/g, '<br/>')),
    [previewHtml],
  );

  // Sync hidden field
  useEffect(() => {
    if (hiddenBodyRef.current) {
      hiddenBodyRef.current.value = bodyHtml;
    }
  }, [bodyHtml]);

  return (
    <form
      key={editingCampaign?.id || 'new'}
      onSubmit={(e) => {
        if (hiddenBodyRef.current) hiddenBodyRef.current.value = bodyHtml;
        // Inject per-template variable values as hidden fields
        if (templateMode === 'rotation' && splitVars.size > 0) {
          for (const [varName, templateVals] of Object.entries(perTemplateVarValues)) {
            if (splitVars.has(varName)) {
              for (const [tid, val] of Object.entries(templateVals)) {
                if (val.trim()) {
                  const input = document.createElement('input');
                  input.type = 'hidden';
                  input.name = `tplvar_per_${varName}_${tid}`;
                  input.value = val.trim();
                  e.currentTarget.appendChild(input);
                }
              }
            }
          }
        }
        onSubmit(e);
      }}
      className="py-1"
    >
      <input type="hidden" name="body_html" ref={hiddenBodyRef} defaultValue={bodyHtml} />
      {/* In rotation mode, subject/body fields are hidden but still needed by the API */}
      {templateMode === 'rotation' && <input type="hidden" name="subject" value={subjectValue} />}

      {/* 2-column layout for rotation mode, single column otherwise */}
      <div className={`${templateMode === 'rotation' ? 'grid grid-cols-1 lg:grid-cols-2 gap-6' : ''}`}>
        {/* LEFT COLUMN: Form fields */}
        <div className="space-y-4">

      {/* Campaign Name */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Campaign Name</label>
        <input
          type="text"
          name="name"
          defaultValue={editingCampaign?.name || ''}
          required
          className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500"
        />
      </div>

      {/* Template Selector — unified checklist */}
      {templates.length > 0 && (
        <div className={`border rounded-lg p-3 space-y-2 ${
          templateMode === 'rotation'
            ? 'bg-gradient-to-r from-violet-50 to-indigo-50 border-violet-200'
            : templateMode === 'single'
              ? 'bg-gradient-to-r from-emerald-50 to-teal-50 border-emerald-200'
              : 'bg-gray-50 border-gray-200'
        }`}>
          <div className="flex items-center justify-between">
            <p className={`text-sm font-semibold flex items-center gap-1.5 ${
              templateMode === 'rotation' ? 'text-violet-800' : templateMode === 'single' ? 'text-emerald-800' : 'text-gray-700'
            }`}>
              {templateMode === 'rotation' ? <><Shuffle size={14} /> Template Rotation</>
                : templateMode === 'single' ? <><CheckCircle2 size={14} /> Single Template</>
                : 'Select Templates'}
            </p>
            {templateMode === 'rotation' && (
              <span className="text-[10px] text-violet-500 bg-violet-100 px-2 py-0.5 rounded-full font-medium">
                {multiTemplateIds.length} templates · 4-6 pattern
              </span>
            )}
            {templateMode === 'single' && (
              <span className="text-[10px] text-emerald-600 bg-emerald-100 px-2 py-0.5 rounded-full font-medium">
                1 template selected
              </span>
            )}
          </div>
          <p className={`text-xs ${
            templateMode === 'rotation' ? 'text-violet-600' : templateMode === 'single' ? 'text-emerald-600' : 'text-gray-500'
          }`}>
            {templateMode === 'rotation'
              ? 'Rotation active: 4 emails with template A → 6 with B → 4 with C → 6 with D... Subject & body come from each template.'
              : templateMode === 'single'
                ? 'Using selected template\'s subject & body for all emails.'
                : 'Check 1 template to use it, or 2+ to enable rotation. Leave empty to write from scratch.'}
          </p>
          <div className="grid grid-cols-1 gap-1.5 max-h-[200px] overflow-y-auto pr-1">
            {templates.map(t => {
              const isSelected = multiTemplateIds.includes(t.id);
              return (
                <label
                  key={t.id}
                  className={`flex items-center gap-2 p-2 rounded-lg border cursor-pointer transition-all ${
                    isSelected
                      ? templateMode === 'rotation'
                        ? 'bg-white border-violet-400 shadow-sm ring-1 ring-violet-200'
                        : 'bg-white border-emerald-400 shadow-sm ring-1 ring-emerald-200'
                      : 'bg-white/60 border-transparent hover:bg-white hover:border-gray-200'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => handleTemplateToggle(t.id)}
                    className={`rounded ${
                      templateMode === 'rotation'
                        ? 'border-violet-300 text-violet-600 focus:ring-violet-500'
                        : 'border-emerald-300 text-emerald-600 focus:ring-emerald-500'
                    }`}
                  />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-medium text-gray-800 truncate block">{t.name}</span>
                    <span className="text-[10px] text-gray-500">{t.category} · {t.subject_line.slice(0, 50)}{t.subject_line.length > 50 ? '...' : ''}</span>
                  </div>
                  {isSelected && multiTemplateIds.length >= 2 && (
                    <span className="text-[10px] font-bold text-violet-600 bg-violet-100 w-5 h-5 rounded-full flex items-center justify-center">
                      {multiTemplateIds.indexOf(t.id) + 1}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
          {multiTemplateIds.length >= 2 && (
            <div className="flex items-center gap-1.5 pt-1 flex-wrap">
              <span className="text-[10px] uppercase tracking-wide text-violet-500 font-medium">Rotation Preview:</span>
              {multiTemplateIds.map((tid, idx) => {
                const tmpl = templates.find(t => t.id === tid);
                const batchSize = idx % 2 === 0 ? 4 : 6;
                return (
                  <span key={tid} className="inline-flex items-center gap-0.5 text-[10px] bg-white border border-violet-200 px-1.5 py-0.5 rounded-full">
                    <span className="font-semibold text-violet-700">{batchSize}×</span>
                    <span className="text-gray-600 truncate max-w-[80px]">{tmpl?.name || `#${tid}`}</span>
                    {idx < multiTemplateIds.length - 1 && <span className="text-violet-300 ml-0.5">→</span>}
                  </span>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Subject Line — hidden in rotation mode */}
      {templateMode !== 'rotation' && (
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Subject Line</label>
        <input
          type="text"
          name="subject"
          value={subjectValue}
          onChange={(e) => setSubjectValue(e.target.value)}
          required
          className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500"
          placeholder="Use {{first_name}} for personalization"
        />
        {allVars.length > 0 && subjectValue && (
          <div className="mt-1 px-3 py-1.5 bg-gray-50 rounded text-sm text-gray-600 border border-gray-100">
            <span className="text-[10px] uppercase tracking-wide text-gray-400 mr-2">Preview:</span>
            <span dangerouslySetInnerHTML={{ __html: sanitizedPreviewSubject }} />
          </div>
        )}
      </div>
      )}

      {/* Email Body — hidden in rotation mode */}
      {templateMode !== 'rotation' && (
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="block text-sm font-medium text-gray-700">Email Body</label>
          <div className="flex items-center gap-0">
            <button
              type="button"
              onClick={() => setShowRawHtml(false)}
              className={`px-2.5 py-1 text-xs rounded-l-md border transition-colors ${
                !showRawHtml
                  ? 'bg-indigo-50 text-indigo-700 border-indigo-200 font-medium'
                  : 'bg-white text-gray-500 border-gray-200 hover:bg-gray-50'
              }`}
            >
              <Eye size={12} className="inline mr-1 -mt-px" />Preview
            </button>
            <button
              type="button"
              onClick={() => setShowRawHtml(true)}
              className={`px-2.5 py-1 text-xs rounded-r-md border-t border-b border-r transition-colors ${
                showRawHtml
                  ? 'bg-indigo-50 text-indigo-700 border-indigo-200 font-medium'
                  : 'bg-white text-gray-500 border-gray-200 hover:bg-gray-50'
              }`}
            >
              <Code size={12} className="inline mr-1 -mt-px" />HTML
            </button>
          </div>
        </div>

        {showRawHtml ? (
          <textarea
            value={bodyHtml}
            onChange={(e) => setBodyHtml(e.target.value)}
            rows={14}
            required={!bodyHtml}
            style={{ minHeight: '200px', maxHeight: '500px' }}
            className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 font-mono text-xs resize-y overflow-y-auto bg-gray-900 text-green-400"
            placeholder="<p>Hi {{first_name}},</p>"
          />
        ) : (
          <div className="border rounded-lg overflow-hidden bg-white">
            {bodyHtml ? (
              isHtmlTemplate ? (
                <iframe
                  title="Email Preview"
                  className="w-full border-0"
                  style={{ minHeight: '300px', maxHeight: '500px', height: '400px' }}
                  sandbox=""
                  srcDoc={sanitizedPreviewDocument}
                />
              ) : (
                <div className="p-4 text-sm text-gray-700 whitespace-pre-wrap leading-relaxed" style={{ minHeight: '120px' }}>
                  <span dangerouslySetInnerHTML={{ __html: sanitizedPlainPreview }} />
                </div>
              )
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-gray-400">
                <Eye size={32} className="mb-2 opacity-50" />
                <p className="text-sm">Select a template or switch to HTML to start writing</p>
              </div>
            )}
          </div>
        )}
      </div>
      )}

      {/* Template Variables — single/scratch mode */}
      {templateMode !== 'rotation' && (customVars.length > 0 || standardVarsInUse.length > 0) && (
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 space-y-3">
          <p className="text-sm font-medium text-slate-700">Template Variables</p>

          {standardVarsInUse.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {standardVarsInUse.map(v => (
                <span key={v} className="px-2 py-0.5 bg-indigo-50 text-indigo-600 text-xs rounded-full border border-indigo-100">
                  {`{{${v}}}`} <span className="text-indigo-400">→ auto-filled</span>
                </span>
              ))}
            </div>
          )}

          {customVars.length > 0 && (
            <div className="space-y-2 pt-1">
              <p className="text-xs text-amber-700 font-medium">Custom variables — fill in values:</p>
              {customVars.map(cv => (
                <div key={cv.name} className="flex items-center gap-2">
                  <span className="text-xs text-gray-500 font-mono whitespace-nowrap min-w-[100px]">{`{{${cv.name}}}`}</span>
                  <input
                    type="text"
                    name={`tplvar_${cv.name}`}
                    value={customVarValues[cv.name] || ''}
                    onChange={(e) => setCustomValue(cv.name, e.target.value)}
                    className="flex-1 px-2.5 py-1 text-sm border rounded-lg focus:ring-2 focus:ring-amber-400"
                    placeholder={cv.fallback || templateVariableLabel(cv.name)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Rotation Mode: Merged Template Variables + Stacked Preview */}
      {templateMode === 'rotation' && (rotationCustomVars.length > 0 || rotationStandardVars.length > 0) && (
        <div className="bg-gradient-to-br from-violet-50 to-indigo-50 border border-violet-200 rounded-xl p-4 space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-violet-800 flex items-center gap-1.5">
              <Shuffle size={14} /> Template Variables
              <span className="text-[10px] text-violet-500 bg-violet-100 px-2 py-0.5 rounded-full font-medium ml-1">
                across {selectedTemplates.length} templates
              </span>
            </p>
          </div>

          {/* Standard variables — auto-filled */}
          {rotationStandardVars.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {rotationStandardVars.map(v => (
                <span key={v} className="px-2 py-0.5 bg-indigo-50 text-indigo-600 text-xs rounded-full border border-indigo-100">
                  {`{{${v}}}`} <span className="text-indigo-400">→ auto-filled</span>
                </span>
              ))}
            </div>
          )}

          {/* Custom variables — shared or split per template */}
          {rotationCustomVars.length > 0 && (
            <div className="space-y-3 pt-1">
              <p className="text-xs text-violet-700 font-medium">Custom variables — fill in values for all templates:</p>
              {rotationCustomVars.map(cv => {
                const usedByTemplates = varTemplateMap[cv.name] || [];
                const isSplit = splitVars.has(cv.name);
                const usedByMultiple = usedByTemplates.length > 1;

                return (
                  <div key={cv.name} className="space-y-1.5">
                    {/* Variable name + template pills + split button */}
                    <div className="flex items-start gap-2">
                      <span className="text-xs text-violet-700 font-mono whitespace-nowrap font-semibold pt-0.5">{`{{${cv.name}}}`}</span>
                      <div className="flex flex-wrap items-center gap-1.5 flex-1 min-w-0">
                        {/* Template usage pills */}
                        {usedByTemplates.map(tid => {
                          const tmpl = templates.find(t => t.id === tid);
                          const idx = multiTemplateIds.indexOf(tid);
                          return (
                            <span key={tid} className="inline-flex items-center text-[9px] bg-white text-violet-600 border border-violet-200 px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap">
                              T{idx + 1}: {tmpl?.name?.slice(0, 12) || `#${tid}`}{(tmpl?.name?.length || 0) > 12 ? '…' : ''}
                            </span>
                          );
                        })}
                        {/* Split/Merge toggle */}
                        {usedByMultiple && (
                        <button
                          type="button"
                          onClick={() => toggleSplitVariable(cv.name)}
                          className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full transition-all ${
                            isSplit
                              ? 'bg-amber-100 text-amber-700 border border-amber-300 hover:bg-amber-200'
                              : 'bg-white text-violet-600 border border-violet-200 hover:bg-violet-100'
                          }`}
                          title={isSplit ? 'Merge: use same value for all templates' : 'Split: set different values per template'}
                        >
                          {isSplit ? <><Merge size={10} /> Merge</> : <><SplitSquareHorizontal size={10} /> Different per template</>}
                        </button>
                      )}
                      </div>
                    </div>

                    {/* Input(s) */}
                    {!isSplit ? (
                      /* Shared input — one value for all templates */
                      <input
                        type="text"
                        name={`tplvar_${cv.name}`}
                        value={customVarValues[cv.name] || ''}
                        onChange={(e) => setCustomValue(cv.name, e.target.value)}
                        className="w-full px-2.5 py-1.5 text-sm border border-violet-200 rounded-lg focus:ring-2 focus:ring-violet-400 bg-white"
                        placeholder={cv.fallback || templateVariableLabel(cv.name)}
                      />
                    ) : (
                      /* Split inputs — one per template */
                      <div className="space-y-1 pl-3 border-l-2 border-amber-300">
                        {usedByTemplates.map(tid => {
                          const tmpl = templates.find(t => t.id === tid);
                          const idx = multiTemplateIds.indexOf(tid);
                          return (
                            <div key={tid} className="flex items-center gap-2">
                              <span className="text-[10px] text-amber-700 font-medium whitespace-nowrap min-w-[80px]">
                                T{idx + 1}: {tmpl?.name?.slice(0, 12) || `#${tid}`}
                              </span>
                              <input
                                type="text"
                                value={perTemplateVarValues[cv.name]?.[tid] || ''}
                                onChange={(e) => setTemplateValue(cv.name, tid, e.target.value)}
                                className="flex-1 px-2.5 py-1 text-sm border border-amber-200 rounded-lg focus:ring-2 focus:ring-amber-400 bg-white"
                                placeholder={cv.fallback || `${templateVariableLabel(cv.name)} for ${tmpl?.name || 'this template'}`}
                              />
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Recipient List */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Recipient List</label>
        <select
          name="list_id"
          defaultValue={editingCampaign?.list_id || ''}
          required
          className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500"
        >
          <option value="">Select a list</option>
          {lists.map(list => (
            <option key={list.id} value={list.id}>
              {list.name} ({list.recipient_count || 0} recipients)
            </option>
          ))}
        </select>
      </div>

      {/* Inboxes */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Inboxes</label>
        <input type="hidden" name="inbox_ids" value={selectedInboxIds.join(',')} />
        <div className="border rounded-lg p-2 space-y-1 max-h-40 overflow-y-auto bg-white">
          {inboxes.length === 0 ? (
            <p className="text-xs text-gray-400 py-1 px-1">No inboxes available</p>
          ) : (
            inboxes.map(inbox => (
              <label
                key={inbox.id}
                className={`flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer transition-colors ${
                  selectedInboxIds.includes(inbox.id)
                    ? 'bg-indigo-50 border border-indigo-200'
                    : 'hover:bg-gray-50 border border-transparent'
                }`}
              >
                <input
                  type="checkbox"
                  checked={selectedInboxIds.includes(inbox.id)}
                  onChange={(e) => toggleInbox(inbox.id, e.target.checked)}
                  className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
                <span className="text-sm text-gray-700 truncate">{inbox.email}</span>
              </label>
            ))
          )}
        </div>
        {selectedInboxIds.length === 0 && (
          <p className="text-xs text-red-500 mt-1">Select at least one inbox</p>
        )}
        {selectedInboxIds.length > 1 && (
          <p className="text-xs text-indigo-600 mt-1">{selectedInboxIds.length} inboxes selected — recipients will be distributed round-robin</p>
        )}
      </div>

      {/* Reply-To Email */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Send Window <span className="text-gray-400 font-normal">(timezone)</span>
        </label>
        <select
          name="send_timezone"
          defaultValue={editingCampaign?.send_timezone || 'US/Eastern'}
          required
          className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500"
        >
          <option value="US/Eastern">Eastern Time (9 AM – 5 PM ET)</option>
          <option value="US/Pacific">Pacific Time (9 AM – 5 PM PT)</option>
        </select>
        <p className="text-xs text-gray-500 mt-1">
          Emails will only be sent during business hours (Mon–Fri) in this timezone.
        </p>
      </div>

      {/* Reply-To Email */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Reply-To Email <span className="text-gray-400 font-normal">(optional)</span>
        </label>
        <input
          type="email"
          name="reply_to_email"
          defaultValue={editingCampaign?.reply_to_email || ''}
          className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500"
          placeholder="replies@yourdomain.com"
        />
        <p className="text-xs text-gray-500 mt-1">
          Where recipients' replies go. Uses inbox default if set, or sender address if empty.
        </p>
      </div>

      {/* Template Attachments (read-only, from template) */}
      {templateAttachments.length > 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
          <p className="text-sm font-medium text-blue-700 mb-2 flex items-center gap-1.5">
            <Paperclip size={14} /> Template Attachments
            <span className="text-blue-400 font-normal text-xs">(automatically included from template)</span>
          </p>
          <div className="space-y-1.5">
            {templateAttachments.map((att, idx) => (
              <div key={idx} className="flex items-center gap-2 p-2 bg-white rounded-lg border border-blue-100">
                {isImageType(att.content_type) ? (
                  <img src={`/uploads/${att.stored_name}`} alt={att.filename} className="w-8 h-8 object-cover rounded" />
                ) : (
                  <div className="w-8 h-8 flex items-center justify-center bg-blue-50 rounded">
                    {getFileIcon(att.content_type)}
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-gray-800 truncate">{att.filename}</p>
                  <p className="text-[10px] text-gray-500">{formatFileSize(att.size)}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Attachments */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          <span className="flex items-center gap-1.5">
            <Paperclip size={14} /> Attachments <span className="text-gray-400 font-normal">(optional)</span>
          </span>
        </label>
        <div className="border-2 border-dashed border-gray-300 rounded-lg p-3 hover:border-indigo-400 transition-colors">
          <input
            type="file"
            multiple
            onChange={onFileSelect}
            className="hidden"
            id="attachment-input"
            accept="image/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip,.rar"
          />
          <label htmlFor="attachment-input" className="flex flex-col items-center cursor-pointer py-2">
            {uploading ? (
              <div className="flex items-center gap-2 text-sm text-indigo-600">
                <RefreshCw className="w-4 h-4 animate-spin" /> Uploading...
              </div>
            ) : (
              <>
                <Paperclip className="w-6 h-6 text-gray-400 mb-1" />
                <span className="text-sm text-gray-600">Click to attach files</span>
                <span className="text-xs text-gray-400 mt-0.5">Images, videos, documents (max 25MB each)</span>
              </>
            )}
          </label>
        </div>

        {uploadedAttachments.length > 0 && (
          <div className="mt-2 space-y-1.5">
            {uploadedAttachments.map((att, idx) => (
              <div key={idx} className="flex items-center gap-2 p-2 bg-gray-50 rounded-lg border border-gray-200">
                {isImageType(att.content_type) ? (
                  <img src={`/uploads/${att.stored_name}`} alt={att.filename} className="w-10 h-10 object-cover rounded" />
                ) : (
                  <div className="w-10 h-10 flex items-center justify-center bg-gray-100 rounded">
                    {getFileIcon(att.content_type)}
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-gray-800 truncate">{att.filename}</p>
                  <p className="text-[10px] text-gray-500">{formatFileSize(att.size)}</p>
                </div>
                <button
                  type="button"
                  onClick={() => onRemoveAttachment(idx)}
                  className="p-1 text-red-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex justify-end gap-3 pt-4">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
        >
          {isEditing ? 'Update' : 'Create'}
        </button>
      </div>
        </div>{/* end left column */}

        {/* RIGHT COLUMN: Stacked live preview (rotation mode only) */}
        {templateMode === 'rotation' && selectedTemplates.length >= 2 && (
          <div className="lg:sticky lg:top-0 lg:self-start"
            style={{ animation: 'previewSlideIn 0.4s cubic-bezier(0.22, 1, 0.36, 1) both', willChange: 'transform, opacity' }}
          >
            <div className="border border-violet-200 rounded-xl overflow-hidden bg-white shadow-sm">
              <div className="bg-gradient-to-r from-violet-50 to-indigo-50 px-4 py-2.5 border-b border-violet-200 flex items-center gap-2">
                <Eye size={14} className="text-violet-600" />
                <span className="text-sm font-semibold text-violet-800">Live Preview</span>
                <span className="text-[10px] text-violet-500 bg-violet-100 px-2 py-0.5 rounded-full">
                  {selectedTemplates.length} templates
                </span>
              </div>
              <div className="overflow-y-auto divide-y divide-violet-100" style={{ maxHeight: 'calc(90vh - 120px)' }}>
                {selectedTemplates.map((tmpl, idx) => {
                  const vals = rotationPreviewValues[tmpl.id] || {};
                  const previewedSubject = sanitizeInlineHtml(
                    renderTemplatePreview(tmpl.subject_line, vals),
                  );
                  const rawPreviewedBody = renderTemplatePreview(tmpl.html_content, vals);
                  const previewedBody = sanitizeEmailHtml(rawPreviewedBody);
                  const previewedPlainBody = sanitizeEmailHtml(rawPreviewedBody.replace(/\n/g, '<br/>'));
                  const isHtml = tmpl.html_content.includes('<') && tmpl.html_content.includes('>');
                  const batchSize = idx % 2 === 0 ? 4 : 6;

                  return (
                    <div key={tmpl.id} className="px-3 py-3">
                      {/* Template header */}
                      <div className="flex items-center gap-2 mb-2">
                        <span className="text-[10px] font-bold text-white bg-violet-600 w-5 h-5 rounded-full flex items-center justify-center shrink-0">
                          {idx + 1}
                        </span>
                        <span className="text-sm font-semibold text-gray-800 truncate">{tmpl.name}</span>
                        <span className="text-[10px] text-violet-500 bg-violet-50 border border-violet-200 px-1.5 py-0.5 rounded-full shrink-0">
                          {batchSize}× per cycle
                        </span>
                      </div>

                      {/* Subject preview */}
                      <div className="px-3 py-1.5 bg-gray-50 rounded-t-lg border border-gray-200 border-b-0">
                        <span className="text-[10px] uppercase tracking-wide text-gray-400 mr-2">Subject:</span>
                        <span className="text-sm text-gray-700" dangerouslySetInnerHTML={{ __html: previewedSubject }} />
                      </div>

                      {/* Body preview */}
                      <div className="border border-gray-200 rounded-b-lg overflow-hidden bg-white">
                        {isHtml ? (
                          <iframe
                            title={`Preview: ${tmpl.name}`}
                            className="w-full border-0"
                            style={{ height: '260px' }}
                            sandbox=""
                            srcDoc={previewedBody}
                          />
                        ) : (
                          <div className="p-3 text-sm text-gray-700 whitespace-pre-wrap leading-relaxed" style={{ maxHeight: '260px', overflowY: 'auto' }}>
                            <span dangerouslySetInnerHTML={{ __html: previewedPlainBody }} />
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>{/* end grid */}
    </form>
  );
}
