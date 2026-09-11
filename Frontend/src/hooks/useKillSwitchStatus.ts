import { useApiQuery } from './useApiQuery';
import type { KillSwitchStatus } from '../../typefiles';

const DEFAULT_STATUS: KillSwitchStatus = {
  enabled: false,
  message: '',
};
const DEFAULT_MESSAGE = 'SoF {RESEND} disabled, contact your provider';

export function useKillSwitchStatus(): KillSwitchStatus {
  const query = useApiQuery<KillSwitchStatus>({
    cacheKey: ['system', 'kill-switch', 'status'],
    endpoint: 'system/kill-switch/status',
    fallbackData: DEFAULT_STATUS,
    refreshInterval: 30_000,
  });
  const status = query.data ?? DEFAULT_STATUS;

  return {
    enabled: Boolean(status.enabled),
    message: status.message || DEFAULT_MESSAGE,
  };
}
