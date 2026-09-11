import { useState, useRef, useCallback } from 'react';
import { Upload, FileText, CheckCircle, AlertCircle, X, ArrowRight, Loader2, ChevronDown, ChevronUp } from 'lucide-react';
import { useImportPanel } from '../features/imports/useImportPanel';
import type { ImportDetailSectionProps, ImportPanelProps } from '../../typefiles';

const ACCEPTED = '.csv,.xlsx,.txt,.pdf,.json';

export function ImportPanel({ fileToImport }: ImportPanelProps) {
  const {
    stage,
    file,
    preview,
    result,
    errorMessage: errorMsg,
    previewFile: handleFile,
    execute: executeImport,
    reset,
  } = useImportPanel(fileToImport);
  const [dragging, setDragging] = useState(false);
  const [showSesDetail, setShowSesDetail] = useState(false);
  const [showSmtpDetail, setShowSmtpDetail] = useState(false);
  const [showInboxDetail, setShowInboxDetail] = useState(false);
  const [showListDetail, setShowListDetail] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  }, [handleFile]);

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) handleFile(f);
  };

  // ── Render: idle drop zone ──
  if (stage === 'idle') {
    return (
      <div className="space-y-3">
        <p className="text-xs text-[#7f9cb5] leading-relaxed">
          Upload a file to bulk-create SMTP accounts, Resend accounts, inboxes, or lists with recipients.
          Accepted: <span className="text-[#a8c5da] font-mono">.csv .xlsx .txt .pdf .json</span>
        </p>
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => fileRef.current?.click()}
          className={`border-2 border-dashed rounded cursor-pointer flex flex-col items-center justify-center gap-2 py-8 px-4 transition-colors select-none
            ${dragging ? 'border-indigo-500 bg-indigo-500/10' : 'border-[#1d2b38] hover:border-indigo-500/60 hover:bg-[#0d1b26]'}`}
        >
          <Upload className="w-7 h-7 text-[#7f9cb5]" />
          <p className="text-sm text-[#a8c5da]">Drop file here or click to browse</p>
          <p className="text-xs text-[#4a6070]">.csv · .xlsx · .txt · .pdf · .json</p>
          <input ref={fileRef} type="file" accept={ACCEPTED} className="hidden" onChange={onFileChange} />
        </div>
      </div>
    );
  }

  // ── Render: parsing spinner ──
  if (stage === 'previewing') {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-10">
        <Loader2 className="w-7 h-7 text-indigo-400 animate-spin" />
        <p className="text-sm text-[#7f9cb5]">Parsing {file?.name}…</p>
      </div>
    );
  }

  // ── Render: executing spinner ──
  if (stage === 'executing') {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-10">
        <Loader2 className="w-7 h-7 text-green-400 animate-spin" />
        <p className="text-sm text-[#7f9cb5]">Creating records…</p>
      </div>
    );
  }

  // ── Render: error ──
  if (stage === 'error') {
    return (
      <div className="space-y-4">
        <div className="bg-red-900/30 border border-red-700/50 rounded p-3 flex gap-2">
          <AlertCircle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-red-300">{errorMsg}</p>
        </div>
        <button onClick={reset} className="text-xs text-[#7f9cb5] hover:text-white flex items-center gap-1">
          <X className="w-3 h-3" /> Start over
        </button>
      </div>
    );
  }

  // ── Render: done ──
  if (stage === 'done' && result) {
    const s = result.stats;
    return (
      <div className="space-y-4">
        <div className="bg-green-900/30 border border-green-700/40 rounded p-3 flex gap-2">
          <CheckCircle className="w-4 h-4 text-green-400 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-green-300 leading-relaxed">{result.summary}</p>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs">
          {[
            { label: 'Resend accounts', created: s.ses_accounts?.created, skipped: s.ses_accounts?.skipped, errors: s.ses_accounts?.errors?.length, tested: s.ses_accounts?.tested, active: s.ses_accounts?.active, failedTest: s.ses_accounts?.failed },
            { label: 'SMTP accounts', created: s.smtp_accounts?.created, skipped: s.smtp_accounts?.skipped, errors: s.smtp_accounts?.errors?.length },
            { label: 'Inboxes', created: s.inboxes?.created, skipped: s.inboxes?.skipped, errors: s.inboxes?.errors?.length },
            { label: 'Lists', created: s.lists?.created, skipped: s.lists?.skipped, errors: s.lists?.errors?.length },
            { label: 'Recipients', created: s.recipients?.added, skipped: s.recipients?.skipped, errors: s.recipients?.errors?.length },
          ].filter(x => (x.created || 0) + (x.skipped || 0) + (x.errors || 0) > 0).map(x => (
            <div key={x.label} className="bg-[#0a1520] border border-[#1d2b38] rounded p-2">
              <p className="text-[#7f9cb5] mb-1">{x.label}</p>
              <p className="text-white">{x.created} created</p>
              {(x.skipped || 0) > 0 && <p className="text-[#4a6070]">{x.skipped} skipped</p>}
              {(x.errors || 0) > 0 && <p className="text-red-400">{x.errors} error(s)</p>}
              {(x.tested || 0) > 0 && (
                <p className={(x.failedTest || 0) > 0 ? 'text-amber-400' : 'text-green-400'}>
                  {x.active} active{(x.failedTest || 0) > 0 ? ` · ${x.failedTest} failed test` : ''}
                </p>
              )}
            </div>
          ))}
        </div>

        <button onClick={reset} className="text-xs text-[#7f9cb5] hover:text-white flex items-center gap-1 pt-1">
          <Upload className="w-3 h-3" /> Import another file
        </button>
      </div>
    );
  }

  // ── Render: preview ──
  if (stage === 'preview' && preview) {
    const hasErrors = preview.errors.length > 0;
    const total = preview.ses_accounts + preview.smtp_accounts + preview.inboxes + preview.lists;

    return (
      <div className="space-y-4">

        {/* File name */}
        <div className="flex items-center gap-2 text-xs text-[#7f9cb5]">
          <FileText className="w-3.5 h-3.5" />
          <span className="font-mono text-[#a8c5da]">{file?.name}</span>
          <button onClick={reset} className="ml-auto hover:text-red-400 transition-colors">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Summary counts */}
        <div className="grid grid-cols-3 gap-2 text-xs">
          {[
            { label: 'Resend Accounts', count: preview.ses_accounts },
            { label: 'SMTP Accounts', count: preview.smtp_accounts },
            { label: 'Inboxes', count: preview.inboxes },
            { label: 'Lists', count: preview.lists },
            { label: 'Recipients', count: preview.total_recipients },
          ].filter(x => x.count > 0).map(x => (
            <div key={x.label} className="bg-[#0a1520] border border-[#1d2b38] rounded p-2 text-center">
              <p className="text-white font-semibold text-base">{x.count}</p>
              <p className="text-[#7f9cb5]">{x.label}</p>
            </div>
          ))}
        </div>

        {/* Errors */}
        {preview.errors.length > 0 && (
          <div className="bg-red-900/20 border border-red-700/40 rounded p-3 space-y-1">
            <p className="text-xs font-semibold text-red-400 flex items-center gap-1">
              <AlertCircle className="w-3 h-3" /> {preview.errors.length} error(s) — these entries will be skipped
            </p>
            {preview.errors.slice(0, 8).map((e, i) => (
              <p key={i} className="text-xs text-red-300 font-mono pl-4">• {e}</p>
            ))}
            {preview.errors.length > 8 && (
              <p className="text-xs text-red-400 pl-4">…and {preview.errors.length - 8} more</p>
            )}
          </div>
        )}

        {/* Warnings */}
        {preview.warnings.length > 0 && (
          <div className="bg-amber-900/20 border border-amber-700/40 rounded p-3 space-y-1">
            <p className="text-xs font-semibold text-amber-400">{preview.warnings.length} warning(s)</p>
            {preview.warnings.slice(0, 5).map((w, i) => (
              <p key={i} className="text-xs text-amber-300 pl-4">• {w}</p>
            ))}
            {preview.warnings.length > 5 && (
              <p className="text-xs text-amber-400 pl-4">…and {preview.warnings.length - 5} more</p>
            )}
          </div>
        )}

        {/* Detail sections */}
        {preview.ses_preview.length > 0 && (
          <DetailSection
            label={`Resend Accounts (${preview.ses_accounts})`}
            open={showSesDetail}
            toggle={() => setShowSesDetail(v => !v)}
          >
            {preview.ses_preview.map((a, i) => (
              <div key={i} className="text-xs text-[#a8c5da] pl-2 border-l border-[#1d2b38]">
                <span className="text-white">{a.account_name}</span>
                {' · '}<span className="font-mono">{a.from_email_prefix}@…</span>
                {' · '}{a.from_name}
              </div>
            ))}
          </DetailSection>
        )}

        {preview.smtp_preview.length > 0 && (
          <DetailSection
            label={`SMTP Accounts (${preview.smtp_accounts})`}
            open={showSmtpDetail}
            toggle={() => setShowSmtpDetail(v => !v)}
          >
            {preview.smtp_preview.map((a, i) => (
              <div key={i} className="text-xs text-[#a8c5da] pl-2 border-l border-[#1d2b38]">
                <span className="text-white">{a.account_name}</span>
                {' · '}<span className="font-mono">{a.from_email}</span>
                {' · '}{a.host}:{a.port}
                {a.has_imap && <span className="text-green-400 ml-1">IMAP✓</span>}
              </div>
            ))}
          </DetailSection>
        )}

        {preview.inbox_preview.length > 0 && (
          <DetailSection
            label={`Inboxes (${preview.inboxes})`}
            open={showInboxDetail}
            toggle={() => setShowInboxDetail(v => !v)}
          >
            {preview.inbox_preview.map((b, i) => (
              <div key={i} className="text-xs text-[#a8c5da] pl-2 border-l border-[#1d2b38]">
                <span className="font-mono text-white">{b.email}</span>
                {' → '}{b.account_name}
                {' · Group '}{b.group}
                {b.start_warmup && <span className="text-indigo-400 ml-1">warmup✓</span>}
              </div>
            ))}
          </DetailSection>
        )}

        {preview.list_preview.length > 0 && (
          <DetailSection
            label={`Lists (${preview.lists})`}
            open={showListDetail}
            toggle={() => setShowListDetail(v => !v)}
          >
            {preview.list_preview.map((l, i) => (
              <div key={i} className="text-xs text-[#a8c5da] pl-2 border-l border-[#1d2b38]">
                <span className="text-white">{l.list_name}</span>
                {' · '}{l.recipient_count} recipient{l.recipient_count !== 1 ? 's' : ''}
                {l.list_description && <span className="text-[#4a6070] ml-1">— {l.list_description}</span>}
              </div>
            ))}
          </DetailSection>
        )}

        {/* Nothing found */}
        {total === 0 && !hasErrors && (
          <p className="text-xs text-[#4a6070] text-center py-4">
            Nothing was detected in this file. Check the format and try again.
          </p>
        )}

        {/* Actions */}
        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={reset}
            className="text-xs text-[#7f9cb5] hover:text-white transition-colors"
          >
            Cancel
          </button>
          <button
            disabled={total === 0}
            onClick={executeImport}
            className="ml-auto flex items-center gap-1.5 text-sm px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500 disabled:bg-[#1d2b38] disabled:text-[#4a6070] text-white transition-colors"
          >
            Import {total > 0 ? `(${total} item${total !== 1 ? 's' : ''})` : ''} <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    );
  }

  return null;
}

function DetailSection({ label, open, toggle, children }: ImportDetailSectionProps) {
  return (
    <div className="border border-[#1d2b38] rounded">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between px-3 py-2 text-xs text-[#7f9cb5] hover:text-white transition-colors"
      >
        <span>{label}</span>
        {open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-1.5 border-t border-[#1d2b38]" style={{ paddingTop: '0.5rem' }}>
          {children}
        </div>
      )}
    </div>
  );
}
