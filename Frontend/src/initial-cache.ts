import { getCache, setCache } from './cache';

/** Return a recent cached value for use as SWR fallback data. */
export function getCachedInitial<T>(key: string, fallback: T): T {
  return getCache<T>(key)?.data ?? fallback;
}

/** Save a successful query response for an immediate future-page fallback. */
export function saveToCache<T>(key: string, data: T): void {
  setCache(key, data);
}

/** Check whether a recent fallback exists for a resource. */
export function hasCachedData(key: string): boolean {
  return getCache(key) !== null;
}