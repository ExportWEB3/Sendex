import type { ChangeEvent, FormEvent, ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';

// ── Authentication ────────────────────────────────────────────────────

export type UserRole = 'admin' | 'user';

export interface User {
  id: number;
  email: string;
  name: string | null;
  role: UserRole;
  is_active: boolean;
  account_expires_at: string | null;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  success: true;
  token: string;
  user: User;
}

export interface AuthOperationResult {
  success: boolean;
  error?: string;
}

export interface AuthContextValue {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  isAdmin: boolean;
  login: (email: string, password: string) => Promise<AuthOperationResult>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

export interface AuthProviderProps {
  children: ReactNode;
}

export type AuthContextType = AuthContextValue;

export interface ValidateActivationCodeResponse {
  valid: boolean;
  duration_days?: number;
  error?: string;
}

export interface ValidateActivationCodeRequest {
  code: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  activation_code: string;
  name?: string;
}

export interface RegisterResponse {
  success: boolean;
  error?: string;
  user?: User;
  message?: string;
}

export interface ForgotPasswordCaptchaRequest {
  email: string;
}

export interface CaptchaResponse extends SuccessResponse {
  captcha_id: string;
  captcha_question: string;
}

export interface PasswordResetRequest {
  email: string;
  captcha_id: string;
  captcha_answer: string;
  new_password: string;
  confirm_password: string;
}

export interface PasswordResetResponse {
  message: string;
}

export interface ActivationCode {
  id: number;
  code: string;
  duration_days: number;
  note: string | null;
  created_at: string;
  expires_at: string | null;
  used_at: string | null;
  used_by_email: string | null;
}

export interface UserInfo {
  id: number;
  email: string;
  name: string | null;
  role: string;
  is_active: boolean;
  account_expires_at: string | null;
  created_at: string | null;
  last_login_at: string | null;
}

export interface UserDetails {
  user: UserInfo;
  stats: {
    campaigns: number;
    smtp_accounts: number;
    inboxes: number;
    lists: number;
    total_recipients: number;
  };
  campaigns: Array<{ id: number; name: string; status: string; total_recipients: number; total_sent: number }>;
  smtp_accounts: Array<{ id: number; name: string; host: string; from_email: string; is_active: boolean }>;
  inboxes: Array<{ id: number; email: string; state: string; is_active: boolean; warmup_day: number; created_at?: string | null }>;
  lists: Array<{ id: number; name: string; recipient_count: number }>;
}

export interface ActivationCodesResponse {
  codes: ActivationCode[];
}

export interface CreateActivationCodeRequest {
  duration_days: number;
  expires_in_days?: number;
  note?: string;
  count?: number;
}

export interface CreateActivationCodeResponse extends ActivationCodesResponse, MessageResponse {}

export interface UsersResponse {
  users: UserInfo[];
}

export interface UpdateUserRoleRequest {
  role: UserRole;
}

export interface SettingsWorkflowState {
  selectedUserId: number | null;
  creatingCode: boolean;
  savingFallback: boolean;
}

export type SettingsWorkflowAction =
  | { type: 'user-details-opened'; userId: number }
  | { type: 'user-details-closed' }
  | { type: 'code-creation-started' }
  | { type: 'code-creation-finished' }
  | { type: 'fallback-save-started' }
  | { type: 'fallback-save-finished' };

export interface SettingsPageController {
  permissions: {
    isAdmin: boolean;
    currentUserId: number | null;
  };
  data: {
    activationCodes: ActivationCode[];
    users: UserInfo[];
    selectedUserDetails: UserDetails | null;
    fallbackConfigured: boolean;
    fallbackInfo: FallbackReplyInfo | null;
  };
  status: {
    initialLoad: boolean;
    loadingCodes: boolean;
    loadingUsers: boolean;
    loadingUserDetails: boolean;
    loadingFallback: boolean;
    creatingCode: boolean;
    savingFallback: boolean;
  };
  admin: {
    refreshCodes: () => Promise<unknown>;
    refreshUsers: () => Promise<unknown>;
    createCode: (durationDays: number, note: string) => Promise<boolean>;
    deleteCode: (codeId: number) => Promise<void>;
    copyCode: (code: string) => Promise<void>;
    openUserDetails: (userId: number) => void;
    closeUserDetails: () => void;
    toggleRole: (userId: number, currentRole: string) => Promise<void>;
  };
  fallback: {
    refresh: () => Promise<unknown>;
    save: (email: string, password: string) => Promise<boolean>;
    remove: () => Promise<boolean>;
  };
  actions: {
    refresh: () => Promise<void>;
  };
}

// ── HTTP infrastructure ───────────────────────────────────────────────

export type HttpMethod = 'get' | 'post' | 'put' | 'patch' | 'delete';
export type HttpAuthMode = 'required' | 'optional' | 'none';
export type HttpResponseType = 'json' | 'text' | 'blob' | 'arraybuffer';
export type QueryParameter = string | number | boolean | null | undefined;
export type QueryParameters = Record<string, QueryParameter | readonly QueryParameter[]>;

export interface RetryPolicy {
  maxRetries: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  jitter?: boolean;
}

export interface HttpRequestOptions<TBody = unknown> {
  endpoint: string;
  method?: HttpMethod;
  data?: TBody;
  query?: QueryParameters;
  headers?: Readonly<Record<string, string>>;
  auth?: HttpAuthMode;
  contentType?: string;
  responseType?: HttpResponseType;
  signal?: AbortSignal;
  timeoutMs?: number;
  retry?: number | RetryPolicy;
  withCredentials?: boolean;
}

export interface RequestNotificationOptions<TValue = unknown> {
  enabled?: boolean;
  message?: string | ((value: TValue) => string);
}

export interface HttpFetcherOptions<TBody = unknown, TResponse = unknown>
  extends Omit<HttpRequestOptions<TBody>, 'endpoint' | 'method' | 'data'> {
  apiEndPoint: string;
  httpMethod?: HttpMethod;
  reqData?: TBody;
  successNotification?: RequestNotificationOptions<TResponse>;
  errorNotification?: RequestNotificationOptions<unknown>;
}

export interface ApiQueryOptions<TResponse = unknown>
  extends Omit<HttpRequestOptions<never>, 'data' | 'method'> {
  cacheKey: string | readonly unknown[];
  enabled?: boolean;
  fallbackData?: TResponse;
  refreshInterval?: number | ((latestData: TResponse | undefined) => number);
  keepPreviousData?: boolean;
  revalidateOnFocus?: boolean;
  shouldRetryOnError?: boolean;
  errorNotification?: RequestNotificationOptions<unknown>;
  onSuccess?: (data: TResponse) => void;
}

export interface ApiErrorOptions {
  status?: number;
  code?: string;
  details?: unknown;
  isNetworkError?: boolean;
  isCanceled?: boolean;
  retryAfterMs?: number;
  cause?: unknown;
}

export interface SuccessResponse {
  success: boolean;
}

export interface MessageResponse extends SuccessResponse {
  message: string;
}

export interface BulkDeleteResource {
  delete: (id: number) => Promise<unknown>;
  forceDelete: (id: number) => Promise<unknown>;
}

export interface BulkDeleteResult {
  succeeded: number;
  failed: number;
}

// ── Sending accounts and inboxes ──────────────────────────────────────

export type SMTPProviderType = 'smtp' | 'ses_api' | 'ses_smtp' | 'brevo';
export type SMTPEncryption = 'none' | 'ssl' | 'tls';
export type SMTPAuthType = 'password' | 'oauth2' | null;

export interface SMTPAccount {
  id: number;
  name: string;
  host: string;
  port: number;
  username: string;
  password?: string;
  encryption: SMTPEncryption;
  from_email: string;
  from_name: string | null;
  hourly_limit: number;
  daily_limit: number;
  is_active: boolean;
  last_used_at: string | null;
  last_error: string | null;
  imap_host: string | null;
  imap_port: number | null;
  imap_username: string | null;
  has_imap: boolean;
  provider_type: SMTPProviderType;
  auth_type: SMTPAuthType;
  oauth2_client_id: string | null;
  oauth2_tenant_id: string | null;
  created_at: string;
}

export interface SMTPAccountInput {
  name?: string;
  host?: string;
  port?: number;
  username?: string;
  password?: string;
  encryption?: SMTPEncryption;
  from_email?: string;
  from_name?: string | null;
  hourly_limit?: number;
  daily_limit?: number;
  is_active?: boolean;
  imap_host?: string | null;
  imap_port?: number | null;
  imap_username?: string | null;
  imap_password?: string | null;
  provider_type?: SMTPProviderType;
  auth_type?: SMTPAuthType;
  oauth2_client_id?: string | null;
  oauth2_client_secret?: string | null;
  oauth2_tenant_id?: string | null;
}

export interface SMTPTestResponse extends MessageResponse {
  provider?: string;
  details?: Record<string, unknown>;
}

export interface ImapStatusResponse {
  total_smtp_accounts: number;
  accounts_with_imap: number;
  imap_active: boolean;
}

export interface ImapDetectionResponse extends MessageResponse {
  imap_host: string | null;
  imap_port: number | null;
  method: string;
  inboxes_updated: number;
}

export interface ImapTestResponse extends MessageResponse {
  host: string;
  port: number;
  inbox_accessible: boolean;
}

export interface ImapCheckScheduleResponse {
  last_check: string | null;
  next_check: string | null;
  interval_seconds: number;
  seconds_until_next: number | null;
  scheduler_active: boolean;
}

export interface ResendConfig {
  configured: boolean;
  resend_verified_domain: string;
  has_api_key: boolean;
  shared_imap_host: string;
  shared_imap_user: string;
  has_shared_imap: boolean;
}

export type InboxGroup = 'A' | 'B' | 'C';
export type InboxState = 'not_started' | 'warming_up' | 'warmed_up' | 'paused' | 'disabled';
export type InboxSendingMode = 'active' | 'distracted' | 'offline';

export interface Inbox {
  id: number;
  email: string;
  smtp_account_id: number;
  imap_host?: string;
  imap_port: number;
  imap_username?: string | null;
  imap_password?: string | null;
  reply_to_email?: string | null;
  group: InboxGroup;
  state: InboxState;
  warmup_day: number;
  daily_cap: number;
  current_daily_count: number;
  total_sent: number;
  total_received: number;
  total_replied: number;
  bounce_rate: number;
  spam_rate: number;
  open_rate: number;
  reply_rate: number;
  is_active: boolean;
  reply_enabled: boolean;
  has_imap: boolean;
  sending_mode: InboxSendingMode;
  health_score: number;
  last_mode_change: string | null;
  created_at: string;
  is_resend_inbox?: boolean;
  imap_auto_detected?: boolean;
  imap_detection_source?: string | null;
}

export interface InboxInput {
  email?: string;
  smtp_account_id?: number;
  imap_host?: string | null;
  imap_port?: number | null;
  imap_username?: string | null;
  imap_password?: string | null;
  reply_to_email?: string | null;
  group?: InboxGroup;
}

export interface FleetQuotaResponse {
  budget: number;
  used_today: number;
  remaining: number;
}

export interface MxStatus {
  inbox_id: number;
  mismatch: boolean;
  mx_imap_host: string | null;
  current_imap_host: string | null;
  message: string;
}

export interface FixImapResponse extends MessageResponse {
  imap_host: string;
  imap_port: number;
  tested: boolean;
}

export interface SetupReplyToRequest {
  reply_to_email: string;
  reply_to_password: string;
}

export interface SetupReplyToResponse extends MessageResponse {
  reply_to_email?: string;
  imap_host?: string;
  step: string;
}

export interface InboxDiagnosticEmailSample {
  from: string;
  to: string;
  subject: string;
  type: 'bounce' | 'read_receipt' | 'reply' | 'other';
  is_reply: boolean;
  is_bounce: boolean;
  is_read_receipt: boolean;
  in_reply_to: boolean;
  date: string;
}

export interface InboxDiagnosticCampaign {
  id: number;
  name: string;
  status: string;
  sent_recipients: string[];
}

export interface InboxDiagnosticMxInfo {
  domain?: string;
  mx_records?: string[];
  imap_host?: string | null;
  match?: boolean;
  error?: string;
}

export interface InboxDiagnosticImapConnection {
  status?: 'connected';
  host?: string | null;
  error?: string;
}

export interface InboxDiagnosticResponse {
  inbox_id: number;
  inbox_email: string;
  imap_host: string | null;
  imap_configured: boolean;
  reply_enabled: boolean;
  reply_to_email: string | null;
  emails_in_inbox: number;
  reply_emails: number;
  bounce_emails: number;
  read_receipt_emails: number;
  matched_to_campaigns: number;
  issues: string[];
  email_samples: InboxDiagnosticEmailSample[];
  campaigns_using_inbox: InboxDiagnosticCampaign[];
  all_campaign_recipients: string[];
  mx_info: InboxDiagnosticMxInfo | null;
  imap_connection: InboxDiagnosticImapConnection | null;
}

export interface InboxSendTestRequest {
  to_email: string;
}

export interface InboxSendTestResponse extends MessageResponse {
  status: 'sent';
  provider: string;
  details: { message_id?: string | null };
}

export interface FallbackReplyEmailResponse {
  configured: boolean;
  id?: number;
  email?: string;
  smtp_host?: string;
  smtp_port?: number;
  imap_host?: string;
  imap_port?: number;
  is_active?: boolean;
  created_at?: string;
}

export interface FallbackReplyEmailRequest {
  email: string;
  app_password: string;
  smtp_host?: string;
  smtp_port?: number;
  imap_host?: string;
  imap_port?: number;
}

export interface SetFallbackReplyEmailResponse extends SuccessResponse {
  id: number;
  email: string;
  smtp_host: string;
  smtp_port: number;
  imap_host: string | null;
  imap_port: number;
}

export interface FallbackReplyInfo {
  email?: string;
  smtp_host?: string;
  is_active?: boolean;
}

// ── Campaigns, lists, and templates ───────────────────────────────────

export interface AttachmentMeta {
  filename: string;
  stored_name: string;
  filepath?: string;
  content_type: string;
  size: number;
}

export interface AttachmentUploadResponse extends AttachmentMeta, SuccessResponse {}

export interface Campaign {
  id: number;
  name: string;
  subject: string;
  body_html: string;
  body_text: string | null;
  reply_to_email: string | null;
  status: 'draft' | 'scheduled' | 'running' | 'paused' | 'completed' | 'cancelled' | 'failed';
  effective_status?: 'draft' | 'scheduled' | 'running' | 'cooldown' | 'paused' | 'completed' | 'cancelled' | 'failed' | null;
  effective_reason?: string | null;
  next_window_open?: string | null;
  worker_runtime_status?: string | null;
  list_id: number;
  inbox_ids: number[];
  inbox_emails?: Record<string, string | null>;
  track_opens: boolean;
  track_clicks: boolean;
  attachments?: AttachmentMeta[] | null;
  template_ids?: number[] | null;
  template_rotation_state?: {
    shuffled_order: number[];
    current_index: number;
    current_batch_sent: number;
    current_batch_size: number;
    batch_toggle: boolean;
  } | null;
  scheduled_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  total_recipients: number;
  total_sent: number;
  total_failed: number;
  total_replies: number;
  send_timezone?: string | null;
  created_at: string;
}

export interface CampaignInput {
  name: string;
  subject: string;
  body_html: string;
  body_text?: string | null;
  list_id: number;
  inbox_ids: number[];
  reply_to_email?: string | null;
  send_timezone?: string;
  attachments?: AttachmentMeta[] | null;
  template_data?: Record<string, unknown>;
  template_ids?: number[];
}

export interface CampaignStats {
  campaign_id: number;
  name: string;
  subject: string;
  body_html: string;
  body_text: string;
  attachments: AttachmentMeta[] | null;
  status: string;
  reply_to_email: string | null;
  inbox_ids: number[];
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  progress: { total: number; sent: number; failed: number; pending: number };
  recipient_counts: Record<string, number>;
  total_recipients: number;
  total_sent: number;
  total_failed: number;
  total_replies: number;
  opens: number;
  clicks: number;
  unsubscribes: number;
  bounces: number;
}

export interface CampaignRecipientDetail {
  id: number;
  recipient_id: number;
  email: string;
  first_name: string | null;
  last_name: string | null;
  status: string;
  error_message: string | null;
  queued_at: string | null;
  sent_at: string | null;
  opened_at: string | null;
  clicked_at: string | null;
  replied_at: string | null;
  bounced_at: string | null;
  open_count: number;
  click_count: number;
  template_id: number | null;
  template_name: string | null;
  has_preview: boolean;
}

export interface CampaignRecipientsResponse {
  campaign_id: number;
  campaign_name: string;
  total: number;
  page: number;
  per_page: number;
  status_counts: Record<string, number>;
  recipients: CampaignRecipientDetail[];
}

export interface CampaignRecipientPreview {
  campaign_id: number;
  campaign_recipient_id: number;
  recipient_id: number;
  email: string;
  status: string;
  sent_at: string;
  template_id: number | null;
  template_name: string | null;
  preview_source: 'snapshot' | 'reconstructed';
  sent_subject: string | null;
  sent_body_html: string | null;
  sent_body_text: string | null;
  sent_from_email: string | null;
  sent_from_name: string | null;
}

export interface CampaignBatchFailure {
  id: number;
  reason: string;
}

export interface CampaignStartAllResponse extends SuccessResponse {
  started: number[];
  failed: CampaignBatchFailure[];
  started_count: number;
  failed_count: number;
  stagger_window_seconds: number;
}

export interface CampaignPauseAllResponse extends SuccessResponse {
  paused: number[];
  failed: CampaignBatchFailure[];
  paused_count: number;
  failed_count: number;
}

export interface CampaignBatchResult {
  success: boolean;
  started?: number[];
  paused?: number[];
  failed: CampaignBatchFailure[];
  started_count?: number;
  paused_count?: number;
  failed_count: number;
  stagger_window_seconds?: number;
}

export interface TerminalStatus {
  state: string;
  cooldown_remaining?: number;
  batch_size?: number;
  remaining?: number;
  message?: string;
}

export interface TerminalSchedule {
  phase: string;
  emails: Array<{
    email: string;
    send_at: number;
    seconds_until: number;
    idx: number;
    total: number;
    tpl?: string | null;
    inbox?: string | null;
    mode?: string | null;
  }>;
  cooldown_until?: number;
  cooldown_start?: number;
  cooldown_seconds?: number;
  total_remaining?: number;
  mode?: string;
  inboxes?: Array<{ id: number; email: string; mode: string; batch_size: number }>;
}

export interface CampaignActivityResponse {
  campaign_id: number;
  campaign_name: string;
  total_sent: number;
  total_failed: number;
  total_recipients: number;
  status: TerminalStatus;
  schedule: TerminalSchedule | null;
  logs: string[];
  server_time: number;
}

export interface CampaignTerminalData {
  logs: string[];
  status: TerminalStatus;
  schedule: TerminalSchedule | null;
}

export type StatusFilter = '' | 'pending' | 'sent' | 'failed' | 'opened' | 'clicked' | 'replied' | 'bounced';

export interface BulkSelectionState {
  enabled: boolean;
  selectedIds: Set<number>;
  isDeleting: boolean;
}

export type BulkSelectionAction =
  | { type: 'toggle-mode' }
  | { type: 'toggle-one'; id: number }
  | { type: 'toggle-all'; ids: number[] }
  | { type: 'delete-started' }
  | { type: 'delete-finished'; reset?: boolean }
  | { type: 'reset' };

export interface BulkSelectionController extends BulkSelectionState {
  selectedCount: number;
  allSelected: boolean;
  toggleMode: () => void;
  toggleOne: (id: number) => void;
  toggleAll: () => void;
  beginDelete: () => void;
  finishDelete: (reset?: boolean) => void;
  reset: () => void;
}

export interface CampaignEditorState {
  isOpen: boolean;
  editingCampaign: Campaign | null;
  pendingFiles: File[];
  uploadedAttachments: AttachmentMeta[];
  uploading: boolean;
  multiTemplateIds: number[];
}

export type CampaignEditorAction =
  | { type: 'open-create' }
  | { type: 'open-edit'; campaign: Campaign }
  | { type: 'close' }
  | { type: 'set-pending-files'; files: File[] }
  | { type: 'set-uploading'; uploading: boolean }
  | { type: 'append-attachment'; attachment: AttachmentMeta }
  | { type: 'remove-attachment'; index: number }
  | { type: 'toggle-template'; templateId: number }
  | { type: 'save-complete' };

export interface CampaignRecipientsState {
  campaign: Campaign | null;
  recipients: CampaignRecipientDetail[];
  total: number;
  page: number;
  loading: boolean;
  statusFilter: StatusFilter;
  statusCounts: Record<string, number>;
  emailSearch: string;
}

export type CampaignRecipientsAction =
  | { type: 'open'; campaign: Campaign }
  | { type: 'close' }
  | { type: 'load-started' }
  | {
    type: 'load-succeeded';
    recipients: CampaignRecipientDetail[];
    total: number;
    statusCounts: Record<string, number>;
    page: number;
  }
  | { type: 'load-finished' }
  | { type: 'filter-changed'; filter: StatusFilter }
  | { type: 'page-changed'; page: number }
  | { type: 'search-changed'; search: string };

export interface CampaignActivityState {
  terminals: Record<number, CampaignTerminalData>;
  tick: number;
}

export type CampaignActivityAction =
  | { type: 'opened'; campaignId: number }
  | { type: 'closed'; campaignId: number }
  | { type: 'updated'; campaignId: number; data: CampaignTerminalData }
  | { type: 'tick' };

export interface CampaignOperationsState {
  stats: CampaignStats | null;
  recipientPreview: CampaignRecipientPreview | null;
  recipientPreviewLoading: boolean;
  startingAll: boolean;
  pausingAll: boolean;
}

export type CampaignOperationsAction =
  | { type: 'stats-opened'; stats: CampaignStats }
  | { type: 'stats-closed' }
  | { type: 'recipient-preview-loading'; active: boolean }
  | { type: 'recipient-preview-opened'; preview: CampaignRecipientPreview }
  | { type: 'recipient-preview-closed' }
  | { type: 'starting-all'; active: boolean }
  | { type: 'pausing-all'; active: boolean };

export interface CampaignPageData {
  campaigns: Campaign[];
  lists: RecipientList[];
  inboxes: Inbox[];
  smtpAccounts: SMTPAccount[];
  templates: TemplateOption[];
}

export interface CampaignPageStatus {
  loading: boolean;
  initialLoad: boolean;
  lastUpdated: Date | null;
  startingAll: boolean;
  pausingAll: boolean;
}

export interface CampaignEditorController extends CampaignEditorState {
  openCreate: () => void;
  openEdit: (campaign: Campaign) => void;
  close: () => void;
  toggleTemplate: (templateId: number) => void;
  selectFiles: (event: ChangeEvent<HTMLInputElement>) => Promise<void>;
  removeAttachment: (index: number) => void;
}

export interface CampaignRecipientsController extends CampaignRecipientsState {
  visibleRecipients: CampaignRecipientDetail[];
  totalPages: number;
  open: (campaign: Campaign) => Promise<void>;
  close: () => void;
  changeFilter: (filter: StatusFilter) => Promise<void>;
  changePage: (page: number) => Promise<void>;
  setSearch: (search: string) => void;
}

export interface CampaignActivityController extends CampaignActivityState {
  toggle: (campaignId: number) => void;
  close: (campaignId: number) => void;
  setTerminalElement: (campaignId: number, element: HTMLDivElement | null) => void;
  serverNow: () => number;
}

export interface CampaignStatsController {
  selected: CampaignStats | null;
  close: () => void;
}

export interface CampaignRecipientPreviewController {
  selected: CampaignRecipientPreview | null;
  loading: boolean;
  open: (campaignId: number, campaignRecipientId: number) => Promise<void>;
  close: () => void;
}

export interface CampaignPageActions {
  refresh: () => Promise<void>;
  start: (id: number) => Promise<void>;
  pause: (id: number) => Promise<void>;
  resume: (id: number) => Promise<void>;
  cancel: (id: number) => Promise<void>;
  remove: (id: number) => Promise<void>;
  removeSelected: () => Promise<void>;
  startAll: () => Promise<void>;
  pauseAll: () => Promise<void>;
  submit: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  viewStats: (id: number) => Promise<void>;
}

export interface CampaignsPageController {
  data: CampaignPageData;
  status: CampaignPageStatus;
  editor: CampaignEditorController;
  recipients: CampaignRecipientsController;
  selection: BulkSelectionController;
  activity: CampaignActivityController;
  stats: CampaignStatsController;
  preview: CampaignRecipientPreviewController;
  actions: CampaignPageActions;
}

export interface DashboardSummary {
  activeInboxCount: number;
  warmingInboxCount: number;
  runningCampaignCount: number;
  totalRecipients: number;
}

export interface DashboardPageController {
  data: {
    inboxes: Inbox[];
    campaigns: Campaign[];
    lists: RecipientList[];
    queue: QueueStatus | null;
    userStats: UserStats | null;
    workers: WorkerStatus[];
  };
  status: {
    loading: boolean;
    initialLoad: boolean;
    lastUpdated: Date | null;
  };
  summary: DashboardSummary;
  refresh: () => Promise<void>;
}

export interface QueueOperationsState {
  resetting: boolean;
  resyncing: boolean;
}

export type QueueOperationsAction =
  | { type: 'resetting'; active: boolean }
  | { type: 'resyncing'; active: boolean };

export interface QueuePageController {
  data: {
    queue: QueueStatus | null;
    health: SystemHealth | null;
    userStats: UserStats | null;
    imapActive: boolean | null;
    workers: WorkerStatus[];
  };
  status: {
    loading: boolean;
    initialLoad: boolean;
    lastUpdated: Date | null;
    resetting: boolean;
    resyncing: boolean;
  };
  actions: {
    refresh: () => Promise<void>;
    startWorker: () => Promise<void>;
    stopWorker: () => Promise<void>;
    processNow: () => Promise<void>;
    resetSystem: () => Promise<void>;
    resetStats: () => Promise<void>;
    resync: () => Promise<void>;
  };
}

export interface ListRecipientView {
  listId: number;
  listName: string;
}

export interface ListsWorkflowState {
  createOpen: boolean;
  addRecipientsListId: number | null;
  recipientView: ListRecipientView | null;
  recipients: ListRecipient[];
  recipientTotal: number;
  recipientPage: number;
  recipientLoading: boolean;
  recipientSearch: string;
}

export type ListsWorkflowAction =
  | { type: 'create-opened' }
  | { type: 'create-closed' }
  | { type: 'add-opened'; listId: number }
  | { type: 'add-closed' }
  | { type: 'recipients-opened'; view: ListRecipientView }
  | { type: 'recipients-closed' }
  | { type: 'recipients-load-started' }
  | { type: 'recipients-loaded'; recipients: ListRecipient[]; total: number; page: number }
  | { type: 'recipients-load-finished' }
  | { type: 'recipient-search-changed'; search: string };

export interface ListsPageController {
  data: {
    lists: RecipientList[];
  };
  status: {
    loading: boolean;
    initialLoad: boolean;
    lastUpdated: Date | null;
  };
  dialogs: {
    createOpen: boolean;
    addRecipientsListId: number | null;
    openCreate: () => void;
    closeCreate: () => void;
    openAddRecipients: (listId: number) => void;
    closeAddRecipients: () => void;
  };
  recipients: {
    view: ListRecipientView | null;
    visible: ListRecipient[];
    total: number;
    page: number;
    loading: boolean;
    search: string;
    pageSize: number;
    open: (listId: number, listName: string) => void;
    close: () => void;
    changePage: (page: number) => void;
    setSearch: (search: string) => void;
  };
  selection: BulkSelectionController;
  actions: {
    refresh: () => Promise<void>;
    create: (event: FormEvent<HTMLFormElement>) => Promise<void>;
    remove: (id: number) => Promise<void>;
    removeSelected: () => Promise<void>;
    addRecipients: (event: FormEvent<HTMLFormElement>, listId: number) => Promise<void>;
  };
}

export interface TemplateEditorSource extends Partial<TemplateFormData> {
  id?: number;
  attachments?: AttachmentMeta[];
}

export interface TemplateEditorState {
  editorOpen: boolean;
  editingId: number | null;
  previewOpen: boolean;
  previewHtml: string;
  form: TemplateFormData;
  attachments: AttachmentMeta[];
  attachmentUploading: boolean;
}

export type TemplateEditorAction =
  | { type: 'editor-opened'; source?: TemplateEditorSource }
  | { type: 'editor-closed' }
  | { type: 'form-updated'; patch: Partial<TemplateFormData> }
  | { type: 'html-updated'; html: string; variables: string[] }
  | { type: 'subject-updated'; subject: string; variables: string[] }
  | { type: 'preview-opened'; html: string }
  | { type: 'preview-closed' }
  | { type: 'uploading'; active: boolean }
  | { type: 'attachment-added'; attachment: AttachmentMeta }
  | { type: 'attachment-removed'; index: number };

export interface TemplatesPageController {
  data: {
    templates: EmailTemplate[];
  };
  status: {
    loading: boolean;
    lastUpdated: Date | null;
  };
  editor: TemplateEditorState & {
    open: (source?: TemplateEditorSource) => void;
    close: () => void;
    update: (patch: Partial<TemplateFormData>) => void;
    updateHtml: (html: string) => void;
    updateSubject: (subject: string) => void;
    uploadAttachments: (event: ChangeEvent<HTMLInputElement>) => Promise<void>;
    removeAttachment: (index: number) => void;
  };
  preview: {
    open: boolean;
    html: string;
    show: (html: string) => void;
    close: () => void;
  };
  selection: BulkSelectionController;
  actions: {
    refresh: () => Promise<void>;
    save: (event: FormEvent) => Promise<void>;
    remove: (id: number) => Promise<void>;
    removeSelected: () => Promise<void>;
  };
}

export type SMTPFormAuthType = Exclude<SMTPAuthType, null>;
export type SMTPDisplayStatus = 'active' | 'inactive' | 'ready';

export interface SMTPAccountsEditorState {
  createOpen: boolean;
  editingAccount: SMTPAccount | null;
  createProvider: SMTPProviderType;
  editProvider: SMTPProviderType;
  createAuthType: SMTPFormAuthType;
  editAuthType: SMTPFormAuthType;
  showPassword: boolean;
  showOauthSecret: boolean;
  showImapPassword: boolean;
}

export type SMTPAccountsEditorAction =
  | { type: 'create-opened'; provider?: SMTPProviderType }
  | { type: 'create-closed' }
  | { type: 'edit-opened'; account: SMTPAccount }
  | { type: 'edit-closed' }
  | { type: 'provider-changed'; mode: 'create' | 'edit'; provider: SMTPProviderType }
  | { type: 'auth-changed'; mode: 'create' | 'edit'; authType: SMTPFormAuthType }
  | { type: 'secret-toggled'; secret: 'password' | 'oauth' | 'imap' };

export interface SMTPAccountsTestingState {
  testingReady: boolean;
  testingAccountIds: Set<number>;
  progress: ReadyTestProgress | null;
  failures: ReadyTestFailure[];
}

export type SMTPAccountsTestingAction =
  | { type: 'account-started'; id: number }
  | { type: 'account-finished'; id: number }
  | { type: 'ready-started'; total: number }
  | { type: 'ready-progressed'; progress: ReadyTestProgress }
  | { type: 'ready-finished'; failures: ReadyTestFailure[] }
  | { type: 'ready-cleaned-up' };

export interface SMTPAccountsPageController {
  data: {
    accounts: SMTPAccount[];
    resendConfig: ResendConfig | null;
    readyAccounts: SMTPAccount[];
  };
  status: {
    loading: boolean;
    initialLoad: boolean;
    lastUpdated: Date | null;
  };
  editor: SMTPAccountsEditorState & {
    openCreate: (provider?: SMTPProviderType) => void;
    closeCreate: () => void;
    openEdit: (account: SMTPAccount) => void;
    closeEdit: () => void;
    setCreateProvider: (provider: SMTPProviderType) => void;
    setEditProvider: (provider: SMTPProviderType) => void;
    setCreateAuthType: (authType: SMTPFormAuthType) => void;
    setEditAuthType: (authType: SMTPFormAuthType) => void;
    togglePassword: () => void;
    toggleOauthSecret: () => void;
    toggleImapPassword: () => void;
  };
  testing: SMTPAccountsTestingState;
  selection: BulkSelectionController;
  actions: {
    refresh: () => Promise<void>;
    test: (id: number) => Promise<void>;
    detectImap: (id: number) => Promise<void>;
    testImap: (id: number) => Promise<void>;
    remove: (id: number) => Promise<void>;
    removeSelected: () => Promise<void>;
    testAllReady: () => Promise<void>;
    create: (event: FormEvent<HTMLFormElement>) => Promise<void>;
    update: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  };
}

export interface InboxMxState {
  statuses: Record<number, MxStatus>;
  checking: Record<number, boolean>;
}

export interface InboxFixWorkflowState {
  inboxId: number | null;
  state: FixState;
  message: string;
  replyToEmail: string;
  replyToPassword: string;
}

export interface InboxDiagnosisState {
  inboxId: number | null;
  loading: boolean;
  result: InboxDiagnosticResponse | null;
  showAppPasswordSteps: boolean;
}

export interface InboxReplyEditorState {
  inboxId: number | null;
  email: string;
  saving: boolean;
}

export interface InboxCreationState {
  open: boolean;
  creating: boolean;
  showImapOverride: boolean;
  selectedSmtpId: number | null;
}

export interface InboxTestSendState {
  inbox: Inbox | null;
  recipient: string;
  sending: boolean;
}

export interface InboxesWorkflowState {
  mx: InboxMxState;
  fix: InboxFixWorkflowState;
  diagnosis: InboxDiagnosisState;
  replyEditor: InboxReplyEditorState;
  creation: InboxCreationState;
  testSend: InboxTestSendState;
}

export type InboxesWorkflowAction =
  | { type: 'mx-checking'; inboxId: number; checking: boolean }
  | { type: 'mx-checked'; inboxId: number; status: MxStatus }
  | { type: 'mx-cleared'; inboxId: number }
  | { type: 'fix-opened'; inboxId: number }
  | { type: 'fix-updated'; patch: Partial<InboxFixWorkflowState> }
  | { type: 'fix-closed' }
  | { type: 'diagnosis-started'; inboxId: number }
  | { type: 'diagnosis-finished'; result: InboxDiagnosticResponse }
  | { type: 'diagnosis-closed' }
  | { type: 'app-password-steps-toggled' }
  | { type: 'reply-edit-opened'; inboxId: number; email: string }
  | { type: 'reply-edit-updated'; email: string }
  | { type: 'reply-saving'; saving: boolean }
  | { type: 'reply-edit-closed' }
  | { type: 'create-opened' }
  | { type: 'create-updated'; patch: Partial<InboxCreationState> }
  | { type: 'create-closed' }
  | { type: 'test-opened'; inbox: Inbox }
  | { type: 'test-recipient-updated'; recipient: string }
  | { type: 'test-sending'; sending: boolean }
  | { type: 'test-closed' };

export interface InboxesPageController {
  data: {
    inboxes: Inbox[];
    smtpAccounts: SMTPAccount[];
    fleetQuota: FleetQuotaResponse | null;
    selectedSmtp: SMTPAccount | undefined;
    selectedSmtpIsApi: boolean;
  };
  status: {
    loading: boolean;
    initialLoad: boolean;
    lastUpdated: Date | null;
  };
  mx: InboxMxState;
  fix: InboxFixWorkflowState & {
    open: (inboxId: number) => Promise<void>;
    close: () => void;
    setEmail: (email: string) => void;
    setPassword: (password: string) => void;
    submit: () => Promise<void>;
  };
  diagnosis: InboxDiagnosisState & {
    run: (inboxId: number) => Promise<void>;
    close: () => void;
    toggleAppPasswordSteps: () => void;
  };
  replyEditor: InboxReplyEditorState & {
    open: (inboxId: number, email?: string) => void;
    close: () => void;
    setEmail: (email: string) => void;
    save: (inboxId: number, emailOverride?: string) => Promise<void>;
  };
  creation: InboxCreationState & {
    openModal: () => void;
    closeModal: () => void;
    setSmtpId: (id: number | null) => void;
    toggleImapOverride: () => void;
  };
  testSend: InboxTestSendState & {
    open: (inbox: Inbox) => void;
    close: () => void;
    setRecipient: (recipient: string) => void;
    submit: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  };
  selection: BulkSelectionController;
  actions: {
    refresh: () => Promise<void>;
    pauseWarmup: (id: number) => Promise<void>;
    resumeWarmup: (id: number) => Promise<void>;
    remove: (id: number) => Promise<void>;
    removeSelected: () => Promise<void>;
    create: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  };
}

export interface RecipientList {
  id: number;
  name: string;
  description: string | null;
  recipient_count: number;
  active_count: number;
  unsubscribed_count: number;
  bounced_count: number;
  is_active: boolean;
  created_at: string;
}

export interface ListRecipient {
  id: number;
  email: string;
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  status: string;
  total_sent: number;
  total_opened: number;
}

export interface ListRecipientsResponse {
  list_id: number;
  list_name: string;
  total: number;
  recipients: ListRecipient[];
}

export interface AddRecipientInput {
  email: string;
  first_name?: string;
  last_name?: string;
}

export interface CreateRecipientListRequest {
  name: string;
  description?: string;
}

export interface AddRecipientsRequest {
  recipients: AddRecipientInput[];
}

export interface AddRecipientsResponse {
  added: number;
}

export interface TemplateOption {
  id: number;
  name: string;
  category: string;
  subject_line: string;
  html_content: string;
  attachments?: AttachmentMeta[];
}

export interface EmailTemplate extends TemplateOption {
  description?: string;
  template_type?: string;
  available_variables?: string[];
  created_at: string;
  usage_count: number;
}

export interface TemplateFormData {
  name: string;
  description: string;
  category: string;
  subject_line: string;
  html_content: string;
  template_type: string;
  available_variables: string[];
}

export interface TemplateInput extends TemplateFormData {
  attachments: AttachmentMeta[] | null;
  text_fallback?: string;
}

// ── Queue and system monitoring ───────────────────────────────────────

export interface UserStats {
  user_id: number;
  today: {
    sent: number;
    failed: number;
    queued: number;
    campaign_sent?: number;
    warmup_sent?: number;
  };
  session: { sent: number; failed: number; queued: number };
  current: { queued: number };
  last_sent_at: string | null;
  last_failed_at: string | null;
}

export interface QueueStatus {
  worker: {
    running: boolean;
    num_workers: number;
    started_at: string | null;
    processed: number;
    sent: number;
    failed: number;
  };
  queue: {
    queued: { high: number; normal: number; low: number; total: number };
    current?: { queued: number; processing: number };
    today?: {
      sent: number;
      failed: number;
      redis_sent?: number;
      campaign_sent?: number;
      warmup_sent?: number;
    };
    historical?: {
      total_queued: number;
      total_sent: number;
      total_retried: number;
      total_failed: number;
    };
    processing: number;
    dead_letter: number;
    totals: { queued: number; sent: number; retried: number; failed: number };
  };
  user_stats?: UserStats;
}

export interface NonSmtpStatus {
  worker: {
    running: boolean;
    worker_num: number;
    started_at: string;
    processed: number;
    sent: number;
    failed: number;
    retried: number;
  };
  queue: QueueStatus;
  processing: number;
  dead_letters: number;
  totals_nonsmtp: QueueStatus;
}

export interface SystemHealth {
  status: string;
  timestamp: string;
  components: {
    database: { status: string; error?: string };
    redis: {
      status: string;
      error?: string;
      queues?: { high: number; normal: number; low: number; processing: number; dead_letter: number };
    };
    campaigns: { status: string; running_campaigns: number; stuck_recipients: number };
    auto_replies: { status: string; pending: number; stuck: number };
  };
}

export interface WorkerStatus {
  timezone: string;
  worker_name?: string;
  alive: boolean;
  status: string;
  local_time?: string;
  business_hours?: boolean;
  next_window_open?: string;
  updated_at?: string;
}

export interface WorkerStatusResponse {
  workers: WorkerStatus[];
  server_time?: string;
}

export interface SystemResetRequest {
  clear_queue?: boolean;
  reset_pending_recipients?: boolean;
  reset_auto_replies?: boolean;
}

export interface SystemResetResponse extends SuccessResponse {
  actions_taken: string[];
  errors: string[];
}

export interface SystemResyncResponse extends SuccessResponse {
  campaigns_checked: number;
  recipients_requeued: number;
  orphaned_processing_cleared: number;
  auto_replies_requeued: number;
  errors: string[];
}

export interface ProcessQueueResponse extends SuccessResponse {
  processed: number;
}

export interface KillSwitchStatus {
  enabled: boolean;
  message: string;
}

// ── Replies ───────────────────────────────────────────────────────────

export type ReplyEngine = 'smtp' | 'resend';
export type AiTone = 'professional' | 'friendly' | 'casual';
export type AiGoal = 'continue_conversation' | 'schedule_call' | 'provide_info' | 'custom';

export interface AutoReply {
  id: number;
  to_email: string;
  from_email?: string;
  subject: string;
  original_reply_subject: string | null;
  original_reply_snippet: string | null;
  body_text: string | null;
  status: string;
  scheduled_at: string | null;
  sent_at: string | null;
  error_message: string | null;
}

export interface ReplyCampaignSummary {
  id: number;
  name: string;
  subject: string;
  status: string;
  engine_type: string;
  total_sent: number;
  total_replies: number;
  reply_rate: number;
  started_at: string | null;
  auto_replies_total: number;
  auto_replies_pending: number;
  auto_replies_sent: number;
}

export interface ReplyCampaignsResponse {
  engine: string;
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
  campaigns: ReplyCampaignSummary[];
}

export interface EngineStats {
  engine: string;
  total_campaigns: number;
  total_sent: number;
  total_replies: number;
  reply_rate: number;
  auto_replies_pending: number;
  auto_replies_sent: number;
  campaigns_with_replies: number;
}

export interface EngineRepliesResponse {
  campaign_id: number;
  campaign_name: string;
  total: number;
  replies: AutoReply[];
}

export interface CheckRepliesRequest {
  enabled?: boolean;
  min_delay_minutes?: number;
  max_delay_minutes?: number;
  custom_message?: string;
  use_ai?: boolean;
  ai_tone?: string;
  ai_goal?: string;
  custom_goal?: string;
}

export interface CheckRepliesResponse {
  campaigns_checked: number;
  total_inboxes_checked: number;
  total_replies_found: number;
  total_auto_replies_scheduled: number;
  errors: string[];
  per_campaign: Array<{
    campaign_id: number;
    campaign_name: string;
    replies_found: number;
    auto_replies_scheduled: number;
  }>;
}

export interface ProcessAutoRepliesResponse {
  processed: number;
  queued: number;
  errors: string[];
}

export interface CancelAutoReplyResponse extends SuccessResponse {
  id: number;
  status: string;
}

export interface ReplyAiConfiguration {
  enabled: boolean;
  tone: AiTone;
  goal: AiGoal;
  customGoal: string;
  context: string;
  panelOpen: boolean;
}

export interface ReplyDashboardWorkflowState {
  page: number;
  repliesByCampaign: Record<number, AutoReply[]>;
  loadingReplyIds: Set<number>;
  expandedCampaignIds: Set<number>;
  checking: boolean;
  processing: boolean;
  checkResult: CheckRepliesResponse | null;
  processResult: ProcessAutoRepliesResponse | null;
  cancellingReplyId: number | null;
  ai: ReplyAiConfiguration;
  suppressSent: boolean;
  secondsUntilCheck: number | null;
  checkingStuckSeconds: number;
}

export type ReplyDashboardWorkflowAction =
  | { type: 'page-set'; page: number }
  | { type: 'campaign-toggled'; campaignId: number }
  | { type: 'replies-loading'; campaignId: number }
  | { type: 'replies-loaded'; campaignId: number; replies: AutoReply[] }
  | { type: 'replies-finished'; campaignId: number }
  | { type: 'check-started' }
  | { type: 'check-finished'; result?: CheckRepliesResponse }
  | { type: 'process-started' }
  | { type: 'process-finished'; result?: ProcessAutoRepliesResponse }
  | { type: 'cancel-started'; replyId: number }
  | { type: 'cancel-finished' }
  | { type: 'ai-updated'; patch: Partial<ReplyAiConfiguration> }
  | { type: 'ai-panel-toggled' }
  | { type: 'suppress-sent-toggled' }
  | { type: 'schedule-synced'; secondsUntilCheck: number | null }
  | { type: 'countdown-ticked' };

export interface ReplyDashboardController {
  data: {
    campaigns: ReplyCampaignSummary[];
    stats: EngineStats | null;
    repliesByCampaign: Record<number, AutoReply[]>;
  };
  status: {
    loading: boolean;
    initialLoad: boolean;
    lastUpdated: Date | null;
    checking: boolean;
    processing: boolean;
    cancellingReplyId: number | null;
    imapActive: boolean | null;
  };
  pagination: {
    page: number;
    totalPages: number;
    total: number;
    perPage: number;
    goTo: (page: number) => void;
    previous: () => void;
    next: () => void;
  };
  replies: {
    loadingIds: Set<number>;
    expandedIds: Set<number>;
    toggleCampaign: (campaignId: number) => void;
  };
  ai: ReplyAiConfiguration & {
    setEnabled: (enabled: boolean) => void;
    setTone: (tone: AiTone) => void;
    setGoal: (goal: AiGoal) => void;
    setCustomGoal: (goal: string) => void;
    setContext: (context: string) => void;
    togglePanel: () => void;
  };
  scheduler: {
    active: boolean;
    lastCheck: string | null;
    secondsUntilCheck: number | null;
    checkingStuckSeconds: number;
    progress: number;
  };
  filters: {
    suppressSent: boolean;
    toggleSuppressSent: () => void;
  };
  results: {
    check: CheckRepliesResponse | null;
    process: ProcessAutoRepliesResponse | null;
  };
  actions: {
    refresh: () => Promise<void>;
    check: () => Promise<void>;
    process: () => Promise<void>;
    cancel: (replyId: number) => Promise<void>;
  };
}

// ── Imports ───────────────────────────────────────────────────────────

export type ImportStage = 'idle' | 'previewing' | 'preview' | 'executing' | 'done' | 'error';
export type MultiImportStage = 'idle' | 'importing' | 'done' | 'error';

export interface ImportPreviewData {
  valid: boolean;
  ses_accounts: number;
  smtp_accounts: number;
  inboxes: number;
  lists: number;
  total_recipients: number;
  errors: string[];
  warnings: string[];
  ses_preview: Array<{ account_name: string; from_email_prefix: string; from_name: string }>;
  smtp_preview: Array<{
    account_name: string;
    from_email: string;
    from_name: string;
    host: string;
    port: number;
    has_imap: boolean;
  }>;
  inbox_preview: Array<{ email: string; account_name: string; group: string; start_warmup: boolean }>;
  list_preview: Array<{ list_name: string; list_description: string; recipient_count: number }>;
  import_token?: string;
}

export interface EntityImportStats {
  created: number;
  skipped: number;
  errors: string[];
}

export interface ResendImportStats extends EntityImportStats {
  tested: number;
  active: number;
  failed: number;
}

export interface RecipientImportStats {
  added: number;
  skipped: number;
  errors: string[];
}

export interface BulkImportStats {
  ses_accounts: ResendImportStats;
  smtp_accounts: EntityImportStats;
  inboxes: EntityImportStats;
  lists: EntityImportStats;
  recipients: RecipientImportStats;
}

export interface ImportExecutionResult extends SuccessResponse {
  summary: string;
  stats: BulkImportStats;
}

export interface ImportExecuteRequest {
  import_token: string;
}

export interface ImportPanelState {
  stage: ImportStage;
  file: File | null;
  preview: ImportPreviewData | null;
  result: ImportExecutionResult | null;
  errorMessage: string;
}

export type ImportPanelAction =
  | { type: 'preview-started'; file: File }
  | { type: 'preview-succeeded'; preview: ImportPreviewData }
  | { type: 'execution-started' }
  | { type: 'execution-succeeded'; result: ImportExecutionResult }
  | { type: 'failed'; message: string }
  | { type: 'reset' };

export interface ImportPanelController extends ImportPanelState {
  previewFile: (file: File) => Promise<void>;
  execute: () => Promise<void>;
  reset: () => void;
}

export interface ImportPanelProps {
  fileToImport?: File | null;
}

export interface MultiImportPanelProps {
  files: File[];
}

export interface ImportDetailSectionProps {
  label: string;
  open: boolean;
  toggle: () => void;
  children: ReactNode;
}

export interface MultiImportFileResult {
  filename: string;
  list_name: string;
  list_id: number | null;
  list_created: boolean;
  imported: number;
  duplicates: number;
  updated: number;
  skipped: number;
  errors: string[];
}

export interface MultiListImportResponse {
  results: MultiImportFileResult[];
}

export interface TemplateImportFileResult {
  filename: string;
  template_name: string | null;
  template_id: number | null;
  subject_line: string | null;
  errors: string[];
}

export interface MultiTemplateImportResponse {
  results: TemplateImportFileResult[];
}

export interface MultiFileImportState<TResult> {
  stage: MultiImportStage;
  results: TResult[];
  errorMessage: string;
}

export type MultiFileImportAction<TResult> =
  | { type: 'started' }
  | { type: 'succeeded'; results: TResult[] }
  | { type: 'failed'; message: string };

export interface MultiFileImportController<TResult> extends MultiFileImportState<TResult> {
  run: () => Promise<void>;
}

// ── Fleet Assistant ───────────────────────────────────────────────────

export interface AssistantDeleteCandidate {
  id: number;
  label: string;
  detail: string;
}

export interface AssistantVariableGroup {
  category: string;
  variables: string[];
  templates: string[];
  count: number;
  resolved_count: number;
  skipped_count?: number;
  resolved: boolean;
}

export interface AssistantVariableTemplatePreview {
  id: number;
  name: string;
  subject: string;
  template_type: string;
  variables: string[];
  pending_variables: string[];
  skipped_variables: string[];
  resolved: boolean;
  attachment_names: string[];
  body?: string;
}

export interface AssistantVariableTemplatePreviewsResponse {
  category: string;
  templates: AssistantVariableTemplatePreview[];
}

export interface AssistantImportResourceCounts {
  created: number;
  reused: number;
  updated: number;
  failed?: number;
}

export interface AssistantAction {
  type: 'delete_selection' | 'delete_confirmation' | 'action_selection' | 'create_campaigns_offer' | 'create_campaigns_plan' | 'bundle_import_review' | 'bundle_variables';
  action_ids?: number[];
  resource_type?: string;
  resource_ids?: number[];
  labels?: string[];
  warning?: string;
  status?: 'pending' | 'completed' | 'cancelled' | 'failed' | 'needs_input' | 'ready';
  candidates?: AssistantDeleteCandidate[];
  tool_name?: string;
  template_count?: number;
  list_count?: number;
  account_count?: number;
  mapping_count?: number;
  materialized?: boolean;
  issues?: string[];
  mapping_rows?: Array<{ template: string; list: string; account?: string; recipients?: number; source?: string }>;
  asset_summary?: { attachment?: number; link?: number; mixed?: number; plain?: number };
  inferred_name_count?: number;
  unresolved_count?: number;
  skipped_count?: number;
  skipped_categories?: string[];
  variable_groups?: AssistantVariableGroup[];
  import_counts?: Record<string, AssistantImportResourceCounts> | null;
  created?: number;
  reused?: number;
  failed?: number;
  rows?: Array<{ template: string; list: string; recipients?: number; inboxes?: number }>;
}

export interface AssistantMessageRecord {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  action?: AssistantAction | null;
  created_at?: string | null;
}

export interface AssistantMessage extends AssistantMessageRecord {
  localId?: string;
}

export interface AssistantConversation {
  id: number;
  title: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AssistantReply {
  reply: string;
  conversation_id: number;
  message_id: number;
  action?: AssistantAction | null;
}

export interface AssistantConversationsResponse {
  conversations: AssistantConversation[];
}

export interface AssistantConversationResponse {
  conversation: AssistantConversation;
  messages: AssistantMessageRecord[];
}

export interface AssistantChatRequest {
  message: string;
  conversation_id?: number | null;
}

export interface AssistantPrepareDeleteRequest {
  conversation_id: number;
  resource_type: string;
  resource_ids: number[];
}

export interface AssistantRunToolRequest {
  conversation_id: number;
  tool_name: string;
  resource_ids: number[];
}

export interface AssistantActionIdsRequest {
  action_ids: number[];
}

export interface AssistantConversationIdRequest {
  conversation_id: number;
}

export interface FleetAssistantState {
  isOpen: boolean;
  messages: AssistantMessage[];
  activeConversationId: number | null;
  input: string;
  isLoading: boolean;
  conversationLoading: boolean;
  actionLoadingId: number | null;
  hasUnread: boolean;
  showBubble: boolean;
  bubbleDismissed: boolean;
  showImport: boolean;
  showHistory: boolean;
  fileToImport: File | null;
  filesToImport: File[];
  templateFilesToImport: File[];
  deleteSelections: Record<number, number[]>;
  importingBundle: boolean;
  previewVariableCategory: string | null;
}

export type FleetAssistantAction =
  | { type: 'assistant-opened' }
  | { type: 'assistant-closed' }
  | { type: 'bubble-shown'; visible: boolean }
  | { type: 'bubble-dismissed' }
  | { type: 'input-updated'; input: string }
  | { type: 'history-set'; visible: boolean }
  | { type: 'conversation-loading' }
  | { type: 'conversation-loaded'; conversationId: number; messages: AssistantMessage[] }
  | { type: 'conversation-load-failed' }
  | { type: 'conversation-remembered'; conversationId: number }
  | { type: 'conversation-started' }
  | { type: 'send-started'; message: AssistantMessage; clearInput: boolean }
  | { type: 'assistant-reply-appended'; reply: AssistantReply; markUnread: boolean }
  | { type: 'local-error-appended'; message: AssistantMessage }
  | { type: 'send-finished' }
  | { type: 'action-started'; actionId: number }
  | { type: 'action-finished' }
  | { type: 'action-dismissed'; messageId: number }
  | { type: 'selection-toggled'; messageId: number; resourceId: number }
  | { type: 'selection-cleared'; messageId: number }
  | { type: 'import-opened'; singleFile: File | null; listFiles: File[]; templateFiles: File[] }
  | { type: 'import-closed' }
  | { type: 'bundle-import-started' }
  | { type: 'bundle-import-finished' }
  | { type: 'preview-opened'; category: string }
  | { type: 'preview-closed' };

export interface FleetAssistantController {
  ui: {
    isOpen: boolean;
    hasUnread: boolean;
    showBubble: boolean;
    bubbleDismissed: boolean;
    open: () => void;
    close: () => void;
    dismissBubble: () => void;
  };
  conversation: {
    messages: AssistantMessage[];
    conversations: AssistantConversation[];
    activeId: number | null;
    input: string;
    isLoading: boolean;
    historyLoading: boolean;
    showHistory: boolean;
    setInput: (input: string) => void;
    send: (messageOverride?: string) => Promise<void>;
    load: (conversationId: number) => Promise<void>;
    startNew: () => void;
    archive: (conversationId: number) => Promise<void>;
    toggleHistory: () => void;
  };
  imports: {
    show: boolean;
    file: File | null;
    listFiles: File[];
    templateFiles: File[];
    importingBundle: boolean;
    selectFiles: (files: File[]) => Promise<void>;
    importBundle: (file: File) => Promise<void>;
    close: () => void;
  };
  workflow: {
    actionLoadingId: number | null;
    deleteSelections: Record<number, number[]>;
    dismissAction: (messageId: number) => void;
    prepareCampaigns: () => Promise<void>;
    confirmCampaigns: () => Promise<void>;
    toggleSelection: (messageId: number, resourceId: number) => void;
    prepareDelete: (action: AssistantAction, resourceIds: number[], messageId?: number) => Promise<void>;
    runSelectedTool: (action: AssistantAction, resourceIds: number[], messageId?: number) => Promise<void>;
    resolveAction: (action: AssistantAction, resolution: 'confirm' | 'cancel') => Promise<void>;
  };
  preview: {
    category: string | null;
    open: (category: string) => void;
    close: () => void;
  };
}

// ── UI contracts ──────────────────────────────────────────────────────

export interface ThemeContextType {
  isDark: boolean;
  toggle: () => void;
}

export interface ThemeProviderProps {
  children: ReactNode;
}

export interface ConfirmProviderProps {
  children: ReactNode;
}

export interface HeaderProps {
  title: string;
  onRefresh?: () => void;
  lastUpdated?: Date | null;
}

export interface CampaignFormProps {
  editingCampaign?: Campaign | null;
  templates?: TemplateOption[];
  multiTemplateIds?: number[];
  onMultiTemplateToggle: (templateId: number) => void;
  lists: RecipientList[];
  inboxes: Inbox[];
  uploadedAttachments: AttachmentMeta[];
  uploading: boolean;
  onFileSelect: (event: ChangeEvent<HTMLInputElement>) => void;
  onRemoveAttachment: (index: number) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
}

export type CampaignTemplateMode = 'scratch' | 'single' | 'rotation';

export interface CampaignTemplateVariable {
  name: string;
  fallback: string;
}

export interface CampaignFormState {
  bodyHtml: string;
  subject: string;
  showRawHtml: boolean;
  customValues: Record<string, string>;
  perTemplateValues: Record<string, Record<number, string>>;
  splitVariables: Set<string>;
  selectedInboxIds: number[];
}

export type CampaignFormAction =
  | { type: 'body-updated'; bodyHtml: string }
  | { type: 'subject-updated'; subject: string }
  | { type: 'raw-html-set'; visible: boolean }
  | { type: 'template-content-applied'; subject: string; bodyHtml: string; resetSingleMode: boolean }
  | { type: 'custom-value-updated'; name: string; value: string }
  | { type: 'template-value-updated'; name: string; templateId: number; value: string }
  | { type: 'split-variable-toggled'; name: string }
  | { type: 'inbox-toggled'; inboxId: number; selected: boolean };

export interface CampaignFormControllerOptions {
  editingCampaign?: Campaign | null;
  templates: TemplateOption[];
  multiTemplateIds: number[];
  onMultiTemplateToggle: (templateId: number) => void;
}

export interface CampaignFormController {
  values: CampaignFormState;
  selection: {
    mode: CampaignTemplateMode;
    singleTemplate: TemplateOption | null;
    selectedTemplates: TemplateOption[];
    templateAttachments: NonNullable<TemplateOption['attachments']>;
  };
  variables: {
    all: string[];
    custom: CampaignTemplateVariable[];
    standard: string[];
    rotationCustom: CampaignTemplateVariable[];
    rotationStandard: string[];
    templateMap: Record<string, number[]>;
    rotationPreviewValues: Record<number, Record<string, string>>;
  };
  preview: {
    html: string;
    subject: string;
    isHtml: boolean;
  };
  actions: {
    toggleTemplate: (templateId: number) => void;
    setBodyHtml: (bodyHtml: string) => void;
    setSubject: (subject: string) => void;
    setRawHtml: (visible: boolean) => void;
    setCustomValue: (name: string, value: string) => void;
    setTemplateValue: (name: string, templateId: number, value: string) => void;
    toggleSplitVariable: (name: string) => void;
    toggleInbox: (inboxId: number, selected: boolean) => void;
  };
}

export interface AssistantTemplatePreviewProps {
  conversationId: number;
  category: string;
  onClose: () => void;
}

export interface AssistantTemplatePreviewState {
  selectedId: number | null;
  showSource: boolean;
}

export type AssistantTemplatePreviewAction =
  | { type: 'list-loaded'; firstTemplateId: number | null }
  | { type: 'template-selected'; templateId: number }
  | { type: 'selection-reset' }
  | { type: 'source-set'; visible: boolean };

export interface AssistantTemplatePreviewController {
  templates: AssistantVariableTemplatePreview[];
  selectedId: number | null;
  selectedIndex: number;
  selectedTemplate: AssistantVariableTemplatePreview | undefined;
  renderedDocument: string;
  showSource: boolean;
  listLoading: boolean;
  previewLoadingId: number | null;
  listError: string;
  previewError: string;
  actions: {
    select: (templateId: number) => void;
    move: (offset: number) => void;
    setShowSource: (visible: boolean) => void;
    retryList: () => Promise<void>;
    retryPreview: () => Promise<void>;
  };
}

export interface ListCardProps {
  list: RecipientList;
  onViewEmails: (listId: number, listName: string) => void;
  onAddRecipients: (listId: number) => void;
  onDelete: (listId: number) => void;
  selectMode?: boolean;
  selected?: boolean;
  onToggleSelect?: (listId: number) => void;
}

export type ConfirmVariant = 'danger' | 'warning' | 'info';

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  zIndex?: number;
  size?: 'default' | 'wide' | 'xl';
}

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  variant?: ConfirmVariant;
}

export interface ConfirmContextType {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

export interface BellNotification {
  id: number;
  timestamp: Date;
  title: string;
  description: string;
  type: 'info';
}

export interface ReplyDashboardProps {
  engine: ReplyEngine;
  title: string;
}

export interface SelectionBarProps {
  count: number;
  total: number;
  onSelectAll: () => void;
  onDelete: () => void;
  onCancel: () => void;
  deleting?: boolean;
  itemLabel: string;
}

export interface NavChildItem {
  to: string;
  icon: LucideIcon;
  label: string;
}

export interface NavItem extends NavChildItem {
  children?: NavChildItem[];
}

export interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: ReactNode;
  iconBg: string;
  subtitleColor?: string;
}

export type ToastType = 'success' | 'error' | 'info';

export interface Toast {
  id: number;
  message: string;
  type: ToastType;
  exiting?: boolean;
}

export interface ProtectedRouteProps {
  children: ReactNode;
  requireAdmin?: boolean;
}

export interface AppLayoutProps {
  children: ReactNode;
}

export type FixState = 'idle' | 'trying' | 'failed' | 'reply-to' | 'saving' | 'done';
export type Step = 'email' | 'captcha' | 'success';

export interface PasswordErrors {
  length: boolean;
  uppercase: boolean;
  lowercase: boolean;
  number: boolean;
  match: boolean;
}

export interface ReadyTestProgress {
  completed: number;
  total: number;
  succeeded: number;
  failed: number;
}

export interface ReadyTestFailure {
  id: number;
  name: string;
  message: string;
}

// ── Cache and utility contracts ───────────────────────────────────────

export interface CacheEntry<T = unknown> {
  data: T;
  timestamp: number;
}

export interface WarmupDaySource {
  warmup_day?: number | null;
  state?: string | null;
  created_at?: string | null;
}

declare global {
  interface Window {
    __addNotification?: (title: string, description: string) => void;
  }
}