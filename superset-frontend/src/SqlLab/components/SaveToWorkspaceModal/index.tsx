/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

import { useState, useCallback, useEffect, ChangeEvent } from 'react';
import {
  Button,
  Modal,
  Input,
  Checkbox,
  Icons,
} from '@superset-ui/core/components';
import ProgressBar from '@superset-ui/core/components/ProgressBar';
import { t, SupersetClient, getClientErrorObject } from '@superset-ui/core';
import { styled } from '@apache-superset/core/ui';
import { extendedDayjs as dayjs } from '@superset-ui/core/utils/dates';
import { addSuccessToast, addDangerToast } from 'src/components/MessageToasts/actions';
import { useDispatch } from 'react-redux';

export interface SaveToWorkspaceModalProps {
  visible: boolean;
  onHide: () => void;
  queryId: string;
  queryName?: string;
  queryResultRows?: number;
}

interface SaveToWorkspaceResponse {
  status: string;
  path: string;
  row_count: number;
}

const StyledModal = styled(Modal)`
  ${({ theme }) => `
    .stw-body {
      padding: ${theme.sizeUnit * 4}px;
    }

    .stw-field {
      margin-bottom: ${theme.sizeUnit * 4}px;
    }

    .stw-label {
      display: block;
      margin-bottom: ${theme.sizeUnit * 2}px;
      font-weight: ${theme.fontWeightNormal};
      color: ${theme.colorText};
    }

    .stw-help {
      margin-top: ${theme.sizeUnit}px;
      font-size: ${theme.fontSizeSM}px;
      color: ${theme.colorTextSecondary};
    }

    .stw-preview {
      margin-top: ${theme.sizeUnit * 4}px;
      padding: ${theme.sizeUnit * 3}px;
      background: ${theme.colorBgContainerDisabled};
      border-radius: ${theme.borderRadius}px;
    }

    .stw-preview-label {
      font-size: ${theme.fontSizeSM}px;
      color: ${theme.colorTextSecondary};
      margin-bottom: ${theme.sizeUnit}px;
    }

    .stw-preview-path {
      font-family: ${theme.fontFamilyCode};
      font-size: ${theme.fontSizeSM}px;
      color: ${theme.colorText};
      word-break: break-all;
    }

    .stw-streaming {
      margin-top: ${theme.sizeUnit * 4}px;
    }

    .stw-progress {
      margin-top: ${theme.sizeUnit * 4}px;
      padding: ${theme.sizeUnit * 3}px;
      background: ${theme.colorBgContainerDisabled};
      border-radius: ${theme.borderRadius}px;
    }

    .stw-progress-label {
      font-size: ${theme.fontSizeSM}px;
      color: ${theme.colorTextSecondary};
      margin-bottom: ${theme.sizeUnit * 2}px;
      text-align: center;
    }
  `}
`;

const generateDefaultFilename = (): string => {
  const timestamp = dayjs().format('YYYYMMDD_HHmmss');
  return `${timestamp}.csv`;
};

