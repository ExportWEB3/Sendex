import { useMemo } from 'react';
import { Header, Modal, StatusBadge, CampaignsSkeleton, CampaignForm, formatFileSize, getFileIcon, isImageType, SelectionBar } from '../components';
import { Plus, Play, Pause, XCircle, BarChart3, Users, ChevronLeft, ChevronRight, Search, CheckCircle2, Clock, AlertTriangle, Mail, MousePointerClick, Reply, X, Paperclip, Trash2, Pencil, Terminal, ListChecks, Eye } from 'lucide-react';
import { useCampaignsPage } from '../features/campaigns/useCampaignsPage';
import { sanitizeEmailHtml } from '../utils/sanitize-html';
import type {
  Campaign,
  Inbox,
  SMTPAccount,
  StatusFilter,
} from '../../typefiles';



export function Campaigns() {
  const page = useCampaignsPage();
  const { campaigns, lists, inboxes, smtpAccounts, templates } = page.data;
  const { loading, initialLoad, lastUpdated, startingAll, pausingAll } = page.status;
  const {
    isOpen: showModal,
    editingCampaign,
    uploadedAttachments,
    uploading,
    multiTemplateIds,
  } = page.editor;
  const {
    campaign: detailCampaign,
    total: recipientTotal,
    page: recipientPage,
    loading: recipientLoading,
    statusFilter,
    statusCounts,
    emailSearch,
    visibleRecipients: filteredRecipients,
    totalPages,
  } = page.recipients;
  const {
    enabled: selectMode,
    selectedIds,
    isDeleting: bulkDeleting,
  } = page.selection;
  const { terminals: openTerminals, tick: tickCounter } = page.activity;
  const { selected: statsModal } = page.stats;
  const {
    selected: recipientPreview,
    loading: previewLoading,
    open: handleViewRecipientPreview,
    close: closeRecipientPreview,
  } = page.preview;
  const sanitizedStatsBody = useMemo(
    () => sanitizeEmailHtml(statsModal?.body_html || statsModal?.body_text || '<em>No content</em>'),
    [statsModal?.body_html, statsModal?.body_text],
  );
  const sanitizedRecipientPreviewBody = useMemo(
    () => sanitizeEmailHtml(
      recipientPreview?.sent_body_html
        || recipientPreview?.sent_body_text
        || '<em>No content was recorded.</em>',
    ),
    [recipientPreview?.sent_body_html, recipientPreview?.sent_body_text],
  );

  const loadData = page.actions.refresh;
  const handleStart = page.actions.start;

  const activeRunningCount = campaigns.filter(c => c.status === 'running').length;

  const handlePauseAll = page.actions.pauseAll;
  const handleStartAll = page.actions.startAll;
  const handlePause = page.actions.pause;
  const handleResume = page.actions.resume;
  const handleCancel = page.actions.cancel;
  const handleDelete = page.actions.remove;
  const toggleSelectMode = page.selection.toggleMode;
  const toggleSelected = page.selection.toggleOne;
  const handleSelectAll = page.selection.toggleAll;
  const handleBulkDelete = page.actions.removeSelected;
  const handleEditCampaign = page.editor.openEdit;
  const handleMultiTemplateToggle = page.editor.toggleTemplate;

  const handleViewStats = page.actions.viewStats;
  const handleViewRecipients = page.recipients.open;
  const handleFilterChange = page.recipients.changeFilter;
  const handlePageChange = page.recipients.changePage;

  const handleCreate = page.actions.submit;
  const toggleTerminal = page.activity.toggle;

  const getProgress = (c: Campaign) => c.total_recipients > 0 ? Math.round((c.total_sent / c.total_recipients) * 100) : 0;

  const getDisplayStatus = (campaign: Campaign) => campaign.effective_status || campaign.status;

  const formatNextWindow = (iso?: string | null) => {
    if (!iso) return null;
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return iso;
    return parsed.toLocaleString();
  };

  const handleFileSelect = page.editor.selectFiles;
  const removeAttachment = page.editor.removeAttachment;

  // Resolve inbox/SMTP names from IDs - handles deleted inboxes
  const getInboxInfo = (campaign: Campaign) => {
    const ids = campaign.inbox_ids || [];
    return ids.map(id => {
      const existing = inboxes.find(i => i.id === id);
      if (existing) return { type: 'live' as const, inbox: existing, email: existing.email, id };
      // Inbox was deleted - use inbox_emails from API
      const email = campaign.inbox_emails?.[String(id)];
      return { type: 'deleted' as const, inbox: null, email: email || `Inbox #${id}`, id };
    });
  };
  const getSmtpForInbox = (inbox: Inbox) => smtpAccounts.find(s => s.id === inbox.smtp_account_id);
  const isApiProvider = (providerType?: string | null) => (
    providerType === 'brevo' || providerType === 'ses_api' || providerType === 'ses_smtp'
  );
  const getDeliveryLabel = (smtp: SMTPAccount | null | undefined) => {
    if (!smtp) return null;
    if (isApiProvider(smtp.provider_type)) return 'Resend';
    return `${smtp.host}:${smtp.port}`;
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'sent': return 'text-emerald-700 bg-emerald-50';
      case 'delivered': return 'text-emerald-700 bg-emerald-50';
      case 'opened': return 'text-blue-700 bg-blue-50';
      case 'clicked': return 'text-indigo-700 bg-indigo-50';
      case 'replied': return 'text-purple-700 bg-purple-50';
      case 'pending': return 'text-amber-700 bg-amber-50';
      case 'failed': return 'text-red-700 bg-red-50';
      case 'bounced': return 'text-red-700 bg-red-50';
      case 'unsubscribed': return 'text-gray-700 bg-gray-100';
      default: return 'text-gray-700 bg-gray-50';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'sent': case 'delivered': return <CheckCircle2 className="w-3.5 h-3.5" />;
      case 'opened': return <Mail className="w-3.5 h-3.5" />;
      case 'clicked': return <MousePointerClick className="w-3.5 h-3.5" />;
      case 'replied': return <Reply className="w-3.5 h-3.5" />;
      case 'pending': return <Clock className="w-3.5 h-3.5" />;
      case 'failed': case 'bounced': return <AlertTriangle className="w-3.5 h-3.5" />;
      default: return <Clock className="w-3.5 h-3.5" />;
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div className="flex-1 flex flex-col min-h-screen">
      <Header title="Campaigns" onRefresh={loadData} lastUpdated={lastUpdated} />
      
      {initialLoad ? <CampaignsSkeleton /> : (
      <div className={`p-3 sm:p-4 md:p-6 flex-1 transition-opacity duration-200 ${loading ? 'opacity-60' : ''}`}>
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 mb-4 sm:mb-6">
          <p className="text-sm text-gray-600">Create and manage email campaigns</p>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <button
              onClick={handleStartAll}
              disabled={startingAll || !campaigns.some(c => ['draft', 'scheduled'].includes(c.status))}
              className="bg-emerald-600 text-white px-3 py-1.5 text-sm rounded-lg hover:bg-emerald-700 transition-colors flex items-center gap-2 justify-center disabled:opacity-50 disabled:cursor-not-allowed"
              title={campaigns.some(c => ['draft', 'scheduled'].includes(c.status))
                ? 'Start every draft/scheduled campaign. First batches are staggered automatically.'
                : 'No draft or scheduled campaigns to start'}
            >
              <Play size={16} /> Start All ({campaigns.filter(c => ['draft', 'scheduled'].includes(c.status)).length})
            </button>
            <button
              onClick={handlePauseAll}
              disabled={pausingAll || activeRunningCount < 2}
              className="bg-amber-600 text-white p-1.5 text-sm rounded-lg hover:bg-amber-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title={activeRunningCount < 2 ? 'Pause all requires at least 2 running campaigns' : `Pause all ${activeRunningCount} running campaigns`}
            >
              <Pause size={16} />
            </button>
            <button
              onClick={toggleSelectMode}
              className={`px-3 py-1.5 text-sm rounded-lg transition-colors flex items-center gap-2 justify-center ${
                selectMode ? 'bg-gray-200 text-gray-800' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              <ListChecks size={16} /> {selectMode ? 'Cancel Select' : 'Select'}
            </button>
            <button
              onClick={page.editor.openCreate}
              className="bg-indigo-600 text-white px-3 py-1.5 text-sm rounded-lg hover:bg-indigo-700 transition-colors flex items-center gap-2 flex-1 sm:flex-none justify-center"
            >
              <Plus size={16} /> New Campaign
            </button>
          </div>
        </div>

        {selectMode && (
          <SelectionBar
            count={selectedIds.size}
            total={campaigns.length}
            onSelectAll={handleSelectAll}
            onDelete={handleBulkDelete}
            onCancel={toggleSelectMode}
            deleting={bulkDeleting}
            itemLabel="campaign"
          />
        )}

        {/* Campaign Cards */}
        <div className="space-y-3 sm:space-y-4">
          {campaigns.length === 0 && (
            <div className="bg-white rounded-xl shadow p-8 text-center text-gray-500">
              No campaigns yet. Create one to get started!
            </div>
          )}
          {campaigns.map(campaign => {
            const displayStatus = getDisplayStatus(campaign);
            const nextWindowText = formatNextWindow(campaign.next_window_open);
            return (
            <div key={campaign.id} className="bg-white rounded-xl shadow p-3 sm:p-4 md:p-5">
              <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                {selectMode && (
                  <input
                    type="checkbox"
                    checked={selectedIds.has(campaign.id)}
                    onChange={() => toggleSelected(campaign.id)}
                    className="mt-1 sm:mt-0 w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 flex-shrink-0"
                  />
                )}
                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="font-medium text-gray-900 truncate text-sm sm:text-base">{campaign.name}</p>
                    <StatusBadge status={displayStatus} text={displayStatus === 'cooldown' ? 'cooldown' : undefined} />
                    {campaign.send_timezone && (
                      <span className="px-1.5 py-0.5 text-[10px] font-medium rounded-full bg-amber-50 text-amber-700 border border-amber-200 whitespace-nowrap">
                        {campaign.send_timezone === 'US/Eastern' ? 'ET' : campaign.send_timezone === 'US/Pacific' ? 'PT' : campaign.send_timezone}
                      </span>
                    )}
                  </div>
                  <p className="text-xs sm:text-sm text-gray-500 truncate">{campaign.subject}</p>
                  {displayStatus === 'cooldown' && (
                    <p className="text-xs text-amber-700 mt-1">
                      {campaign.effective_reason || (nextWindowText ? `Waiting for next send window: ${nextWindowText}` : 'Worker is in cooldown until the next send window.')}
                    </p>
                  )}
                  {/* Inbox & SMTP info */}
                  <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1.5">
                    {getInboxInfo(campaign).map(info => {
                      const smtp = info.inbox ? getSmtpForInbox(info.inbox) : null;
                      const deliveryLabel = getDeliveryLabel(smtp);
                      return (
                        <div key={info.id} className="flex items-center gap-1.5 text-xs">
                          <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded font-medium ${info.type === 'deleted' ? 'bg-red-50 text-red-600' : 'bg-indigo-50 text-indigo-700'}`}>
                            <Mail size={10} /> {info.email}{info.type === 'deleted' ? ' (deleted)' : ''}
                          </span>
                          {deliveryLabel && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded">
                              via {deliveryLabel}
                            </span>
                          )}
                        </div>
                      );
                    })}
                    {campaign.reply_to_email && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-blue-50 text-blue-700 rounded text-xs">
                        <Reply size={10} /> Reply-To: {campaign.reply_to_email}
                      </span>
                    )}
                    {campaign.attachments && campaign.attachments.length > 0 && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-amber-50 text-amber-700 rounded text-xs">
                        <Paperclip size={10} /> {campaign.attachments.length} attachment{campaign.attachments.length > 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                  <div className="mt-2">
                    <div className="flex justify-between text-xs text-gray-500 mb-1">
                      <span>{campaign.total_sent} / {campaign.total_recipients} sent</span>
                      <span>{getProgress(campaign)}%</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-1.5">
                      <div className="bg-indigo-600 h-1.5 rounded-full transition-all" style={{ width: `${getProgress(campaign)}%` }} />
                    </div>
                    {campaign.total_failed > 0 && (
                      <p className="text-xs text-red-500 mt-1">{campaign.total_failed} failed</p>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1.5 sm:gap-2 flex-shrink-0">
                  {campaign.status === 'running' && (
                    <button onClick={() => handlePause(campaign.id)} className="p-1.5 sm:p-2 text-amber-600 hover:bg-amber-50 rounded-lg transition-colors" title="Pause">
                      <Pause size={16} />
                    </button>
                  )}
                  {campaign.status === 'paused' && (
                    <button onClick={() => handleResume(campaign.id)} className="p-1.5 sm:p-2 text-emerald-600 hover:bg-emerald-50 rounded-lg transition-colors" title="Resume">
                      <Play size={16} />
                    </button>
                  )}
                  {['draft', 'scheduled'].includes(campaign.status) && (
                    <button onClick={() => handleStart(campaign.id)} className="p-1.5 sm:p-2 text-emerald-600 hover:bg-emerald-50 rounded-lg transition-colors" title="Start">
                      <Play size={16} />
                    </button>
                  )}
                  <button
                    onClick={() => handleViewRecipients(campaign)}
                    className="p-1.5 sm:p-2 text-violet-600 hover:bg-violet-50 rounded-lg transition-colors"
                    title="View Recipients"
                  >
                    <Users size={16} />
                  </button>
                  {['running', 'paused'].includes(campaign.status) && (
                    <button
                      onClick={() => toggleTerminal(campaign.id)}
                      className={`flex items-center gap-1 px-2 py-1 sm:py-1.5 rounded-lg text-[11px] font-medium transition-colors ${openTerminals[campaign.id] ? 'bg-gray-900 text-green-400' : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700'}`}
                    >
                      <Terminal size={14} />
                      <span>View Activity</span>
                    </button>
                  )}
                  <button onClick={() => handleViewStats(campaign.id)} className="p-1.5 sm:p-2 text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors" title="Stats">
                    <BarChart3 size={16} />
                  </button>
                  {['draft', 'scheduled'].includes(campaign.status) && (
                    <button onClick={() => handleEditCampaign(campaign)} className="p-1.5 sm:p-2 text-blue-600 hover:bg-blue-50 rounded-lg transition-colors" title="Edit">
                      <Pencil size={16} />
                    </button>
                  )}
                  {!['completed', 'cancelled'].includes(campaign.status) && (
                    <button onClick={() => handleCancel(campaign.id)} className="p-1.5 sm:p-2 text-red-500 hover:bg-red-50 rounded-lg transition-colors" title="Cancel">
                      <XCircle size={16} />
                    </button>
                  )}
                  <button onClick={() => handleDelete(campaign.id)} className="p-1.5 sm:p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors" title="Delete">
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {/* Live Terminal Panel */}
              {openTerminals[campaign.id] && (() => {
                const tData = openTerminals[campaign.id];
                const tLogs = tData.logs;
                const tStatus = tData.status;
                const tSchedule = tData.schedule;
                return (
                <div className="mt-3 rounded-lg overflow-hidden border border-gray-700">
                  {/* Terminal Header */}
                  <div className="bg-gray-800 px-3 py-1.5 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="flex gap-1">
                        <div className="w-2.5 h-2.5 rounded-full bg-red-500" />
                        <div className="w-2.5 h-2.5 rounded-full bg-yellow-500" />
                        <div className="w-2.5 h-2.5 rounded-full bg-green-500" />
                      </div>
                      <span className="text-xs text-gray-400 font-mono">campaign:{campaign.id} — live</span>
                      {/* Timezone badge */}
                      {campaign.send_timezone && (
                        <span className={`inline-flex items-center px-1 py-0 rounded text-[9px] font-mono font-bold ${
                          campaign.send_timezone === 'US/Eastern' ? 'bg-blue-900/60 text-blue-400' : 'bg-orange-900/60 text-orange-400'
                        }`}>
                          {campaign.send_timezone === 'US/Eastern' ? 'ET' : campaign.send_timezone === 'US/Pacific' ? 'PT' : campaign.send_timezone}
                        </span>
                      )}
                      {/* Per-inbox mode badges */}
                      {tSchedule?.inboxes && tSchedule.inboxes.length > 0 && (
                        <div className="flex items-center gap-1 ml-1">
                          {tSchedule.inboxes.map((ib: { id: number; email: string; mode: string; batch_size: number }, idx: number) => {
                            const modeColors: Record<string, string> = {
                              ACTIVE: 'bg-green-900/60 text-green-400',
                              HYPER: 'bg-purple-900/60 text-purple-400',
                              DISTRACTED: 'bg-yellow-900/60 text-yellow-400',
                              OFFLINE: 'bg-red-900/60 text-red-400',
                            };
                            return (
                              <span key={idx} className={`inline-flex items-center gap-0.5 px-1 py-0 rounded text-[9px] font-mono ${modeColors[ib.mode] || 'bg-gray-700 text-gray-400'}`}
                                title={`${ib.email} — ${ib.mode} mode (batch: ${ib.batch_size})`}>
                                {ib.email.split('@')[0]}:{ib.mode[0]}
                              </span>
                            );
                          })}
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {/* Status indicator */}
                      {(() => {
                        void tickCounter;
                        const now = page.activity.serverNow();
                        const sched = tSchedule;
                        let label = tStatus.state.toUpperCase();
                        let colorClass = 'bg-gray-700 text-gray-400';
                        let dotClass = 'bg-gray-500';
                        
                        if (sched?.emails) {
                          const nextEmail = sched.emails.find(e => (e.send_at - now) > -2);
                          const allSent = !nextEmail;
                          
                          if (!allSent && nextEmail) {
                            const secsUntil = Math.max(0, Math.round(nextEmail.send_at - now));
                            if (secsUntil > 0) {
                              label = `NEXT IN ${secsUntil}s`;
                              colorClass = 'bg-cyan-900/50 text-cyan-400';
                              dotClass = 'bg-cyan-400 animate-pulse';
                            } else {
                              label = 'SENDING';
                              colorClass = 'bg-green-900/50 text-green-400';
                              dotClass = 'bg-green-400 animate-pulse';
                            }
                          } else if (sched.total_remaining === 0) {
                            label = 'COMPLETED';
                            colorClass = 'bg-green-900/50 text-green-400';
                            dotClass = 'bg-green-400';
                          } else if (sched.cooldown_until) {
                            const cooldownStart = sched.cooldown_start || sched.cooldown_until;
                            const inCooldownPhase = now >= cooldownStart;
                            if (inCooldownPhase) {
                              const cdSecs = Math.max(0, Math.round(sched.cooldown_until - now));
                              if (cdSecs > 0) {
                                const m = Math.floor(cdSecs / 60);
                                const s = cdSecs % 60;
                                label = `COOLDOWN ${m}m${String(s).padStart(2, '0')}s`;
                                colorClass = 'bg-yellow-900/50 text-yellow-400';
                                dotClass = 'bg-yellow-400 animate-pulse';
                              } else {
                                label = 'STARTING';
                                colorClass = 'bg-cyan-900/50 text-cyan-400';
                                dotClass = 'bg-cyan-400 animate-pulse';
                              }
                            } else {
                              const secsLeft = Math.max(0, Math.round(cooldownStart - now));
                              label = `DELIVERING ${secsLeft}s`;
                              colorClass = 'bg-green-900/50 text-green-400';
                              dotClass = 'bg-green-400 animate-pulse';
                            }
                          }
                        } else if (tStatus.state === 'completed') {
                          label = 'COMPLETED';
                          colorClass = 'bg-green-900/50 text-green-400';
                          dotClass = 'bg-green-400';
                        } else if (tStatus.state === 'paused') {
                          label = 'PAUSED';
                          colorClass = 'bg-orange-900/50 text-orange-400';
                          dotClass = 'bg-orange-400';
                        } else if (tStatus.state === 'cancelled') {
                          label = 'CANCELLED';
                          colorClass = 'bg-red-900/50 text-red-400';
                          dotClass = 'bg-red-400';
                        } else if (tStatus.state === 'cooldown' && tStatus.cooldown_remaining) {
                          label = `COOLDOWN ${Math.floor(tStatus.cooldown_remaining / 60)}m${tStatus.cooldown_remaining % 60}s`;
                          colorClass = 'bg-yellow-900/50 text-yellow-400';
                          dotClass = 'bg-yellow-400 animate-pulse';
                        } else if (tStatus.state === 'sending') {
                          label = 'SENDING';
                          colorClass = 'bg-green-900/50 text-green-400';
                          dotClass = 'bg-green-400 animate-pulse';
                        }
                        
                        return (
                          <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${colorClass}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${dotClass}`} />
                            {label}
                          </span>
                        );
                      })()}
                      <button onClick={() => toggleTerminal(campaign.id)} className="text-gray-500 hover:text-gray-300 transition-colors">
                        <X size={14} />
                      </button>
                    </div>
                  </div>
                  {/* Terminal Body */}
                  <div
                    ref={(element) => page.activity.setTerminalElement(campaign.id, element)}
                    className="bg-gray-950 px-3 py-2 h-48 overflow-y-auto font-mono text-xs leading-relaxed scrollbar-thin"
                    style={{ scrollbarWidth: 'thin', scrollbarColor: '#374151 transparent' }}
                  >
                    {tLogs.length === 0 && !tSchedule ? (
                      <div className="text-gray-600 flex items-center gap-2">
                        <span className="animate-pulse">●</span> Waiting for activity...
                      </div>
                    ) : (
                      <>
                        {tLogs.map((line, i) => (
                          <div key={i} className={`${
                            line.includes('✅') ? 'text-green-400' :
                            line.includes('❌') ? 'text-red-400' :
                            line.includes('📦') ? 'text-cyan-400' :
                            line.includes('⏸') ? 'text-yellow-400' :
                            line.includes('📧') ? 'text-gray-300' :
                            'text-gray-400'
                          }`}>
                            {line}
                          </div>
                        ))}
                        {/* Live countdown status — ticks every 1s */}
                        {(() => {
                          void tickCounter;
                          if (!tSchedule) {
                            if (tStatus.state === 'idle' && tStatus.message) {
                              return (
                                <div className="text-gray-500 mt-1 flex items-center gap-1.5 border-t border-gray-800 pt-1">
                                  <span>💤</span> {tStatus.message}
                                </div>
                              );
                            }
                            if (tStatus.state === 'cooldown') {
                              return (
                                <div className="text-yellow-400 mt-1 flex items-center gap-1.5 border-t border-gray-800 pt-1">
                                  <span>⏸</span> {tStatus.message || 'Worker cooldown active. Waiting for next send window.'}
                                </div>
                              );
                            }
                            return null;
                          }
                          const sched = tSchedule;
                          const now = page.activity.serverNow();
                          const nextEmail = sched.emails?.find(e => (e.send_at - now) > -2);
                          const sentCount = sched.emails ? sched.emails.filter(e => (e.send_at - now) <= -2).length : 0;
                          const allSent = sentCount >= (sched.emails?.length || 0);
                          
                          if (!allSent && nextEmail) {
                            const secsUntil = Math.max(0, Math.round(nextEmail.send_at - now));
                            if (secsUntil > 0) {
                              return (
                                <div className="text-cyan-400 mt-1 border-t border-gray-800 pt-1 space-y-0.5">
                                  <div className="flex items-center gap-1.5">
                                    <span className="animate-pulse">⏳</span>
                                    <span>Next email in <span className="text-white font-bold">{secsUntil}s</span></span>
                                  </div>
                                  <div className="text-gray-500 pl-5">
                                    #{nextEmail.idx}/{nextEmail.total} → {nextEmail.email}{nextEmail.inbox ? ` via ${nextEmail.inbox.split('@')[0]}` : ''}{nextEmail.mode ? ` [${nextEmail.mode}]` : ''}{nextEmail.tpl ? ` [${nextEmail.tpl}]` : ''}
                                  </div>
                                </div>
                              );
                            }
                            return (
                              <div className="text-green-400 mt-1 flex items-center gap-1.5 border-t border-gray-800 pt-1">
                                <span className="animate-pulse">📤</span>
                                Sending #{nextEmail.idx}/{nextEmail.total} → {nextEmail.email}{nextEmail.inbox ? ` via ${nextEmail.inbox.split('@')[0]}` : ''}...
                              </div>
                            );
                          }
                          
                          if (sched.total_remaining === 0) {
                            return (
                              <div className="text-green-400 mt-1 border-t border-gray-800 pt-1 space-y-1">
                                <div className="flex items-center gap-1.5">
                                  <span>🎉</span>
                                  <span className="font-bold">Campaign completed!</span>
                                </div>
                                <div className="text-gray-500 pl-5">
                                  All emails have been sent successfully.
                                </div>
                              </div>
                            );
                          }
                          
                          if (sched.cooldown_until) {
                            const cooldownStart = sched.cooldown_start || sched.cooldown_until;
                            const inCooldownPhase = now >= cooldownStart;
                            if (inCooldownPhase) {
                              const cdSecs = Math.max(0, Math.round(sched.cooldown_until - now));
                              if (cdSecs > 0) {
                                const mins = Math.floor(cdSecs / 60);
                                const secs = cdSecs % 60;
                                return (
                                  <div className="text-yellow-400 mt-1 border-t border-gray-800 pt-1 space-y-0.5">
                                    <div className="flex items-center gap-1.5">
                                      <span className="animate-pulse">⏸</span>
                                      <span>Cooldown: <span className="text-white font-bold">{mins}m {String(secs).padStart(2, '0')}s</span> before next batch</span>
                                    </div>
                                    {sched.total_remaining != null && (
                                      <div className="text-gray-500 pl-5">
                                        {sched.total_remaining} emails remaining
                                        {sched.inboxes && sched.inboxes.length > 1 && (
                                          <span> · {sched.inboxes.map((ib: { email: string; mode: string }) => `${ib.email.split('@')[0]}[${ib.mode[0]}]`).join(', ')}</span>
                                        )}
                                      </div>
                                    )}
                                  </div>
                                );
                              }
                              return (
                                <div className="text-cyan-400 mt-1 flex items-center gap-1.5 border-t border-gray-800 pt-1">
                                  <span className="animate-pulse">🔄</span> Starting next batch...
                                </div>
                              );
                            } else {
                              const secsLeft = Math.max(0, Math.round(cooldownStart - now));
                              return (
                                <div className="text-green-400 mt-1 border-t border-gray-800 pt-1 space-y-0.5">
                                  <div className="flex items-center gap-1.5">
                                    <span className="animate-pulse">📤</span>
                                    <span>Delivering batch... last email in <span className="text-white font-bold">{secsLeft}s</span></span>
                                  </div>
                                </div>
                              );
                            }
                          }
                          
                          return null;
                        })()}
                      </>
                    )}
                  </div>
                </div>
                );
              })()}
            </div>
            );
          })}
        </div>
      </div>
      )}

      {/* Create/Edit Campaign Modal */}
      <Modal isOpen={showModal} onClose={page.editor.close} title={editingCampaign ? "Edit Campaign" : "Create Campaign"} size={multiTemplateIds.length >= 2 ? 'xl' : 'default'}>
        <CampaignForm
          editingCampaign={editingCampaign}
          templates={templates}
          multiTemplateIds={multiTemplateIds}
          onMultiTemplateToggle={handleMultiTemplateToggle}
          lists={lists}
          inboxes={inboxes}
          uploadedAttachments={uploadedAttachments}
          uploading={uploading}
          onFileSelect={handleFileSelect}
          onRemoveAttachment={removeAttachment}
          onSubmit={handleCreate}
          onCancel={page.editor.close}
        />
      </Modal>

      {/* Stats Modal */}
      <Modal isOpen={!!statsModal} onClose={page.stats.close} title={`${statsModal?.name || ''}`}>
        {statsModal && (
          <div className="space-y-5 max-h-[75vh] overflow-y-auto">
            {/* Status & Dates Row */}
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={statsModal.status} />
              {statsModal.created_at && <span className="text-xs text-gray-400">Created {new Date(statsModal.created_at).toLocaleString()}</span>}
              {statsModal.started_at && <span className="text-xs text-gray-400">| Started {new Date(statsModal.started_at).toLocaleString()}</span>}
              {statsModal.completed_at && <span className="text-xs text-gray-400">| Completed {new Date(statsModal.completed_at).toLocaleString()}</span>}
            </div>

            {/* Subject */}
            <div>
              <p className="text-xs font-medium text-gray-500 mb-1">Subject</p>
              <p className="text-sm font-semibold text-gray-900 bg-gray-50 px-3 py-2 rounded-lg">{statsModal.subject}</p>
            </div>

            {/* Reply-To */}
            {statsModal.reply_to_email && (
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1">Reply-To</p>
                <p className="text-sm text-gray-700 bg-gray-50 px-3 py-2 rounded-lg">{statsModal.reply_to_email}</p>
              </div>
            )}

            {/* Email Body Preview */}
            <div>
              <p className="text-xs font-medium text-gray-500 mb-1">Email Body</p>
              <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
                <div
                  className="p-3 sm:p-4 text-sm prose prose-sm max-w-none"
                  style={{ maxHeight: '280px', overflowY: 'auto' }}
                  dangerouslySetInnerHTML={{ __html: sanitizedStatsBody }}
                />
              </div>
            </div>

            {/* Attachments */}
            {statsModal.attachments && statsModal.attachments.length > 0 && (
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1">Attachments ({statsModal.attachments.length})</p>
                <div className="space-y-1.5">
                  {statsModal.attachments.map((att, idx) => (
                    <div key={idx} className="flex items-center gap-2 p-2 bg-gray-50 rounded-lg border border-gray-200">
                      {isImageType(att.content_type) ? (
                        <img src={`/uploads/${att.stored_name}`} alt={att.filename} className="w-10 h-10 object-cover rounded" />
                      ) : (
                        <div className="w-10 h-10 flex items-center justify-center bg-gray-100 rounded">
                          {getFileIcon(att.content_type)}
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-medium text-gray-800 truncate">{att.filename}</p>
                        <p className="text-[10px] text-gray-500">{formatFileSize(att.size)}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Stats Grid */}
            <div>
              <p className="text-xs font-medium text-gray-500 mb-2">Delivery Statistics</p>
              <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                <div className="bg-indigo-50 p-2.5 rounded-lg text-center">
                  <p className="text-lg font-bold text-indigo-700">{statsModal.progress.sent}</p>
                  <p className="text-[10px] text-indigo-500 font-medium">Sent</p>
                </div>
                <div className="bg-amber-50 p-2.5 rounded-lg text-center">
                  <p className="text-lg font-bold text-amber-700">{statsModal.progress.pending}</p>
                  <p className="text-[10px] text-amber-500 font-medium">Pending</p>
                </div>
                <div className="bg-red-50 p-2.5 rounded-lg text-center">
                  <p className="text-lg font-bold text-red-700">{statsModal.progress.failed}</p>
                  <p className="text-[10px] text-red-500 font-medium">Failed</p>
                </div>
                <div className="bg-emerald-50 p-2.5 rounded-lg text-center">
                  <p className="text-lg font-bold text-emerald-700">{statsModal.opens}</p>
                  <p className="text-[10px] text-emerald-500 font-medium">Opens</p>
                </div>
                <div className="bg-blue-50 p-2.5 rounded-lg text-center">
                  <p className="text-lg font-bold text-blue-700">{statsModal.clicks}</p>
                  <p className="text-[10px] text-blue-500 font-medium">Clicks</p>
                </div>
                <div className="bg-violet-50 p-2.5 rounded-lg text-center">
                  <p className="text-lg font-bold text-violet-700">{statsModal.total_replies || 0}</p>
                  <p className="text-[10px] text-violet-500 font-medium">Replies</p>
                </div>
                <div className="bg-orange-50 p-2.5 rounded-lg text-center">
                  <p className="text-lg font-bold text-orange-700">{statsModal.bounces}</p>
                  <p className="text-[10px] text-orange-500 font-medium">Bounces</p>
                </div>
                <div className="bg-gray-100 p-2.5 rounded-lg text-center">
                  <p className="text-lg font-bold text-gray-700">{statsModal.unsubscribes}</p>
                  <p className="text-[10px] text-gray-500 font-medium">Unsubs</p>
                </div>
              </div>
            </div>
          </div>
        )}
      </Modal>

      {/* Recipient Email Preview Modal */}
      <Modal
        isOpen={!!recipientPreview}
        onClose={closeRecipientPreview}
        title={`Email Preview — ${recipientPreview?.email || ''}`}
        zIndex={70}
        size="wide"
      >
        {recipientPreview && (
          <div className="space-y-5 max-h-[75vh] overflow-y-auto">
            {recipientPreview.preview_source === 'reconstructed' && (
              <div className="border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Historic preview reconstructed from the campaign and recipient data. Exact send-time snapshots are available for newer sends.
              </div>
            )}

            {/* From & Template Info */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1">From</p>
                <p className="text-sm text-gray-900">
                  {recipientPreview.sent_from_name && `${recipientPreview.sent_from_name} `}
                  {recipientPreview.sent_from_email
                    ? <>&lt;{recipientPreview.sent_from_email}&gt;</>
                    : '—'}
                </p>
              </div>
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1">Template</p>
                <p className="text-sm text-indigo-700 font-medium">{recipientPreview.template_name || 'Custom campaign content'}</p>
              </div>
            </div>

            {/* Sent At & Status */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1">Status</p>
                <div>
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${getStatusColor(recipientPreview.status)}`}>
                    {getStatusIcon(recipientPreview.status)}
                    <span className="capitalize">{recipientPreview.status}</span>
                  </span>
                </div>
              </div>
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1">Sent At</p>
                <p className="text-sm text-gray-900">
                  {new Date(recipientPreview.sent_at).toLocaleString()}
                </p>
              </div>
            </div>

            {/* Subject */}
            {recipientPreview.sent_subject && (
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1">Subject</p>
                <p className="text-sm font-semibold text-gray-900 bg-gray-50 px-3 py-2 rounded-lg">
                  {recipientPreview.sent_subject}
                </p>
              </div>
            )}

            {/* Email Body Preview */}
            <div>
              <p className="text-xs font-medium text-gray-500 mb-1">Email Body</p>
              <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
                <div
                  className="p-3 sm:p-4 text-sm prose prose-sm max-w-none"
                  style={{ maxHeight: '420px', overflowY: 'auto' }}
                  dangerouslySetInnerHTML={{ __html: sanitizedRecipientPreviewBody }}
                />
                </div>
              </div>
          </div>
        )}
      </Modal>

      {/* Recipient Details Full-Page Overlay */}
      {detailCampaign && (
        <div className="fixed inset-0 z-50 bg-white flex flex-col">
          {/* Header */}
          <div className="border-b border-gray-200 px-3 sm:px-4 md:px-6 py-3 sm:py-4 flex items-center gap-3">
            <button
              onClick={page.recipients.close}
              className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors flex-shrink-0"
            >
              <X size={20} className="text-gray-600" />
            </button>
            <div className="min-w-0 flex-1">
              <h2 className="font-semibold text-gray-900 text-sm sm:text-base truncate">{detailCampaign.name}</h2>
              <p className="text-xs text-gray-500 truncate">{detailCampaign.subject}</p>
            </div>
            <StatusBadge status={detailCampaign.status} />
          </div>

          {/* Status Count Badges */}
          <div className="border-b border-gray-100 px-3 sm:px-4 md:px-6 py-2 sm:py-3 overflow-x-auto">
            <div className="flex gap-1.5 sm:gap-2 min-w-max">
              <button
                onClick={() => handleFilterChange('')}
                className={`px-2.5 py-1 text-xs sm:text-sm rounded-full font-medium transition-colors ${
                  statusFilter === '' ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                All ({Object.values(statusCounts).reduce((a, b) => a + b, 0)})
              </button>
              {Object.entries(statusCounts).sort(([a], [b]) => {
                const order = ['sent', 'pending', 'failed', 'opened', 'clicked', 'replied', 'bounced', 'delivered', 'unsubscribed'];
                return order.indexOf(a) - order.indexOf(b);
              }).map(([status, count]) => (
                <button
                  key={status}
                  onClick={() => handleFilterChange(status as StatusFilter)}
                  className={`px-2.5 py-1 text-xs sm:text-sm rounded-full font-medium transition-colors flex items-center gap-1 ${
                    statusFilter === status ? 'bg-gray-900 text-white' : `${getStatusColor(status)} hover:opacity-80`
                  }`}
                >
                  {getStatusIcon(status)}
                  <span className="capitalize">{status}</span>
                  <span className="opacity-70">({count})</span>
                </button>
              ))}
            </div>
          </div>

          {/* Search */}
          <div className="px-3 sm:px-4 md:px-6 py-2 sm:py-3 border-b border-gray-100">
            <div className="relative max-w-sm">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                value={emailSearch}
                onChange={event => page.recipients.setSearch(event.target.value)}
                placeholder="Search by email..."
                className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-300"
              />
            </div>
          </div>

          {/* Recipient List */}
          <div className={`flex-1 overflow-y-auto px-3 sm:px-4 md:px-6 py-2 sm:py-3 ${recipientLoading ? 'opacity-50' : ''}`}>
            {filteredRecipients.length === 0 ? (
              <div className="text-center py-12 text-gray-500">
                <Users className="w-10 h-10 mx-auto mb-3 text-gray-300" />
                <p className="text-sm">No recipients found</p>
              </div>
            ) : (
              <>
                {/* Mobile: Cards */}
                <div className="sm:hidden space-y-2">
                  {filteredRecipients.map(r => (
                    <div key={r.id} className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                      <div className="flex items-start justify-between gap-2 mb-1.5">
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-medium text-gray-900 truncate">{r.email}</p>
                          {r.first_name && <p className="text-[10px] text-gray-500">{r.first_name} {r.last_name || ''}</p>}
                        </div>
                        <span className={`shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold ${getStatusColor(r.status)}`}>
                          {getStatusIcon(r.status)}
                          <span className="capitalize">{r.status}</span>
                        </span>
                      </div>
                      {r.sent_at && <p className="text-[10px] text-gray-400">Sent: {formatDate(r.sent_at)}</p>}
                      {r.template_name && (
                        <p className="text-[10px] text-indigo-500 mt-0.5">Template: {r.template_name}</p>
                      )}
                      {r.has_preview && (
                        <button
                          onClick={() => handleViewRecipientPreview(detailCampaign.id, r.id)}
                          disabled={previewLoading}
                          className="mt-2 inline-flex items-center gap-1 border border-cyan-500/40 px-2 py-1 text-[10px] font-medium text-cyan-600 disabled:opacity-50"
                        >
                          <Eye size={11} /> Preview sent email
                        </button>
                      )}
                      {r.error_message && (
                        <p className="text-[10px] text-red-600 mt-1 bg-red-50 px-2 py-1 rounded break-words">{r.error_message}</p>
                      )}
                      <div className="flex gap-3 mt-1.5 text-[10px] text-gray-400">
                        {r.open_count > 0 && <span>Opens: {r.open_count}</span>}
                        {r.click_count > 0 && <span>Clicks: {r.click_count}</span>}
                        {r.replied_at && <span className="text-purple-600">Replied</span>}
                        {r.bounced_at && <span className="text-red-600">Bounced</span>}
                      </div>
                    </div>
                  ))}
                </div>

                {/* Desktop: Table */}
                <div className="hidden sm:block">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-gray-500 uppercase border-b border-gray-200">
                        <th className="pb-2 pr-3 font-medium">Email</th>
                        <th className="pb-2 pr-3 font-medium">Name</th>
                        <th className="pb-2 pr-3 font-medium">Status</th>
                        <th className="pb-2 pr-3 font-medium">Template</th>
                        <th className="pb-2 pr-3 font-medium">Preview</th>
                        <th className="pb-2 pr-3 font-medium">Sent At</th>
                        <th className="pb-2 pr-3 font-medium">Opens</th>
                        <th className="pb-2 font-medium">Error</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {filteredRecipients.map(r => (
                        <tr key={r.id} className="hover:bg-gray-50">
                          <td className="py-2 pr-3">
                            <span className="text-gray-900 truncate block max-w-50 lg:max-w-xs">{r.email}</span>
                          </td>
                          <td className="py-2 pr-3 text-gray-500 whitespace-nowrap">
                            {r.first_name || '-'} {r.last_name || ''}
                          </td>
                          <td className="py-2 pr-3">
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${getStatusColor(r.status)}`}>
                              {getStatusIcon(r.status)}
                              <span className="capitalize">{r.status}</span>
                            </span>
                          </td>
                          <td className="py-2 pr-3 text-xs">
                            {r.template_name ? (
                              <span className="inline-flex items-center px-1.5 py-0.5 bg-indigo-50 text-indigo-700 rounded text-[11px] truncate max-w-40" title={r.template_name}>
                                {r.template_name}
                              </span>
                            ) : <span className="text-gray-300">-</span>}
                          </td>
                          <td className="py-2 pr-3 text-xs">
                            {r.has_preview ? (
                              <button
                                onClick={() => handleViewRecipientPreview(detailCampaign.id, r.id)}
                                disabled={previewLoading}
                                className="inline-flex items-center gap-1 border border-cyan-500/40 px-2 py-1 text-[11px] font-medium text-cyan-600 hover:bg-cyan-500/10 disabled:opacity-50"
                                title="Preview the email sent to this recipient"
                              >
                                <Eye size={11} /> View
                              </button>
                            ) : <span className="text-gray-300">-</span>}
                          </td>
                          <td className="py-2 pr-3 text-gray-500 whitespace-nowrap text-xs">
                            {formatDate(r.sent_at)}
                          </td>
                          <td className="py-2 pr-3 text-gray-500 text-xs">
                            <div className="flex gap-2">
                              {r.open_count > 0 && <span title="Opens">{r.open_count} opens</span>}
                              {r.click_count > 0 && <span title="Clicks">{r.click_count} clicks</span>}
                              {r.replied_at && <span className="text-purple-600" title={`Replied ${formatDate(r.replied_at)}`}>replied</span>}
                              {!r.open_count && !r.click_count && !r.replied_at && '-'}
                            </div>
                          </td>
                          <td className="py-2 max-w-50">
                            {r.error_message ? (
                              <span className="text-xs text-red-600 truncate block" title={r.error_message}>{r.error_message}</span>
                            ) : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="border-t border-gray-200 px-3 sm:px-4 md:px-6 py-2 sm:py-3 flex items-center justify-between">
              <p className="text-xs text-gray-500">
                Page {recipientPage} of {totalPages} ({recipientTotal} total)
              </p>
              <div className="flex gap-1.5">
                <button
                  onClick={() => handlePageChange(recipientPage - 1)}
                  disabled={recipientPage <= 1}
                  className="p-1.5 rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  onClick={() => handlePageChange(recipientPage + 1)}
                  disabled={recipientPage >= totalPages}
                  className="p-1.5 rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
