import { Header, StatCard, StatusBadge, DashboardSkeleton } from '../components';
import { Inbox, Megaphone, Send, Users, Activity, Clock, Zap, Moon } from 'lucide-react';
import { getDisplayWarmupDay } from '../warmupDay';
import { useDashboardPage } from '../features/dashboard/useDashboardPage';



export function Dashboard() {
  const page = useDashboardPage();
  const { inboxes, campaigns, lists, queue, userStats, workers: workerStatus } = page.data;
  const { loading, initialLoad, lastUpdated } = page.status;
  const {
    activeInboxCount,
    warmingInboxCount,
    runningCampaignCount,
    totalRecipients,
  } = page.summary;
  const loadData = page.refresh;

  const formatWorkerTime12h = (timezone?: string): string | null => {
    if (!timezone) return null;
    try {
      return new Intl.DateTimeFormat('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
        timeZone: timezone,
      }).format(new Date());
    } catch {
      return null;
    }
  };

  const formatDateTime12h = (iso?: string): string => {
    if (!iso) return '-';
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return iso;
    return dt.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
  };

  return (
    <div className="flex-1 flex flex-col min-h-screen">
      <Header title="Dashboard" onRefresh={loadData} lastUpdated={lastUpdated} />
      
      {initialLoad ? <DashboardSkeleton /> : (
      <div className={`p-4 sm:p-6 flex-1 transition-opacity duration-200 ${loading ? 'opacity-60' : ''}`}>
        {/* Stats Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-6 mb-8">
          <StatCard
            title="Active Inboxes"
            value={activeInboxCount}
            subtitle={`${warmingInboxCount} warming up`}
            subtitleColor="text-emerald-600"
            icon={<Inbox className="w-5 h-5 text-indigo-600" />}
            iconBg="bg-indigo-100"
          />
          <StatCard
            title="Campaigns"
            value={campaigns.length}
            subtitle={`${runningCampaignCount} running`}
            subtitleColor="text-violet-600"
            icon={<Megaphone className="w-5 h-5 text-violet-600" />}
            iconBg="bg-violet-100"
          />
          <StatCard
            title="Emails Sent Today"
            value={userStats?.today.sent ?? 0}
            subtitle={`${userStats?.current.queued ?? queue?.queue.queued.total ?? 0} in queue`}
            icon={<Send className="w-5 h-5 text-emerald-600" />}
            iconBg="bg-emerald-100"
          />
          <StatCard
            title="Total Recipients"
            value={totalRecipients}
            subtitle={`${lists.length} lists`}
            icon={<Users className="w-5 h-5 text-amber-600" />}
            iconBg="bg-amber-100"
          />
        </div>

        {/* Worker Status */}
        {workerStatus.length > 0 && (
          <div className="mb-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {workerStatus.map(w => {
              const baseName = w.timezone === 'US/Eastern' ? 'East' : w.timezone === 'US/Pacific' ? 'Pacific' : w.timezone;
              const tzShort = w.timezone === 'US/Eastern' ? 'ET' : w.timezone === 'US/Pacific' ? 'PT' : w.timezone;
              const instanceNum = w.worker_name?.match(/(\d+)$/)?.[1] ?? '1';
              const isOnline = w.alive && w.status !== 'offline';
              const isSending = w.status === 'sending';
              const isCooldown = w.status === 'cooldown';
              const localTime12h = formatWorkerTime12h(w.timezone);
              return (
                <div key={w.worker_name || w.timezone} className={`rounded-xl border p-4 ${
                  isSending ? 'bg-emerald-50 border-emerald-200' :
                  isCooldown ? 'bg-amber-50 border-amber-200' :
                  'bg-gray-50 border-gray-200'
                }`}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Clock size={16} className={isSending ? 'text-emerald-600' : isCooldown ? 'text-amber-600' : 'text-gray-400'} />
                      <span className="font-semibold text-sm text-gray-800">{baseName} -{instanceNum}</span>
                    </div>
                    <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full ${
                      isSending ? 'bg-emerald-100 text-emerald-700' :
                      isCooldown ? 'bg-amber-100 text-amber-700' :
                      !isOnline ? 'bg-red-100 text-red-600' :
                      'bg-gray-100 text-gray-600'
                    }`}>
                      {isSending ? <><Zap size={10} /> Sending</> :
                       isCooldown ? <><Moon size={10} /> Cooldown</> :
                       !isOnline ? 'Offline' : w.status}
                    </span>
                  </div>
                  <div className="text-xs text-gray-500 space-y-0.5">
                    {localTime12h && <p>Local time: <span className="font-mono text-gray-700">{localTime12h} {tzShort}</span></p>}
                    {isCooldown && w.next_window_open && (
                      <p>Next window: <span className="font-mono text-amber-700">{formatDateTime12h(w.next_window_open)}</span></p>
                    )}
                    <p>Business hours: 9 AM – 5 PM Mon–Fri</p>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Recent Data */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Recent Campaigns */}
          <div className="bg-white rounded-xl shadow p-6">
            <h3 className="font-semibold text-gray-800 mb-4">Recent Campaigns</h3>
            {campaigns.length === 0 ? (
              <p className="text-gray-500 text-sm">No campaigns yet</p>
            ) : (
              <div className="space-y-3">
                {campaigns.slice(0, 5).map(campaign => (
                  <div key={campaign.id} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                    <div>
                      <p className="font-medium text-gray-800">{campaign.name}</p>
                      <p className="text-sm text-gray-500">
                        {campaign.total_sent} / {campaign.total_recipients} sent
                      </p>
                    </div>
                    <StatusBadge status={campaign.status} />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Inbox Sending Modes */}
          <div className="bg-white rounded-xl shadow p-6">
            <div className="flex items-center gap-2 mb-4">
              <Activity className="w-4 h-4 text-indigo-500" />
              <h3 className="font-semibold text-gray-800">Inbox Sending Modes</h3>
            </div>
            {inboxes.length === 0 ? (
              <p className="text-gray-500 text-sm">No inboxes configured</p>
            ) : (
              <>
                {/* Mode summary bar */}
                {(() => {
                  const active = inboxes.filter(i => i.sending_mode === 'active').length;
                  const distracted = inboxes.filter(i => i.sending_mode === 'distracted').length;
                  const offline = inboxes.length - active - distracted;
                  return (
                    <div className="flex gap-3 mb-4 text-xs font-medium">
                      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-100 text-emerald-700">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> {active} Active
                      </span>
                      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-amber-100 text-amber-700">
                        <span className="w-1.5 h-1.5 rounded-full bg-amber-500" /> {distracted} Distracted
                      </span>
                      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-gray-100 text-gray-600">
                        <span className="w-1.5 h-1.5 rounded-full bg-gray-400" /> {offline} Offline
                      </span>
                    </div>
                  );
                })()}
                <div className="space-y-3">
                  {inboxes.slice(0, 5).map(inbox => {
                    const modeCfg: Record<string, { bg: string; text: string; label: string; dot: string }> = {
                      active:     { bg: 'bg-emerald-100', text: 'text-emerald-700', label: 'Active',     dot: 'bg-emerald-500' },
                      distracted: { bg: 'bg-amber-100',   text: 'text-amber-700',   label: 'Distracted', dot: 'bg-amber-500' },
                      offline:    { bg: 'bg-gray-100',    text: 'text-gray-600',    label: 'Offline',    dot: 'bg-gray-400' },
                    };
                    const m = modeCfg[inbox.sending_mode || 'offline'] || modeCfg.offline;
                    const displayWarmupDay = getDisplayWarmupDay(inbox);
                    return (
                      <div key={inbox.id} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                        <div>
                          <p className="font-medium text-gray-800 text-sm">{inbox.email}</p>
                          <p className="text-xs text-gray-500">
                            Day {displayWarmupDay} • {inbox.current_daily_count}/{inbox.daily_cap} today
                          </p>
                        </div>
                        <span className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-medium ${m.bg} ${m.text}`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />
                          {m.label}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      )}
    </div>
  );
}