export const SaveToWorkspaceModal = ({
  visible,
  onHide,
  queryId,
  queryName,
  queryResultRows,
}: SaveToWorkspaceModalProps) => {
  const dispatch = useDispatch();
  const [filename, setFilename] = useState('');
  const [subfolder, setSubfolder] = useState('sql_exports');
  const [streaming, setStreaming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savingMessage, setSavingMessage] = useState('');
  const [progressPercent, setProgressPercent] = useState(0);

  // Auto-enable streaming for large result sets
  const STREAMING_AUTO_ENABLE_THRESHOLD = 1000;

  // Reset form when modal opens
  useEffect(() => {
    if (visible) {
      setFilename(generateDefaultFilename());
      setSubfolder('sql_exports');
      
      // Auto-enable streaming if query returned 1000+ rows (likely hit display limit)
      const shouldAutoEnableStreaming = 
        queryResultRows !== undefined && queryResultRows >= STREAMING_AUTO_ENABLE_THRESHOLD;
      setStreaming(shouldAutoEnableStreaming);
      
      setSaving(false);
      setProgressPercent(0);
    }
  }, [visible, queryResultRows]);

  // Poll for progress when streaming and saving
  useEffect(() => {
    let pollInterval: NodeJS.Timeout | null = null;

    const pollProgress = async () => {
      try {
        const response = await SupersetClient.get({
          endpoint: `/api/v1/sqllab/save_to_workspace_progress/${queryId}/`,
        });
        const progress = response.json as {
          processed: number;
          total: number;
          status: string;
        };

        if (progress.total > 0) {
          const percent = Math.floor((progress.processed / progress.total) * 100);
          setProgressPercent(percent);
          
          // Update message with actual numbers
          setSavingMessage(
            t('Exporting: %(processed)s / %(total)s rows (%(percent)s%%)', {
              processed: progress.processed.toLocaleString(),
              total: progress.total.toLocaleString(),
              percent,
            }),
          );
        } else if (progress.status === 'counting') {
          setSavingMessage(t('Counting total rows...'));
        }
      } catch (error) {
        // Progress endpoint might return 404 if export finished or hasn't started
        // This is expected, so we silently ignore
      }
    };

    if (saving && streaming) {
      // Start polling immediately
      pollProgress();
      // Then poll every second
      pollInterval = setInterval(pollProgress, 1000);
    }

    return () => {
      if (pollInterval) {
        clearInterval(pollInterval);
      }
    };
  }, [saving, streaming, queryId, dispatch]);

  const getPreviewPath = useCallback(() => {
    const sanitizedSubfolder = subfolder.trim().replace(/\.\./g, '').replace(/^\/+|\/+$/g, '');
    const sanitizedFilename = filename.replace(/\.\./g, '').replace(/^\/+/, '');
    
    // If subfolder is empty, save directly to ~/work/
    if (!sanitizedSubfolder) {
      return `~/work/${sanitizedFilename}`;
    }
    return `~/work/${sanitizedSubfolder}/${sanitizedFilename}`;
  }, [subfolder, filename]);

  const handleSave = useCallback(async () => {
    if (!filename.trim()) {
      dispatch(addDangerToast(t('Please enter a filename')));
      return;
    }

    setSaving(true);
    setProgressPercent(0);
    
    // Show appropriate message based on mode
    if (streaming) {
      setSavingMessage(t('Preparing to export...'));
    } else {
      setSavingMessage(t('Saving results to workspace...'));
    }

    try {
      // Don't use ensureAppRoot here - SupersetClient already handles appRoot internally
      const response = await SupersetClient.post({
        endpoint: `/api/v1/sqllab/save_to_workspace/${queryId}/`,
        jsonPayload: {
          filename: filename.trim(),
          subfolder: subfolder.trim() || 'sql_exports',
          streaming,
        },
      });

      const result = response.json as SaveToWorkspaceResponse;
      dispatch(
        addSuccessToast(
          t('Successfully saved %(rows)s rows to %(path)s', {
            rows: result.row_count.toLocaleString(),
            path: result.path,
          }),
        ),
      );
      onHide();
    } catch (error) {
      const clientError = await getClientErrorObject(error);
      dispatch(
        addDangerToast(
          clientError.message || t('Failed to save to workspace'),
        ),
      );
    } finally {
      setSaving(false);
      setSavingMessage('');
    }
  }, [dispatch, filename, onHide, queryId, streaming, subfolder]);

  const handleFilenameChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      setFilename(e.target.value);
    },
    [],
  );

  const handleSubfolderChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      setSubfolder(e.target.value);
    },
    [],
  );

  const handleStreamingChange = useCallback(
    (e: { target: { checked: boolean } }) => {
      setStreaming(e.target.checked);
    },
    [],
  );

  return (
    <StyledModal
      show={visible}
      onHide={onHide}
      title={t('Save to Workspace')}
      footer={
        <>
          <Button onClick={onHide} disabled={saving}>
            {t('Cancel')}
          </Button>
          <Button
            buttonStyle="primary"
            onClick={handleSave}
            disabled={saving || !filename.trim()}
            loading={saving}
          >
            <Icons.SaveOutlined iconSize="s" />
            {saving ? t('Saving...') : t('Save')}
          </Button>
        </>
      }
    >
      <div className="stw-body">
        <div className="stw-field">
          <label className="stw-label" htmlFor="stw-filename">
            {t('Filename')}
          </label>
          <Input
            id="stw-filename"
            value={filename}
            onChange={handleFilenameChange}
            placeholder={t('Enter filename')}
            disabled={saving}
          />
          <div className="stw-help">
            {t('The file will be saved as a CSV file.')}
          </div>
        </div>

        <div className="stw-field">
          <label className="stw-label" htmlFor="stw-subfolder">
            {t('Subfolder')}
          </label>
          <Input
            id="stw-subfolder"
            value={subfolder}
            onChange={handleSubfolderChange}
            placeholder={t('sql_exports')}
            disabled={saving}
          />
          <div className="stw-help">
            {t('Folder within ~/work/ where the file will be saved. If empty, the file will be saved directly to ~/work/')}
          </div>
        </div>

        <div className="stw-preview">
          <div className="stw-preview-label">{t('File will be saved to:')}</div>
          <div className="stw-preview-path">{getPreviewPath()}</div>
        </div>

        <div className="stw-streaming">
          <Checkbox
            checked={streaming}
            onChange={handleStreamingChange}
            disabled={saving}
          >
            {t('Use streaming mode for large datasets')}
          </Checkbox>
          <div className="stw-help">
            {t('Streaming mode will export all data using batches without row limits.')}
          </div>
        </div>

        {saving && (
          <div className="stw-progress">
            <div className="stw-progress-label">{savingMessage}</div>
            <ProgressBar 
              percent={streaming ? progressPercent : 100} 
              showInfo={false}
            />
          </div>
        )}
      </div>
    </StyledModal>
  );
};

export default SaveToWorkspaceModal;


