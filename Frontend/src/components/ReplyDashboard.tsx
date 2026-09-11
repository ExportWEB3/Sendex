import { Header, StatusBadge, RepliesSkeleton } from '../components';
import { useReplyDashboard } from '../features/replies/useReplyDashboard';
import type {
  AiGoal,
  AiTone,
  ReplyDashboardProps,
} from '../../typefiles';
import { 
  MessageSquareReply, Clock, CheckCircle2, Send, AlertCircle,
  RefreshCw, Mail, User, Calendar, ShieldOff, Settings,
  ChevronDown, ChevronUp, Timer, XCircle, Loader2
} from 'lucide-react';

// ── Helpers ──

const formatDate = (d: string | null) => {
  if (!d) return '-';
  return new Date(d).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

const getTimeUntil = (d: string | null) => {
  if (!d) return '';
  const diff = new Date(d).getTime() - Date.now();
  if (diff <= 0) return 'Due now';
  const mins = Math.floor(diff / 60000);
  const hrs = Math.floor(mins / 60);
  return hrs > 0 ? `in ${hrs}h ${mins % 60}m` : `in ${mins}m`;
};

const formatCountdown = (secs: number) => {
  const m = Math.floor(secs / 60); const s = secs % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
};

// ── Component ──

export function ReplyDashboard({ engine, title }: ReplyDashboardProps) {
  const {
    data: { campaigns, stats, repliesByCampaign: campaignReplies },
    status: {
      loading,
      initialLoad,
      lastUpdated,
      checking,
      processing,
      cancellingReplyId,
      imapActive,
    },
    pagination: {
      page,
      totalPages,
      total,
      perPage: PER_PAGE,
      goTo: setPage,
      previous: goToPreviousPage,
      next: goToNextPage,
    },
    replies: {
      loadingIds: loadingReplies,
      expandedIds: expandedCampaigns,
      toggleCampaign,
    },
    ai: {
      enabled: useAI,
      tone: aiTone,
      goal: aiGoal,
      customGoal: customGoalText,
      context: customMessage,
      panelOpen: showAIConfig,
      setEnabled: setUseAI,
      setTone: setAiTone,
      setGoal: setAiGoal,
      setCustomGoal: setCustomGoalText,
      setContext: setCustomMessage,
      togglePanel: toggleAIConfig,
    },
    scheduler: {
      active: schedulerActive,
      lastCheck: lastImapCheck,
      secondsUntilCheck,
      checkingStuckSeconds,
      progress: checkProgress,
    },
    filters: { suppressSent, toggleSuppressSent },
    results: { check: checkResult, process: processResult },
    actions: {
      refresh: loadData,
      check: handleCheck,
      process: handleProcess,
      cancel: handleCancel,
    },
  } = useReplyDashboard(engine);

  // ── Computed ──

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'pending': return <StatusBadge status="pending" text="Pending" />;
      case 'queued': return <StatusBadge status="warming_up" text="Queued" />;
      case 'sent': return <StatusBadge status="active" text="Sent" />;
      case 'failed': return <StatusBadge status="error" text="Failed" />;
      case 'cancelled': return <StatusBadge status="inactive" text="Cancelled" />;
      default: return <StatusBadge status="inactive" text={status} />;
    }
  };

  const queuePending = stats?.auto_replies_pending || 0;

  // ── Render ──

  return (
    <div className={`flex-1 flex flex-col min-h-screen bg-gray-50/50 relative ${imapActive === false ? 'overflow-hidden max-h-screen' : ''}`}>
      <Header title={title} onRefresh={loadData} lastUpdated={lastUpdated} />

      {imapActive === false && (
        <div className="fixed inset-0 z-50 bg-gray-900/60 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="max-w-md w-full bg-white rounded-2xl shadow-xl overflow-hidden transform transition-all">
            <div className="p-8 text-center">
              <div className="mx-auto h-20 w-20 bg-red-50 rounded-full flex items-center justify-center mb-6 ring-8 ring-red-50/50">
                <ShieldOff className="h-10 w-10 text-red-500" />
              </div>
              <h2 className="text-2xl font-bold text-gray-900 mb-2">IMAP Connection Missing</h2>
              <p className="text-red-600 font-medium mb-4">Auto-Replies Offline</p>
              <p className="text-gray-500 mb-8 leading-relaxed">
                Connect your IMAP settings on the SMTP configuration page to unlock intelligent reply monitoring and auto-responses.
              </p>
              <a href="/smtp" className="inline-flex items-center justify-center w-full sm:w-auto gap-2 px-6 py-3 bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 active:bg-indigo-800 transition-all text-sm font-semibold shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2">
                <Settings className="h-4 w-4" /> Configure IMAP Settings
              </a>
            </div>
          </div>
        </div>
      )}

      {initialLoad ? <RepliesSkeleton /> : (
      <div className={`p-3 sm:p-5 flex-1 w-full mx-auto overflow-x-hidden transition-opacity duration-300 ease-in-out ${loading ? 'opacity-50' : 'opacity-100'}`}>
        
        {/* Top Status Bar: Timer & Worker */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 mb-5">
          {schedulerActive ? (
            <div className="flex-1 bg-white border border-indigo-100 rounded-xl p-3 sm:px-4 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-3 relative overflow-hidden group">
              <div className="absolute inset-0 bg-gradient-to-r from-indigo-50/50 to-transparent pointer-events-none" />
              <div className="flex items-center gap-2 relative z-10">
                <div className="h-8 w-8 rounded-full bg-indigo-50 flex items-center justify-center flex-shrink-0 group-hover:scale-110 transition-transform">
                  <Timer className="h-4 w-4 text-indigo-600" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    Auto-Sync Active
                    {checkingStuckSeconds > 60 && <span className="flex h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" />}
                  </h3>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    Last sync: {lastImapCheck ? new Date(lastImapCheck).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Never'}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-3 relative z-10 sm:ml-auto">
                <div className="hidden md:flex flex-col items-end gap-1">
                  <div className="text-[9px] font-medium text-indigo-400 uppercase tracking-wider">Next Sync</div>
                  <div className="w-24 bg-gray-100 rounded-full h-1.5 overflow-hidden">
                    <div className={`h-full rounded-full transition-all duration-1000 ease-linear ${checkProgress > 80 ? 'bg-indigo-400' : 'bg-indigo-600'}`} style={{ width: `${checkProgress}%` }} />
                  </div>
                </div>
                <div className="min-w-[65px] text-right">
                  {secondsUntilCheck !== null && secondsUntilCheck > 0 ? (
                    <span className="text-base font-mono font-bold text-indigo-600 tabular-nums tracking-tight">{formatCountdown(secondsUntilCheck)}</span>
                  ) : checkingStuckSeconds > 60 ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-50 text-[11px] font-medium text-amber-700 border border-amber-200">
                      <Clock className="w-3 h-3" /> ({Math.floor(checkingStuckSeconds / 60)}m)
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-indigo-50 text-[11px] font-medium text-indigo-700 border border-indigo-100">
                      <RefreshCw className="w-3 h-3 animate-spin" /> Syncing
                    </span>
                  )}
                </div>
              </div>
            </div>
          ) : imapActive !== false && (
            <div className="w-full bg-amber-50 border border-amber-200 rounded-xl p-3 flex items-center gap-2 text-amber-800 shadow-sm animate-pulse">
              <AlertCircle className="w-4 h-4 flex-shrink-0 text-amber-600" />
              <div className="text-xs font-medium">Background worker inactive. Auto-replies may be delayed.</div>
            </div>
          )}
        </div>

        {/* Action Controls & AI Toggle */}
        <div className="flex flex-col lg:flex-row justify-between items-start gap-4 mb-5 bg-white p-3 rounded-xl shadow-sm border border-gray-100">
          <div className="flex flex-wrap items-center gap-2 w-full lg:w-auto">
            <button onClick={handleCheck} disabled={checking}
              className="flex-1 sm:flex-none flex items-center justify-center gap-1.5 px-4 py-2 bg-gray-900 text-white rounded-lg hover:bg-gray-800 disabled:opacity-70 disabled:cursor-not-allowed transition-all font-medium text-xs shadow-sm active:scale-95">
              {checking ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Mail className="w-3.5 h-3.5" />}
              <span>Manual Check</span>
            </button>
            <button onClick={handleProcess} disabled={processing || queuePending === 0}
              className="flex-1 sm:flex-none flex items-center justify-center gap-1.5 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all font-medium text-xs shadow-sm active:scale-95">
              {processing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
              <span>Process Queue {queuePending > 0 && <span className="bg-white/20 px-1 py-0.5 rounded text-[10px]">{queuePending}</span>}</span>
            </button>
            <div className="w-full sm:w-auto h-px sm:h-6 sm:w-px bg-gray-200 my-1 sm:my-0 md:mx-1" />
            <button onClick={toggleAIConfig}
              className={`flex-1 sm:flex-none flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg border transition-all font-medium text-xs ${useAI ? 'bg-gradient-to-r from-indigo-50 to-purple-50 border-indigo-200 text-indigo-700 shadow-sm hover:from-indigo-100 hover:to-purple-100' : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'}`}>
              <span className="text-base leading-none">&#10024;</span>
              <span>AI Engine {useAI ? 'Active' : 'Off'}</span>
            </button>
          </div>
          <div className="flex items-center w-full lg:w-auto pl-1 lg:pl-0 mt-1 lg:mt-0">
            <label className="group flex items-center gap-2 cursor-pointer select-none">
              <div className="relative flex items-center">
                <input type="checkbox" checked={suppressSent} onChange={toggleSuppressSent} className="peer sr-only" />
                <div className="w-8 h-4.5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-indigo-500 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3.5 after:w-3.5 after:transition-all peer-checked:bg-indigo-600 transition-colors"></div>
              </div>
              <span className="text-xs font-medium text-gray-600 group-hover:text-gray-900 transition-colors">Hide Sent Replies</span>
            </label>
          </div>
        </div>

        {/* AI Configuration Panel */}
        {showAIConfig && (
          <div className="mb-5 overflow-hidden rounded-xl bg-white border border-indigo-100 shadow-sm animate-in slide-in-from-top-2 fade-in duration-200">
            <div className="p-3 sm:p-4 border-b border-indigo-50 bg-gradient-to-r from-indigo-50/50 to-transparent flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="p-1.5 bg-indigo-100 rounded-lg"><Settings className="w-3.5 h-3.5 text-indigo-600" /></div>
                <h4 className="font-semibold text-gray-900 text-sm">AI Engine Configuration</h4>
              </div>
              <label className="flex items-center gap-2 cursor-pointer">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-indigo-600">Master Switch</span>
                <input type="checkbox" checked={useAI} onChange={e => setUseAI(e.target.checked)} className="w-3.5 h-3.5 text-indigo-600 rounded cursor-pointer" />
              </label>
            </div>
            {useAI && (
              <div className="p-4">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div className="space-y-1">
                    <label className="block text-[10px] font-bold uppercase tracking-wider text-gray-500">Response Tone</label>
                    <div className="relative">
                      <select value={aiTone} onChange={e => setAiTone(e.target.value as AiTone)} className="w-full pl-2 pr-8 py-2 text-xs border border-gray-200 rounded-lg bg-gray-50 hover:bg-white focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-colors appearance-none cursor-pointer text-gray-900 font-medium">
                        <option value="professional">Professional &amp; Formal</option>
                        <option value="friendly">Friendly &amp; Approachable</option>
                        <option value="casual">Casual &amp; Direct</option>
                      </select>
                      <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
                    </div>
                  </div>
                  <div className="space-y-1">
                    <label className="block text-[10px] font-bold uppercase tracking-wider text-gray-500">Primary Goal</label>
                    <div className="relative">
                      <select value={aiGoal} onChange={e => setAiGoal(e.target.value as AiGoal)} className="w-full pl-2 pr-8 py-2 text-xs border border-gray-200 rounded-lg bg-gray-50 hover:bg-white focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-colors appearance-none cursor-pointer text-gray-900 font-medium">
                        <option value="continue_conversation">Continue Conversation</option>
                        <option value="schedule_call">Schedule a Discovery Call</option>
                        <option value="provide_info">Provide Additional Info</option>
                        <option value="custom">Custom Objective...</option>
                      </select>
                      <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
                    </div>
                  </div>
                  <div className="space-y-1">
                    <label className="block text-[10px] font-bold uppercase tracking-wider text-gray-500">Business Context</label>
                    <input type="text" value={customMessage} onChange={e => setCustomMessage(e.target.value)} placeholder="e.g. We are a B2B SaaS company..."
                      className="w-full px-2 py-2 text-xs border border-gray-200 rounded-lg bg-gray-50 hover:bg-white focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-colors placeholder:text-gray-400 text-gray-900 font-medium" />
                  </div>
                </div>
                {aiGoal === 'custom' && (
                  <div className="mt-4 pt-4 border-t border-gray-100 animate-in fade-in duration-300">
                    <label className="block text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">Custom Instruct Prompt</label>
                    <textarea value={customGoalText} onChange={e => setCustomGoalText(e.target.value)} placeholder="Prompt the AI with exact instructions for handling replies..." rows={2}
                      className="w-full p-3 text-xs border border-indigo-200 rounded-lg bg-indigo-50/30 focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-colors resize-none text-gray-900 placeholder:text-indigo-300" />
                    <p className="flex items-center gap-1 text-[10px] text-indigo-600 mt-1 font-medium">
                      <AlertCircle className="w-3 h-3" /> Overrides default AI behavior
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Global Statistics Grid */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4 mb-5">
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 hover:border-indigo-100 transition-colors flex flex-col justify-between group relative overflow-hidden">
              <div className="absolute right-0 top-0 w-20 h-20 bg-gradient-to-br from-indigo-50 to-transparent rounded-bl-full -z-10 opacity-50 group-hover:scale-110 transition-transform duration-500" />
              <div className="flex items-center justify-between mb-2">
                <div className="w-8 h-8 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center"><MessageSquareReply className="w-4 h-4" /></div>
                <span className="text-[10px] font-bold text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">{stats.reply_rate.toFixed(1)}% rate</span>
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900 tracking-tight">{stats.total_replies}</p>
                <p className="text-xs font-medium text-gray-500 mt-0.5">Total Received</p>
              </div>
            </div>
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 hover:border-amber-100 transition-colors flex flex-col justify-between group relative overflow-hidden">
              <div className="absolute right-0 top-0 w-20 h-20 bg-gradient-to-br from-amber-50 to-transparent rounded-bl-full -z-10 opacity-50 group-hover:scale-110 transition-transform duration-500" />
              <div className="flex items-center justify-between mb-2">
                <div className="w-8 h-8 rounded-lg bg-amber-50 text-amber-600 flex items-center justify-center"><Clock className="w-4 h-4" /></div>
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900 tracking-tight">{stats.auto_replies_pending}</p>
                <p className="text-xs font-medium text-gray-500 mt-0.5">Pending Generation</p>
              </div>
            </div>
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 hover:border-emerald-100 transition-colors flex flex-col justify-between group relative overflow-hidden">
              <div className="absolute right-0 top-0 w-20 h-20 bg-gradient-to-br from-emerald-50 to-transparent rounded-bl-full -z-10 opacity-50 group-hover:scale-110 transition-transform duration-500" />
              <div className="flex items-center justify-between mb-2">
                <div className="w-8 h-8 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center"><CheckCircle2 className="w-4 h-4" /></div>
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900 tracking-tight">{stats.auto_replies_sent}</p>
                <p className="text-xs font-medium text-gray-500 mt-0.5">Successfully Sent</p>
              </div>
            </div>
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 hover:border-violet-100 transition-colors flex flex-col justify-between group relative overflow-hidden">
              <div className="absolute right-0 top-0 w-20 h-20 bg-gradient-to-br from-violet-50 to-transparent rounded-bl-full -z-10 opacity-50 group-hover:scale-110 transition-transform duration-500" />
              <div className="flex items-center justify-between mb-2">
                <div className="w-8 h-8 rounded-lg bg-violet-50 text-violet-600 flex items-center justify-center"><Send className="w-4 h-4" /></div>
                <span className="text-[10px] font-bold text-gray-500 bg-gray-50 px-1.5 py-0.5 rounded">{stats.total_campaigns} Active</span>
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900 tracking-tight">{stats.campaigns_with_replies}</p>
                <p className="text-xs font-medium text-gray-500 mt-0.5">Campaigns w/ Replies</p>
              </div>
            </div>
          </div>
        )}

        {/* Dynamic Action Banners */}
        <div className="space-y-3 mb-5">
          {checkResult && (
            <div className="p-3 sm:p-4 bg-indigo-50 border border-indigo-100 rounded-xl shadow-sm animate-in fade-in slide-in-from-top-2">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div className="flex gap-2 items-start">
                  <div className="p-1.5 bg-indigo-100 rounded-full mt-0.5"><Mail className="w-3.5 h-3.5 text-indigo-600" /></div>
                  <div>
                    <h4 className="text-xs font-bold text-indigo-900 mb-0.5">Check Complete</h4>
                    <p className="text-[11px] text-indigo-700 font-medium leading-snug">
                      Scanned {checkResult.campaigns_checked} campaigns across {checkResult.total_inboxes_checked} inboxes.
                      <br className="sm:hidden" /> Found {checkResult.total_replies_found} new replies &amp; scheduled {checkResult.total_auto_replies_scheduled} responses.
                    </p>
                  </div>
                </div>
              </div>
              {checkResult.per_campaign.length > 0 && (
                <div className="mt-3 pt-2 border-t border-indigo-100/50 flex flex-wrap gap-1.5">
                  {checkResult.per_campaign.map(c => (
                    <span key={c.campaign_id} className="inline-flex items-center px-2 py-0.5 rounded bg-white/60 text-[10px] font-medium text-indigo-800 border border-indigo-100">
                      {c.campaign_name}: <span className="ml-1 font-bold">{c.replies_found}</span>
                    </span>
                  ))}
                </div>
              )}
              {checkResult.errors.length > 0 && (
                <div className="mt-2 text-[10px] font-medium text-red-600 bg-red-50 p-2 rounded-lg border border-red-100 flex items-start gap-1.5">
                  <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
                  <span>{checkResult.errors.join(' \u2022 ')}</span>
                </div>
              )}
            </div>
          )}

          {processResult && (
            <div className="p-4 sm:p-5 bg-emerald-50 border border-emerald-100 rounded-2xl shadow-sm animate-in fade-in slide-in-from-top-2">
              <div className="flex gap-3 items-start">
                <div className="p-2 bg-emerald-100 rounded-full mt-0.5"><Send className="w-4 h-4 text-emerald-600" /></div>
                <div>
                  <h4 className="text-sm font-bold text-emerald-900 mb-0.5">Processing Complete</h4>
                  <p className="text-sm text-emerald-700 font-medium">Successfully processed {processResult.processed} replies. {processResult.queued} added to send queue.</p>
                </div>
              </div>
              {processResult.errors.length > 0 && (
                <div className="mt-3 text-xs font-medium text-red-600 bg-red-50 p-2.5 rounded-lg border border-red-100 flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" />
                  <span>{processResult.errors.join(' \u2022 ')}</span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Campaign Reply Listing */}
        <div className="space-y-3">
          <h3 className="text-base font-bold text-gray-900 px-1">Campaign Responses</h3>
          
          {campaigns.length === 0 ? (
            <div className="text-center py-12 px-4 bg-white rounded-2xl border border-gray-100 shadow-sm">
              <div className="mx-auto w-16 h-16 bg-gray-50 rounded-full flex items-center justify-center mb-3">
                <MessageSquareReply className="w-8 h-8 text-gray-300" />
              </div>
              <h3 className="text-base font-semibold text-gray-900 mb-1">No {engine.toUpperCase()} Campaigns</h3>
              <p className="text-gray-500 text-xs max-w-sm mx-auto">
                Campaigns sent via {engine === 'resend' ? 'Resend' : 'direct SMTP'} will appear here once they start sending.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {campaigns.map(campaign => {
                const replies = campaignReplies[campaign.id] || [];
                const isExpanded = expandedCampaigns.has(campaign.id);
                const isLoadingReplies = loadingReplies.has(campaign.id);
                const hasIncoming = campaign.total_replies > 0;

                return (
                  <div key={campaign.id} className={`bg-white rounded-xl shadow-sm border transition-all duration-200 overflow-hidden ${isExpanded ? 'border-indigo-200 shadow-sm' : 'border-gray-100 hover:border-gray-200'}`}>
                    {/* Campaign Accordion Header */}
                    <button onClick={() => toggleCampaign(campaign.id)}
                      className="w-full flex items-center gap-3 p-3 sm:p-4 text-left focus:outline-none focus:bg-gray-50 group hover:bg-gray-50 transition-colors">
                      <div className={`relative flex-shrink-0 w-10 h-10 sm:w-12 sm:h-12 rounded-xl flex flex-col items-center justify-center transition-colors ${hasIncoming ? 'bg-indigo-50 group-hover:bg-indigo-100' : 'bg-gray-50 group-hover:bg-gray-100'}`}>
                        <span className={`text-base sm:text-lg font-bold leading-none mb-0.5 ${hasIncoming ? 'text-indigo-600' : 'text-gray-400'}`}>{campaign.total_replies}</span>
                        <span className={`text-[8px] font-bold uppercase tracking-wider ${hasIncoming ? 'text-indigo-400' : 'text-gray-400'}`}>Replies</span>
                        {campaign.auto_replies_pending > 0 && (
                          <span className="absolute -top-1 -right-1 w-3 h-3 bg-amber-500 rounded-full border-2 border-white flex items-center justify-center">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                          </span>
                        )}
                      </div>
                      <div className="flex-1 min-w-0 pr-2">
                        <h3 className="text-sm sm:text-base font-bold text-gray-900 truncate mb-0.5 pr-4">
                          {campaign.name}
                          <span className={`ml-1.5 inline-flex items-center px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider rounded-full align-middle ${
                            campaign.engine_type === 'mixed' ? 'bg-purple-100 text-purple-700' :
                            campaign.engine_type === 'smtp' ? 'bg-emerald-100 text-emerald-700' :
                            'bg-blue-100 text-blue-700'
                          }`}>
                            {campaign.engine_type === 'mixed' ? 'Mixed' : campaign.engine_type === 'smtp' ? 'SMTP' : 'Resend'}
                          </span>
                        </h3>
                        <div className="flex flex-wrap items-center gap-2 sm:gap-3 text-[10px] sm:text-[11px]">
                          <span className="flex items-center gap-1 text-gray-500 font-medium bg-gray-100 px-1.5 py-0.5 rounded">
                            <Send className="w-2.5 h-2.5" /> {campaign.total_sent} Sent
                          </span>
                          {campaign.reply_rate > 0 && (
                            <span className="flex items-center gap-1 text-indigo-600 font-medium bg-indigo-50 px-1.5 py-0.5 rounded">
                              <Timer className="w-2.5 h-2.5" /> {campaign.reply_rate.toFixed(1)}% Rate
                            </span>
                          )}
                          {campaign.auto_replies_pending > 0 && (
                            <span className="flex items-center gap-1 text-amber-700 font-bold bg-amber-50 px-1.5 py-0.5 rounded">
                              <Clock className="w-2.5 h-2.5" /> {campaign.auto_replies_pending} Pending
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-1.5 flex-shrink-0 ml-auto">
                        {campaign.auto_replies_total > 0 && (
                          <span className="hidden sm:inline-flex items-center justify-center px-1.5 py-0.5 bg-gray-900 text-white text-[9px] font-bold uppercase tracking-widest rounded-full">
                            {campaign.auto_replies_total} Total
                          </span>
                        )}
                        <div className={`p-1 rounded-md transition-colors ${isExpanded ? 'bg-indigo-50 text-indigo-600' : 'text-gray-400 group-hover:bg-gray-200'}`}>
                          {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                        </div>
                      </div>
                    </button>

                    {/* Campaign Threads/Replies list */}
                    {isExpanded && (
                      <div className="border-t border-gray-100 bg-gray-50/50 p-2 sm:p-4">
                        {isLoadingReplies ? (
                          <div className="py-8 text-center">
                            <Loader2 className="w-5 h-5 text-indigo-500 animate-spin mx-auto mb-2" />
                            <p className="text-xs text-gray-500 font-medium">Loading replies...</p>
                          </div>
                        ) : replies.length === 0 ? (
                          <div className="py-6 text-center bg-white rounded-lg border border-gray-100 border-dashed">
                            <Mail className="w-6 h-6 text-gray-300 mx-auto mb-1.5" />
                            <p className="text-xs text-gray-500 font-medium">Nothing to show right now.</p>
                            {suppressSent && <p className="text-[10px] text-gray-400 mt-1">Try disabling "Hide Sent Replies" above.</p>}
                          </div>
                        ) : (
                          <div className="space-y-3">
                            {replies.map(reply => (
                              <div key={reply.id} className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden hover:border-indigo-200 transition-colors">
                                {/* Thread Header */}
                                <div className="px-3 py-2 border-b border-gray-100 flex flex-wrap sm:flex-nowrap items-center justify-between gap-2 bg-gray-50/50">
                                  <div className="flex items-center gap-2 min-w-0">
                                    <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-100 to-purple-100 flex items-center justify-center flex-shrink-0 shadow-inner">
                                      <User className="w-3.5 h-3.5 text-indigo-600" />
                                    </div>
                                    <div className="min-w-0">
                                      <h4 className="text-xs font-bold text-gray-900 truncate pr-2">{reply.to_email}</h4>
                                      {reply.from_email && <p className="text-[10px] font-medium text-gray-500 truncate mt-0.5">via {reply.from_email}</p>}
                                    </div>
                                  </div>
                                  <div className="flex items-center gap-2 flex-shrink-0 w-full sm:w-auto justify-between sm:justify-end">
                                    {reply.status === 'pending' && reply.scheduled_at && (
                                      <span className="flex items-center gap-1 px-1.5 py-0.5 bg-amber-50 text-amber-700 text-[10px] font-bold rounded border border-amber-100">
                                        <Timer className="w-2.5 h-2.5" /> {getTimeUntil(reply.scheduled_at)}
                                      </span>
                                    )}
                                    <div className="flex items-center gap-1.5">
                                      <div className="scale-90 transform origin-right">{getStatusBadge(reply.status)}</div>
                                      {reply.status === 'pending' && (
                                        <button onClick={() => handleCancel(reply.id)} disabled={cancellingReplyId === reply.id}
                                          className="p-1 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors disabled:opacity-50" title="Cancel Reply">
                                          {cancellingReplyId === reply.id ? <Loader2 className="w-3.5 h-3.5 animate-spin text-red-500" /> : <XCircle className="w-3.5 h-3.5" />}
                                        </button>
                                      )}
                                    </div>
                                  </div>
                                </div>

                                {/* Thread Content Grid */}
                                <div className="p-3 grid grid-cols-1 lg:grid-cols-2 gap-3">
                                  {/* Incoming */}
                                  <div className="flex gap-2.5 relative">
                                    <div className="absolute left-[9px] top-6 bottom-0 w-px bg-gray-200 lg:hidden" />
                                    <div className="w-5 h-5 rounded-full bg-gray-100 flex items-center justify-center flex-shrink-0 ring-2 ring-white relative z-10 mt-0.5">
                                      <Mail className="w-2.5 h-2.5 text-gray-500" />
                                    </div>
                                    <div className="flex-1 bg-gray-50 rounded-lg rounded-tl-sm p-3 border border-gray-100">
                                      <div className="text-[9px] font-bold text-gray-500 uppercase tracking-widest mb-1">Their Message</div>
                                      <p className="text-xs font-bold text-gray-900 mb-1 leading-snug">{reply.original_reply_subject || 'Re: ' + reply.subject}</p>
                                      <p className="text-xs text-gray-600 leading-relaxed whitespace-pre-wrap font-medium">{reply.original_reply_snippet || <span className="text-gray-400 italic">No text content available</span>}</p>
                                    </div>
                                  </div>
                                  {/* Outgoing */}
                                  <div className="flex gap-2.5 lg:flex-row-reverse relative">
                                    <div className="w-5 h-5 rounded-full bg-indigo-100 flex items-center justify-center flex-shrink-0 ring-2 ring-white relative z-10 mt-0.5 lg:order-2">
                                      <Send className="w-2.5 h-2.5 text-indigo-600" />
                                    </div>
                                    <div className="flex-1 bg-indigo-50/50 rounded-lg rounded-tr-sm lg:rounded-tr-lg lg:rounded-tl-sm p-3 border border-indigo-100 lg:order-1">
                                      <div className="flex justify-between items-start mb-1">
                                        <div className="text-[9px] font-bold text-indigo-500 uppercase tracking-widest">Our Response</div>
                                        {reply.sent_at && <span className="text-[9px] font-bold text-emerald-600 flex items-center gap-0.5"><CheckCircle2 className="w-2.5 h-2.5" /> Sent</span>}
                                      </div>
                                      <p className="text-xs font-bold text-indigo-950 mb-1 leading-snug">{reply.subject}</p>
                                      <p className={`text-xs leading-relaxed whitespace-pre-wrap font-medium ${reply.body_text ? 'text-indigo-900/80' : 'text-indigo-400 italic'}`}>
                                        {reply.body_text || 'Drafting AI response...'}
                                      </p>
                                    </div>
                                  </div>
                                </div>

                                {/* Thread Footer */}
                                <div className="px-3 py-2 bg-gray-50/30 border-t border-gray-50 flex items-center gap-3 text-[10px] font-medium text-gray-400">
                                  {reply.scheduled_at && <span className="flex items-center gap-1"><Calendar className="w-3 h-3" /> Scheduled: {formatDate(reply.scheduled_at)}</span>}
                                  {reply.sent_at && <span className="flex items-center gap-1 text-gray-500"><CheckCircle2 className="w-3 h-3 text-emerald-500" /> Delivered: {formatDate(reply.sent_at)}</span>}
                                </div>

                                {/* Error Banner */}
                                {reply.error_message && (
                                  <div className="px-3 py-2 bg-red-50 border-t border-red-100 flex items-start gap-2">
                                    <AlertCircle className="w-3.5 h-3.5 text-red-500 flex-shrink-0 mt-0.5" />
                                    <div>
                                      <h5 className="text-[11px] font-bold text-red-800">Failed to send</h5>
                                      <p className="text-[10px] font-medium text-red-600 mt-0.5">{reply.error_message}</p>
                                    </div>
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              
              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex flex-col sm:flex-row items-center justify-between border-t border-gray-200 border-dashed pt-4 pb-2 gap-3">
                  <p className="text-xs font-medium text-gray-500 order-2 sm:order-1">
                    Showing <span className="font-bold text-gray-900">{(page - 1) * PER_PAGE + 1}</span> to <span className="font-bold text-gray-900">{Math.min(page * PER_PAGE, total)}</span> of <span className="font-bold text-gray-900">{total}</span>
                  </p>
                  <div className="flex items-center gap-1.5 bg-white rounded-lg p-1 shadow-sm border border-gray-100 order-1 sm:order-2">
                    <button onClick={goToPreviousPage} disabled={page === 1}
                      className="px-3 py-1.5 text-xs font-bold rounded hover:bg-gray-50 text-gray-600 disabled:opacity-40 disabled:hover:bg-transparent transition-colors">
                      Prev
                    </button>
                    <div className="h-5 w-px bg-gray-200" />
                    <div className="flex px-0.5 gap-0.5">
                      {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                        let pageNum: number;
                        if (totalPages <= 7) {
                          pageNum = i + 1;
                        } else if (page <= 4) {
                          pageNum = i + 1;
                        } else if (page >= totalPages - 3) {
                          pageNum = totalPages - 6 + i;
                        } else {
                          pageNum = page - 3 + i;
                        }
                        return (
                          <button key={pageNum} onClick={() => setPage(pageNum)}
                            className={`w-7 h-7 text-xs font-bold rounded transition-all ${pageNum === page ? 'bg-indigo-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-100'}`}>
                            {pageNum}
                          </button>
                        );
                      })}
                    </div>
                    <div className="h-5 w-px bg-gray-200" />
                    <button onClick={goToNextPage} disabled={page === totalPages}
                      className="px-4 py-2 text-sm font-bold rounded-lg hover:bg-gray-50 text-gray-600 disabled:opacity-40 disabled:hover:bg-transparent transition-colors">
                      Next
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      )}
    </div>
  );
}
