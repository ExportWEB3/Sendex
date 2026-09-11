import { mutate } from 'swr';
import type { CacheEntry } from '../typefiles';

// Global in-memory store
const store = new Map<string, CacheEntry>();

// Default TTL: 30 seconds
const DEFAULT_TTL = 30_000;

/**
 * Get cached data if available and not expired.
 * Returns { data, isStale } or null if no cache.
 */
export function getCache<T>(key: string, ttl = DEFAULT_TTL): { data: T; isStale: boolean } | null {
  const entry = store.get(key);
  if (!entry) return null;
  const age = Date.now() - entry.timestamp;
  return { data: entry.data as T, isStale: age > ttl };
}

/**
 * Set data in cache.
 */
export function setCache<T>(key: string, data: T): void {
  store.set(key, { data, timestamp: Date.now() });
}

/**
 * Clear both fallback data and SWR responses so data cannot cross sessions.
 */
export function clearCache(): void {
  store.clear();
  void mutate(() => true, undefined, { revalidate: false });
}

// ── Cache key constants ─────────────────────────────────────────────
export const CACHE_KEYS = {
  SMTP_LIST: 'smtp:list',
  RESEND_CONFIG: 'smtp:resend-config',
  INBOX_LIST: 'inboxes:list',
  CAMPAIGN_LIST: 'campaigns:list',
  LIST_LIST: 'lists:list',
  QUEUE_STATUS: 'queue:status',
  TEMPLATES: 'templates:list',
  REPLIES: 'replies:',
  SETTINGS: 'settings:',
  DASHBOARD: 'dashboard:',
} as const;
