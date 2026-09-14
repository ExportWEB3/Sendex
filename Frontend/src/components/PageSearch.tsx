import { Search, X } from 'lucide-react';

interface PageSearchProps {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: string;
  resultCount: number;
  totalCount: number;
}

export function PageSearch({
  value,
  onChange,
  placeholder,
  label,
  resultCount,
  totalCount,
}: PageSearchProps) {
  const searching = value.trim().length > 0;

  return (
    <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="relative w-full sm:max-w-md">
        <Search
          aria-hidden="true"
          size={16}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
        />
        <input
          type="search"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          aria-label={label}
          className="w-full border border-gray-300 bg-white py-2 pl-9 pr-9 text-sm text-gray-900 placeholder:text-gray-400 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
        />
        {value && (
          <button
            type="button"
            onClick={() => onChange('')}
            aria-label={`Clear ${label.toLowerCase()}`}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-400 transition-colors hover:text-gray-700"
          >
            <X size={14} />
          </button>
        )}
      </div>
      <p className="shrink-0 text-xs text-gray-500" aria-live="polite">
        {searching ? `${resultCount} of ${totalCount} shown` : `${totalCount} total`}
      </p>
    </div>
  );
}
