import { useCallback, useMemo, useReducer } from 'react';
import type {
  CampaignFormAction,
  CampaignFormController,
  CampaignFormControllerOptions,
  CampaignFormState,
  CampaignTemplateMode,
  TemplateOption,
} from '../../../typefiles';
import {
  extractAllTemplateVariables,
  extractCustomTemplateVariables,
  renderTemplatePreview,
  STANDARD_TEMPLATE_VARIABLES,
} from './campaign-template-utils';

function getTemplateMode(templateIds: number[]): CampaignTemplateMode {
  if (templateIds.length >= 2) return 'rotation';
  if (templateIds.length === 1) return 'single';
  return 'scratch';
}

function createInitialState(options: CampaignFormControllerOptions): CampaignFormState {
  const initialTemplate = options.multiTemplateIds.length > 0
    ? options.templates.find((template) => template.id === options.multiTemplateIds[0])
    : undefined;
  return {
    bodyHtml: initialTemplate?.html_content ?? options.editingCampaign?.body_html ?? '',
    subject: initialTemplate?.subject_line ?? options.editingCampaign?.subject ?? '',
    showRawHtml: false,
    customValues: {},
    perTemplateValues: {},
    splitVariables: new Set<string>(),
    selectedInboxIds: options.editingCampaign?.inbox_ids || [],
  };
}

function formReducer(state: CampaignFormState, action: CampaignFormAction): CampaignFormState {
  switch (action.type) {
    case 'body-updated':
      return { ...state, bodyHtml: action.bodyHtml };
    case 'subject-updated':
      return { ...state, subject: action.subject };
    case 'raw-html-set':
      return { ...state, showRawHtml: action.visible };
    case 'template-content-applied':
      return {
        ...state,
        subject: action.subject,
        bodyHtml: action.bodyHtml,
        customValues: action.resetSingleMode ? {} : state.customValues,
        showRawHtml: action.resetSingleMode ? false : state.showRawHtml,
      };
    case 'custom-value-updated':
      return {
        ...state,
        customValues: { ...state.customValues, [action.name]: action.value },
      };
    case 'template-value-updated':
      return {
        ...state,
        perTemplateValues: {
          ...state.perTemplateValues,
          [action.name]: {
            ...state.perTemplateValues[action.name],
            [action.templateId]: action.value,
          },
        },
      };
    case 'split-variable-toggled': {
      const splitVariables = new Set(state.splitVariables);
      if (splitVariables.has(action.name)) splitVariables.delete(action.name);
      else splitVariables.add(action.name);
      return { ...state, splitVariables };
    }
    case 'inbox-toggled':
      return {
        ...state,
        selectedInboxIds: action.selected
          ? state.selectedInboxIds.includes(action.inboxId)
            ? state.selectedInboxIds
            : [...state.selectedInboxIds, action.inboxId]
          : state.selectedInboxIds.filter((id) => id !== action.inboxId),
      };
    default:
      return state;
  }
}

function isTemplate(template: TemplateOption | undefined): template is TemplateOption {
  return template !== undefined;
}

