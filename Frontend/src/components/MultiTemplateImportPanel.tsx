import { CheckCircle, AlertCircle, FileText, Loader2, ArrowRight } from 'lucide-react';
import { useMultiFileImport } from '../features/imports/useMultiFileImport';
import type { MultiImportPanelProps, TemplateImportFileResult } from '../../typefiles';

function deriveTemplateName(filename: string): string {
  const withoutExt = filename.replace(/\.[^/.]+$/, '');
  return withoutExt.trim() || filename;
}

export function MultiTemplateImportPanel({ files }: MultiImportPanelProps) {
  const {
    stage,
    results,
    errorMessage: errorMsg,
    run: runImport,
  } = useMultiFileImport<TemplateImportFileResult>('email-templates/import-multi', files);

  if (stage === 'idle') {
    return (
      <div className="space-y-3">
        <p className="text-xs text-[#7f9cb5] leading-relaxed">
          Each file becomes its own email template, named after the file. The first
          "Subject:" line becomes the subject; everything after it becomes the body.
          Category is set to "custom".
        </p>
        <div className="space-y-1.5">
          {files.map((f, i) => (
            <div key={i} className="flex items-center gap-2 border border-[#1d2b38] bg-[#0a1520] rounded p-2 text-xs">
              <FileText className="w-3.5 h-3.5 text-[#7f9cb5] flex-shrink-0" />
              <span className="font-mono text-[#a8c5da] truncate">{f.name}</span>
              <ArrowRight className="w-3 h-3 text-[#4a6070] flex-shrink-0" />
              <span className="text-white truncate">{deriveTemplateName(f.name)}</span>
            </div>
          ))}
        </div>
        <button
          onClick={runImport}
          className="flex items-center gap-1.5 text-sm px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
        >
          Import {files.length} file{files.length !== 1 ? 's' : ''} <ArrowRight className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  }

  if (stage === 'importing') {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-10">
        <Loader2 className="w-7 h-7 text-green-400 animate-spin" />
        <p className="text-sm text-[#7f9cb5]">Creating templates…</p>
      </div>
    );
  }

  if (stage === 'error') {
    return (
      <div className="bg-red-900/30 border border-red-700/50 rounded p-3 flex gap-2">
        <AlertCircle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
        <p className="text-sm text-red-300">{errorMsg}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {results.map((r, i) => (
        <div key={i} className="border border-[#1d2b38] bg-[#0a1520] rounded p-2 text-xs space-y-1">
          <div className="flex items-center gap-2">
            {r.template_id === null
              ? <AlertCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
              : <CheckCircle className="w-3.5 h-3.5 text-green-400 flex-shrink-0" />}
            <span className="font-mono text-[#a8c5da] truncate">{r.filename}</span>
          </div>
          {r.template_id !== null ? (
            <p className="text-white pl-5">
              Created template <span className="text-[#a8c5da]">{r.template_name}</span>
              {' · '}subject: "{r.subject_line}"
            </p>
          ) : (
            <p className="text-red-400 pl-5">No template created</p>
          )}
          {r.errors.map((e, j) => (
            <p key={j} className="text-red-300 pl-5">• {e}</p>
          ))}
        </div>
      ))}
    </div>
  );
}
