import { Header, StatCard, QueueSkeleton } from '../components';
import { useAuth } from '../contexts/useAuth';
import { Play, Square, Zap, Clock, CheckCircle, XCircle, AlertTriangle, RefreshCw, Shield, Activity, User, ShieldOff, Moon } from 'lucide-react';
import { useQueuePage } from '../features/queue/useQueuePage';

export function Queue() {
  const { isAdmin } = useAuth();
  const page = useQueuePage();
  const {
    queue: status,
    health,
    userStats,
    imapActive,
    workers: workerStatus,
  } = page.data;
  const { loading, initialLoad, lastUpdated, resetting, resyncing } = page.status;
  const loadData = page.actions.refresh;

  const handleStartWorker = page.actions.startWorker;
  const handleStopWorker = page.actions.stopWorker;
  const handleProcessNow = page.actions.processNow;
  const handleSystemReset = page.actions.resetSystem;
  const handleResetStats = page.actions.resetStats;
  const handleResync = page.actions.resync;

  const getHealthColor = (status: string) => {
    switch (status) {
      case 'healthy': return 'text-emerald-600 bg-emerald-100';
      case 'warning': return 'text-amber-600 bg-amber-100';
      case 'degraded': return 'text-orange-600 bg-orange-100';
      case 'unhealthy': return 'text-red-600 bg-red-100';
      default: return 'text-gray-600 bg-gray-100';
    }
  };

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
      <Header title="Queue Monitor" onRefresh={loadData} lastUpdated={lastUpdated} />
      
      {initialLoad ? <QueueSkeleton /> : (
      <div className={`p-4 sm:p-6 flex-1 transition-opacity duration-200 ${loading && !status ? 'opacity-60' : ''}`}>
        {/* Stats Grid — admins see global totals, regular users see their own */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 sm:gap-6 mb-6">
          <StatCard
            title="Queued"
            value={isAdmin ? (status?.queue.queued.total || 0) : (userStats?.current.queued ?? status?.queue.queued.total ?? 0)}
            icon={<Clock className="w-5 h-5 text-indigo-600" />}
            iconBg="bg-indigo-100"
          />
          <StatCard
            title="Processing"
            value={status?.queue.processing || 0}
            icon={<Zap className="w-5 h-5 text-amber-600" />}
            iconBg="bg-amber-100"
          />
          <StatCard
            title="Campaign Today"
            value={isAdmin
              ? (status?.queue.today?.campaign_sent ?? 0)
              : (userStats?.today?.campaign_sent ?? 0)}
            icon={<CheckCircle className="w-5 h-5 text-emerald-600" />}
            iconBg="bg-emerald-100"
          />
          <StatCard
            title="Warmup Today"
            value={isAdmin
              ? (status?.queue.today?.warmup_sent ?? 0)
              : (userStats?.today?.warmup_sent ?? 0)}
            icon={<Activity className="w-5 h-5 text-blue-600" />}
            iconBg="bg-blue-100"
          />
          <StatCard
            title="Failed Today"
            value={isAdmin
              ? ((status?.queue.today?.failed ?? status?.queue.totals.failed) || 0)
              : (userStats?.today?.failed ?? 0)}
            icon={<XCircle className="w-5 h-5 text-red-500" />}
            iconBg="bg-red-100"
          />
        </div>

        {/* Controls */}
        {isAdmin && (
          <div className="flex flex-wrap gap-3 mb-6">
            <button
              onClick={handleStartWorker}
              disabled={status?.worker.running}
              className="bg-emerald-600 text-white px-3 py-1.5 text-sm rounded-lg hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 transition-colors"
            >
              <Play size={16} /> Start Worker
            </button>
            <button
              onClick={handleStopWorker}
              disabled={!status?.worker.running}
              className="bg-red-600 text-white px-3 py-1.5 text-sm rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 transition-colors"
            >
              <Square size={16} /> Stop Worker
            </button>
            <button
              onClick={handleProcessNow}
              className="bg-indigo-600 text-white px-3 py-1.5 text-sm rounded-lg hover:bg-indigo-700 flex items-center gap-1.5 transition-colors"
            >
              <Zap size={16} /> Process Now
            </button>

            {/* System Controls */}
            <div className="border-l border-gray-200 pl-3 ml-1 flex gap-3">
              <button
                onClick={handleResync}
                disabled={resyncing}
                className="border border-gray-300 text-gray-700 px-3 py-1.5 text-sm rounded-lg hover:bg-gray-50 disabled:opacity-50 flex items-center gap-1.5 transition-colors"
                title="Resync database with queue"
              >
                <Activity size={16} className={resyncing ? 'animate-spin' : ''} /> 
                {resyncing ? 'Resyncing...' : 'Resync'}
              </button>
              <button
                onClick={handleSystemReset}
                disabled={resetting}
                className="border border-red-200 text-red-600 px-3 py-1.5 text-sm rounded-lg hover:bg-red-50 disabled:opacity-50 flex items-center gap-1.5 transition-colors"
                title="Emergency system reset"
              >
                <RefreshCw size={16} className={resetting ? 'animate-spin' : ''} /> 
                {resetting ? 'Resetting...' : 'Reset System'}
              </button>
            </div>
          </div>
        )}

        {/* System Health */}
        {health && (
          <div className="bg-white rounded-xl shadow p-6 mb-6">
            <h3 className="font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <Shield size={20} />
              System Health
              <span className={`px-2 py-1 rounded-full text-xs font-bold ${getHealthColor(health.status)}`}>
                {health.status.toUpperCase()}
              </span>
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className={`p-3 rounded-lg ${getHealthColor(health.components.database.status)}`}>
                <p className="text-sm font-medium">Database</p>
                <p className="text-lg font-bold">{health.components.database.status}</p>
              </div>
              <div className={`p-3 rounded-lg ${getHealthColor(health.components.redis.status)}`}>
                <p className="text-sm font-medium">Redis</p>
                <p className="text-lg font-bold">{health.components.redis.status}</p>
              </div>
              <div className={`p-3 rounded-lg ${getHealthColor(health.components.campaigns.status)}`}>
                <p className="text-sm font-medium">Campaigns</p>
                <p className="text-lg font-bold">{health.components.campaigns.running_campaigns} running</p>
                {health.components.campaigns.stuck_recipients > 0 && (
                  <p className="text-xs text-orange-600">{health.components.campaigns.stuck_recipients} stuck</p>
                )}
              </div>
              <div className={`p-3 rounded-lg ${imapActive === false ? 'bg-gray-100' : getHealthColor(health.components.auto_replies?.status || 'healthy')}`}>
                <p className="text-sm font-medium flex items-center gap-1.5">Auto-Replies{imapActive === false && <ShieldOff className="h-3.5 w-3.5 text-gray-400" />}</p>
                {imapActive === false ? (
                  <p className="text-lg font-bold text-gray-400">Disabled</p>
                ) : (
                  <>
                    <p className="text-lg font-bold">{health.components.auto_replies?.pending || 0} pending</p>
                    {(health.components.auto_replies?.stuck || 0) > 0 && (
                      <p className="text-xs text-orange-600">{health.components.auto_replies?.stuck} stuck</p>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Worker Status — Dual Timezone */}
        <div className="bg-white rounded-xl shadow p-6 mb-6">
          <h3 className="font-semibold text-gray-800 mb-4 flex items-center gap-2">
            Worker Status
            <span className="text-xs font-normal text-gray-400">({workerStatus.filter(w => w.alive).length}/{workerStatus.length} online)</span>
          </h3>
          {workerStatus.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {workerStatus.map(w => {
                const baseName = w.timezone === 'US/Eastern' ? 'East' : w.timezone === 'US/Pacific' ? 'Pacific' : w.timezone;
                const tzShort = w.timezone === 'US/Eastern' ? 'ET' : w.timezone === 'US/Pacific' ? 'PT' : w.timezone;
                const instanceNum = w.worker_name?.match(/(\d+)$/)?.[1] ?? '1';
                const isOnline = w.alive && w.status !== 'offline';
                const isSending = w.status === 'sending';
                const isCooldown = w.status === 'cooldown';
                const localTime12h = formatWorkerTime12h(w.timezone);
                return (
                  <div key={w.worker_name || w.timezone} className={`rounded-lg border p-4 ${isSending ? 'bg-emerald-50 border-emerald-200' : isCooldown ? 'bg-amber-50 border-amber-200' : !isOnline ? 'bg-red-50 border-red-200' : 'bg-gray-50 border-gray-200'}`}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-semibold text-sm text-gray-800 flex items-center gap-1.5">
                        <Clock size={14} className={isSending ? 'text-emerald-600' : isCooldown ? 'text-amber-600' : 'text-gray-400'} />
                        {baseName} -{instanceNum}
                      </span>
                      <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full ${isSending ? 'bg-emerald-100 text-emerald-700' : isCooldown ? 'bg-amber-100 text-amber-700' : !isOnline ? 'bg-red-100 text-red-600' : 'bg-gray-100 text-gray-600'}`}>
                        {isSending ? <><Zap size={10} /> Sending</> : isCooldown ? <><Moon size={10} /> Cooldown</> : !isOnline ? 'Offline' : w.status}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500 space-y-0.5">
                      {localTime12h && <p>Local: <span className="font-mono text-gray-700">{localTime12h} {tzShort}</span></p>}
                      {isCooldown && w.next_window_open && <p>Next window: <span className="font-mono text-amber-700">{formatDateTime12h(w.next_window_open)}</span></p>}
                      <p>Hours: 9 AM – 5 PM Mon–Fri</p>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-sm text-gray-500">Status</p>
                <p className="font-semibold">{status?.worker.running ? 'Running' : 'Stopped'}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Workers</p>
                <p className="font-semibold">{status?.worker.num_workers || 0}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Processed</p>
                <p className="font-semibold">{status?.worker.processed || 0}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Started At</p>
                <p className="font-semibold">{status?.worker.started_at ? new Date(status.worker.started_at).toLocaleTimeString() : '-'}</p>
              </div>
            </div>
          )}
        </div>

        {/* Queue Details */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="bg-white rounded-xl shadow p-6">
            <h3 className="font-semibold text-gray-800 mb-4">Queue by Priority</h3>
            <div className="space-y-3">
              <div className="flex justify-between items-center p-3 bg-red-50 rounded-lg">
                <span className="font-medium text-red-800">High Priority</span>
                <span className="text-lg font-bold text-red-600">{status?.queue.queued.high || 0}</span>
              </div>
              <div className="flex justify-between items-center p-3 bg-yellow-50 rounded-lg">
                <span className="font-medium text-yellow-800">Normal Priority</span>
                <span className="text-lg font-bold text-yellow-600">{status?.queue.queued.normal || 0}</span>
              </div>
              <div className="flex justify-between items-center p-3 bg-gray-50 rounded-lg">
                <span className="font-medium text-gray-800">Low Priority</span>
                <span className="text-lg font-bold text-gray-600">{status?.queue.queued.low || 0}</span>
              </div>
            </div>
          </div>

          <div className="bg-white rounded-xl shadow p-6">
            <h3 className="font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <User size={18} />
              Your Statistics
            </h3>
            <p className="text-xs text-gray-500 mb-3">Your personal email activity today</p>
            <div className="space-y-3">
              <div className="flex justify-between items-center p-3 bg-emerald-50 rounded-lg">
                <span className="font-medium text-emerald-800">Campaign Today</span>
                <span className="text-lg font-bold text-emerald-600">{userStats?.today?.campaign_sent ?? 0}</span>
              </div>
              <div className="flex justify-between items-center p-3 bg-blue-50 rounded-lg">
                <span className="font-medium text-blue-800">Warmup Today</span>
                <span className="text-lg font-bold text-blue-600">{userStats?.today?.warmup_sent ?? 0}</span>
              </div>
              <div className="flex justify-between items-center p-3 bg-indigo-50 rounded-lg">
                <span className="font-medium text-indigo-800">In Queue</span>
                <span className="text-lg font-bold text-indigo-600">{userStats?.current.queued || 0}</span>
              </div>
              <div className="flex justify-between items-center p-3 bg-red-50 rounded-lg">
                <div className="flex items-center gap-2">
                  <AlertTriangle size={18} className="text-red-600" />
                  <span className="font-medium text-red-800">Failed Today</span>
                </div>
                <span className="text-lg font-bold text-red-600">{userStats?.today.failed ?? 0}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Admin: Global Stats */}
        {isAdmin && (
          <div className="mt-6 bg-white rounded-xl shadow p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="font-semibold text-gray-800 flex items-center gap-2">
                  <Shield size={18} />
                  Global Statistics
                </h3>
                <p className="text-xs text-gray-500 mt-1">All users combined (admin view)</p>
              </div>
              <button
                onClick={handleResetStats}
                className="border border-red-200 text-red-500 px-3 py-1.5 text-xs rounded-lg hover:bg-red-50 flex items-center gap-1.5 transition-colors"
                title="Reset all cumulative stats to zero"
              >
                <RefreshCw size={14} /> Reset Stats
              </button>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <div className="p-3 bg-green-50 rounded-lg">
                <p className="text-sm text-green-800">Sent Today</p>
                <p className="text-xl font-bold text-green-600">{status?.queue.today?.sent ?? status?.queue.totals.sent ?? 0}</p>
              </div>
              <div className="p-3 bg-emerald-50 rounded-lg">
                <p className="text-sm text-emerald-800">All-Time Sent</p>
                <p className="text-xl font-bold text-emerald-600">{status?.queue.historical?.total_sent ?? 0}</p>
              </div>
              <div className="p-3 bg-blue-50 rounded-lg">
                <p className="text-sm text-blue-800">In Queue</p>
                <p className="text-xl font-bold text-blue-600">{status?.queue.queued.total || 0}</p>
              </div>
              <div className="p-3 bg-red-50 rounded-lg">
                <p className="text-sm text-red-800">Failed Today</p>
                <p className="text-xl font-bold text-red-600">{status?.queue.today?.failed ?? status?.queue.totals.failed ?? 0}</p>
              </div>
              <div className="p-3 bg-orange-50 rounded-lg">
                <p className="text-sm text-orange-800">Dead Letter</p>
                <p className="text-xl font-bold text-orange-600">{status?.queue.dead_letter || 0}</p>
              </div>
            </div>
          </div>
        )}
      </div>
      )}
    </div>
  );
}
