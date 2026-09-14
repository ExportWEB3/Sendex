export type PageSearchValue = string | number | boolean | null | undefined;

export function matchesPageSearch(
  search: string,
  values: readonly PageSearchValue[],
): boolean {
  const terms = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;

  const searchableText = values
    .filter((value) => value !== null && value !== undefined)
    .map((value) => String(value).toLowerCase())
    .join(' ');

  return terms.every((term) => searchableText.includes(term));
}
