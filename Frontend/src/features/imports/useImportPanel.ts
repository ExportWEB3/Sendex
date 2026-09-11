import { useCallback, useEffect, useReducer, useRef } from 'react';
import { getErrorMessage } from '../../http/api-error';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import type {
  ImportExecuteRequest,
  ImportExecutionResult,
  ImportPanelAction,
  ImportPanelController,
  ImportPanelState,
  ImportPreviewData,
} from '../../../typefiles';

const INITIAL_STATE: ImportPanelState = {
  stage: 'idle',
  file: null,
  preview: null,
  result: null,
  errorMessage: '',
};

function importPanelReducer(
  state: ImportPanelState,
  action: ImportPanelAction,
): ImportPanelState {
  switch (action.type) {
    case 'preview-started':
      return {
        stage: 'previewing',
        file: action.file,
        preview: null,
        result: null,
        errorMessage: '',
      };
    case 'preview-succeeded':
      return { ...state, stage: 'preview', preview: action.preview };
    case 'execution-started':
      return { ...state, stage: 'executing', errorMessage: '' };
    case 'execution-succeeded':
      return { ...state, stage: 'done', result: action.result };
    case 'failed':
      return { ...state, stage: 'error', errorMessage: action.message };
    case 'reset':
      return INITIAL_STATE;
    default:
      return state;
  }
}

export function useImportPanel(fileToImport?: File | null): ImportPanelController {
  const { fetchIt } = useHttpFetcher();
  const [state, dispatch] = useReducer(importPanelReducer, INITIAL_STATE);
  const lastExternalFileRef = useRef<File | null>(null);

  const previewFile = useCallback(async (file: File) => {
    dispatch({ type: 'preview-started', file });
    try {
      const reqData = new FormData();
      reqData.append('file', file);
      const preview = await fetchIt<ImportPreviewData, FormData>({
        apiEndPoint: 'import/preview',
        httpMethod: 'post',
        reqData,
      });
      dispatch({ type: 'preview-succeeded', preview });
    } catch (error) {
      dispatch({ type: 'failed', message: getErrorMessage(error) });
    }
  }, [fetchIt]);

  useEffect(() => {
    if (!fileToImport) {
      lastExternalFileRef.current = null;
      return;
    }
    if (fileToImport === lastExternalFileRef.current) return;

    lastExternalFileRef.current = fileToImport;
    const timeout = window.setTimeout(() => void previewFile(fileToImport), 0);
    return () => window.clearTimeout(timeout);
  }, [fileToImport, previewFile]);

  const execute = useCallback(async () => {
    if (!state.preview?.import_token) {
      dispatch({
        type: 'failed',
        message: 'This import preview has expired. Start over and upload the file again.',
      });
      return;
    }

    dispatch({ type: 'execution-started' });
    try {
      const reqData: ImportExecuteRequest = { import_token: state.preview.import_token };
      const result = await fetchIt<ImportExecutionResult, ImportExecuteRequest>({
        apiEndPoint: 'import/execute',
        httpMethod: 'post',
        reqData,
      });
      dispatch({ type: 'execution-succeeded', result });
    } catch (error) {
      dispatch({ type: 'failed', message: getErrorMessage(error) });
    }
  }, [fetchIt, state.preview]);

  return {
    ...state,
    previewFile,
    execute,
    reset: () => dispatch({ type: 'reset' }),
  };
}
