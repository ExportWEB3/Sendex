import type {
  CampaignActivityAction,
  CampaignActivityState,
  CampaignEditorAction,
  CampaignEditorState,
  CampaignOperationsAction,
  CampaignOperationsState,
  CampaignRecipientsAction,
  CampaignRecipientsState,
} from '../../../typefiles';

export const INITIAL_CAMPAIGN_EDITOR_STATE: CampaignEditorState = {
  isOpen: false,
  editingCampaign: null,
  pendingFiles: [],
  uploadedAttachments: [],
  uploading: false,
  multiTemplateIds: [],
};

export const INITIAL_CAMPAIGN_RECIPIENTS_STATE: CampaignRecipientsState = {
  campaign: null,
  recipients: [],
  total: 0,
  page: 1,
  loading: false,
  statusFilter: '',
  statusCounts: {},
  emailSearch: '',
};

export const INITIAL_CAMPAIGN_ACTIVITY_STATE: CampaignActivityState = {
  terminals: {},
  tick: 0,
};

export const INITIAL_CAMPAIGN_OPERATIONS_STATE: CampaignOperationsState = {
  stats: null,
  recipientPreview: null,
  recipientPreviewLoading: false,
  startingAll: false,
  pausingAll: false,
};

export function campaignEditorReducer(
  state: CampaignEditorState,
  action: CampaignEditorAction,
): CampaignEditorState {
  switch (action.type) {
    case 'open-create':
      return {
        ...INITIAL_CAMPAIGN_EDITOR_STATE,
        isOpen: true,
      };
    case 'open-edit':
      return {
        ...INITIAL_CAMPAIGN_EDITOR_STATE,
        isOpen: true,
        editingCampaign: action.campaign,
        uploadedAttachments: action.campaign.attachments ?? [],
        multiTemplateIds: action.campaign.template_ids ?? [],
      };
    case 'close':
      return { ...state, isOpen: false, editingCampaign: null };
    case 'set-pending-files':
      return { ...state, pendingFiles: action.files };
    case 'set-uploading':
      return { ...state, uploading: action.uploading };
    case 'append-attachment':
      return {
        ...state,
        uploadedAttachments: [...state.uploadedAttachments, action.attachment],
      };
    case 'remove-attachment':
      return {
        ...state,
        uploadedAttachments: state.uploadedAttachments.filter((_, index) => index !== action.index),
      };
    case 'toggle-template':
      return {
        ...state,
        multiTemplateIds: state.multiTemplateIds.includes(action.templateId)
          ? state.multiTemplateIds.filter((id) => id !== action.templateId)
          : [...state.multiTemplateIds, action.templateId],
      };
    case 'save-complete':
      return {
        ...state,
        isOpen: false,
        editingCampaign: null,
        pendingFiles: [],
        uploadedAttachments: [],
        uploading: false,
      };
    default:
      return state;
  }
}

export function campaignRecipientsReducer(
  state: CampaignRecipientsState,
  action: CampaignRecipientsAction,
): CampaignRecipientsState {
  switch (action.type) {
    case 'open':
      return {
        ...state,
        campaign: action.campaign,
        page: 1,
        statusFilter: '',
        emailSearch: '',
      };
    case 'close':
      return { ...state, campaign: null };
    case 'load-started':
      return { ...state, loading: true };
    case 'load-succeeded':
      return {
        ...state,
        recipients: action.recipients,
        total: action.total,
        statusCounts: action.statusCounts,
        page: action.page,
        loading: false,
      };
    case 'load-finished':
      return { ...state, loading: false };
    case 'filter-changed':
      return { ...state, statusFilter: action.filter, page: 1 };
    case 'page-changed':
      return { ...state, page: action.page };
    case 'search-changed':
      return { ...state, emailSearch: action.search };
    default:
      return state;
  }
}

export function campaignActivityReducer(
  state: CampaignActivityState,
  action: CampaignActivityAction,
): CampaignActivityState {
  switch (action.type) {
    case 'opened':
      return {
        ...state,
        terminals: {
          ...state.terminals,
          [action.campaignId]: {
            logs: [],
            status: { state: 'idle' },
            schedule: null,
          },
        },
      };
    case 'closed': {
      const terminals = { ...state.terminals };
      delete terminals[action.campaignId];
      return { ...state, terminals };
    }
    case 'updated':
      if (!state.terminals[action.campaignId]) return state;
      return {
        ...state,
        terminals: {
          ...state.terminals,
          [action.campaignId]: {
            ...action.data,
            schedule: action.data.schedule ?? state.terminals[action.campaignId].schedule,
          },
        },
      };
    case 'tick':
      return { ...state, tick: state.tick + 1 };
    default:
      return state;
  }
}

export function campaignOperationsReducer(
  state: CampaignOperationsState,
  action: CampaignOperationsAction,
): CampaignOperationsState {
  switch (action.type) {
    case 'stats-opened':
      return { ...state, stats: action.stats };
    case 'stats-closed':
      return { ...state, stats: null };
    case 'recipient-preview-loading':
      return { ...state, recipientPreviewLoading: action.active };
    case 'recipient-preview-opened':
      return { ...state, recipientPreview: action.preview };
    case 'recipient-preview-closed':
      return { ...state, recipientPreview: null };
    case 'starting-all':
      return { ...state, startingAll: action.active };
    case 'pausing-all':
      return { ...state, pausingAll: action.active };
    default:
      return state;
  }
}
