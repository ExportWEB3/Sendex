import { useCallback, useReducer } from 'react';
import { getErrorMessage } from '../../http/api-error';
import { useHttpFetcher } from '../../hooks/useHttpFetcher';
import type {
  MultiFileImportAction,
  MultiFileImportController,
  MultiFileImportState,
} from '../../../typefiles';

function createInitialState<TResult>(): MultiFileImportState<TResult> {
  return {
    stage: 'idle',
    results: [],
    errorMessage: '',
  };
}

function multiFileImportReducer<TResult>(
  state: MultiFileImportState<TResult>,
  action: MultiFileImportAction<TResult>,
): MultiFileImportState<TResult> {
  switch (action.type) {
    case 'started':
      return { ...state, stage: 'importing', errorMessage: '' };
    case 'succeeded':
      return { stage: 'done', results: action.results, errorMessage: '' };
    case 'failed':
      return { ...state, stage: 'error', errorMessage: action.message };
    default:
      return state;
  }
}

export function useMultiFileImport<TResult>(
  endpoint: string,
  files: File[],
): MultiFileImportController<TResult> {
  const { fetchIt } = useHttpFetcher();
  const [state, dispatch] = useReducer(
    multiFileImportReducer<TResult>,
    undefined,
    createInitialState<TResult>,
  );

  const run = useCallback(async () => {
    dispatch({ type: 'started' });
    try {
      const reqData = new FormData();
      files.forEach((file) => reqData.append('files', file));
      const response = await fetchIt<{ results: TResult[] }, FormData>({
        apiEndPoint: endpoint,
        httpMethod: 'post',
        reqData,
      });
      dispatch({ type: 'succeeded', results: response.results || [] });
    } catch (error) {
      dispatch({ type: 'failed', message: getErrorMessage(error) });
    }
  }, [endpoint, fetchIt, files]);

  return { ...state, run };
}
