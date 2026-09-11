import type { CampaignTemplateVariable } from '../../../typefiles';

export const STANDARD_TEMPLATE_VARIABLES = new Set([
  'first_name',
  'last_name',
  'full_name',
  'email',
  'company',
  'title',
  'sender_name',
  'sender_email',
  'unsubscribe_link',
]);

const TEMPLATE_VARIABLE_PATTERN = /\{\{\s*([a-zA-Z0-9_-]+)\s*(?:\|\s*([^}]*?)\s*)?\}\}/g;

export const normalizeTemplateVariable = (name: string) => (
  name.trim().toLowerCase().replace(/-/g, '_')
);

export function extractCustomTemplateVariables(text: string): CampaignTemplateVariable[] {
  const seen = new Set<string>();
  const result: CampaignTemplateVariable[] = [];
  for (const match of text.matchAll(TEMPLATE_VARIABLE_PATTERN)) {
    const name = normalizeTemplateVariable(match[1]);
    if (!STANDARD_TEMPLATE_VARIABLES.has(name) && !seen.has(name)) {
      seen.add(name);
      result.push({ name, fallback: match[2] || '' });
    }
  }
  return result;
}

export function extractAllTemplateVariables(text: string): string[] {
  const variables = new Set<string>();
  for (const match of text.matchAll(TEMPLATE_VARIABLE_PATTERN)) {
    variables.add(normalizeTemplateVariable(match[1]));
  }
  return Array.from(variables);
}

export function templateVariableLabel(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}

export function renderTemplatePreview(
  html: string,
  values: Record<string, string>,
): string {
  if (!html) return '';
  return html.replace(
    TEMPLATE_VARIABLE_PATTERN,
    (_match, variableName: string, fallback: string | undefined) => {
      const key = normalizeTemplateVariable(variableName);
      const compactKey = key.replace(/_/g, '');
      const value = values[key] || values[compactKey];
      if (value) return value;
      if (fallback !== undefined) return fallback;
      return `<span style="background:rgba(66,130,162,0.26);color:#9fd7ee;padding:1px 6px;border:1px solid rgba(111,168,198,0.46);font-size:12px;">{{${variableName}}}</span>`;
    },
  );
}
