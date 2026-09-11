import type { WarmupDaySource } from '../typefiles';

const DAY_MS = 24 * 60 * 60 * 1000;

function utcDateStartMs(value: Date): number {
  return Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate());
}

export function getDisplayWarmupDay(source: WarmupDaySource): number {
  const storedDay = Math.max(0, Number(source.warmup_day ?? 0));

  // Keep backend values for non-warming states.
  if (source.state !== 'warming_up' || !source.created_at) {
    return storedDay;
  }

  const created = new Date(source.created_at);
  if (Number.isNaN(created.getTime())) {
    return storedDay;
  }

  const now = new Date();
  const daysElapsed = Math.max(0, Math.floor((utcDateStartMs(now) - utcDateStartMs(created)) / DAY_MS));
  const realDay = daysElapsed + 1;

  // Never show less than backend warmup_day.
  return Math.max(storedDay, realDay);
}