export function useCampaignForm(options: CampaignFormControllerOptions): CampaignFormController {
  const [values, dispatch] = useReducer(formReducer, options, createInitialState);
  const templateMode = getTemplateMode(options.multiTemplateIds);
  const singleTemplate = templateMode === 'single'
    ? options.templates.find((template) => template.id === options.multiTemplateIds[0]) ?? null
    : null;

  const selectedTemplates = useMemo(
    () => options.multiTemplateIds
      .map((id) => options.templates.find((template) => template.id === id))
      .filter(isTemplate),
    [options.multiTemplateIds, options.templates],
  );

  const combinedRotationContent = useMemo(
    () => selectedTemplates
      .map((template) => `${template.html_content} ${template.subject_line}`)
      .join(' '),
    [selectedTemplates],
  );
  const rotationAllVariables = useMemo(
    () => templateMode === 'rotation'
      ? extractAllTemplateVariables(combinedRotationContent)
      : [],
    [combinedRotationContent, templateMode],
  );
  const rotationCustomVariables = useMemo(
    () => templateMode === 'rotation'
      ? extractCustomTemplateVariables(combinedRotationContent)
      : [],
    [combinedRotationContent, templateMode],
  );
  const rotationStandardVariables = useMemo(
    () => rotationAllVariables.filter((name) => STANDARD_TEMPLATE_VARIABLES.has(name)),
    [rotationAllVariables],
  );

  const templateMap = useMemo(() => {
    const map: Record<string, number[]> = {};
    selectedTemplates.forEach((template) => {
      const variables = extractAllTemplateVariables(
        `${template.html_content} ${template.subject_line}`,
      );
      variables.forEach((name) => {
        if (STANDARD_TEMPLATE_VARIABLES.has(name)) return;
        if (!map[name]) map[name] = [];
        map[name].push(template.id);
      });
    });
    return map;
  }, [selectedTemplates]);

  const rotationPreviewValues = useMemo(() => {
    const previews: Record<number, Record<string, string>> = {};
    selectedTemplates.forEach((template) => {
      const templateValues: Record<string, string> = {};
      Object.entries(values.customValues).forEach(([name, value]) => {
        if (value && !values.splitVariables.has(name)) templateValues[name] = value;
      });
      Object.entries(values.perTemplateValues).forEach(([name, perTemplate]) => {
        if (values.splitVariables.has(name) && perTemplate[template.id]) {
          templateValues[name] = perTemplate[template.id];
        }
      });
      previews[template.id] = templateValues;
    });
    return previews;
  }, [selectedTemplates, values.customValues, values.perTemplateValues, values.splitVariables]);

  const allVariables = useMemo(
    () => extractAllTemplateVariables(`${values.bodyHtml} ${values.subject}`),
    [values.bodyHtml, values.subject],
  );
  const customVariables = useMemo(
    () => extractCustomTemplateVariables(`${values.bodyHtml} ${values.subject}`),
    [values.bodyHtml, values.subject],
  );
  const standardVariables = useMemo(
    () => allVariables.filter((name) => STANDARD_TEMPLATE_VARIABLES.has(name)),
    [allVariables],
  );
  const previewValues = useMemo(
    () => Object.fromEntries(
      Object.entries(values.customValues).filter(([, value]) => Boolean(value)),
    ),
    [values.customValues],
  );

  const toggleTemplate = useCallback((templateId: number) => {
    const nextIds = options.multiTemplateIds.includes(templateId)
      ? options.multiTemplateIds.filter((id) => id !== templateId)
      : [...options.multiTemplateIds, templateId];
    const nextMode = getTemplateMode(nextIds);
    const nextFirstTemplate = options.templates.find((template) => template.id === nextIds[0]);
    const firstTemplateChanged = nextIds[0] !== options.multiTemplateIds[0];

    if (nextFirstTemplate && (nextMode !== templateMode || firstTemplateChanged)) {
      dispatch({
        type: 'template-content-applied',
        subject: nextFirstTemplate.subject_line,
        bodyHtml: nextFirstTemplate.html_content,
        resetSingleMode: nextMode === 'single',
      });
    }
    options.onMultiTemplateToggle(templateId);
  }, [options, templateMode]);

  return {
    values,
    selection: {
      mode: templateMode,
      singleTemplate,
      selectedTemplates,
      templateAttachments: singleTemplate?.attachments || [],
    },
    variables: {
      all: allVariables,
      custom: customVariables,
      standard: standardVariables,
      rotationCustom: rotationCustomVariables,
      rotationStandard: rotationStandardVariables,
      templateMap,
      rotationPreviewValues,
    },
    preview: {
      html: renderTemplatePreview(values.bodyHtml, previewValues),
      subject: renderTemplatePreview(values.subject, previewValues),
      isHtml: values.bodyHtml.includes('<') && values.bodyHtml.includes('>'),
    },
    actions: {
      toggleTemplate,
      setBodyHtml: (bodyHtml) => dispatch({ type: 'body-updated', bodyHtml }),
      setSubject: (subject) => dispatch({ type: 'subject-updated', subject }),
      setRawHtml: (visible) => dispatch({ type: 'raw-html-set', visible }),
      setCustomValue: (name, value) => dispatch({ type: 'custom-value-updated', name, value }),
      setTemplateValue: (name, templateId, value) => dispatch({
        type: 'template-value-updated', name, templateId, value,
      }),
      toggleSplitVariable: (name) => dispatch({ type: 'split-variable-toggled', name }),
      toggleInbox: (inboxId, selected) => dispatch({ type: 'inbox-toggled', inboxId, selected }),
    },
  };
}
