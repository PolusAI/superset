# Save to Workspace Feature

## Overview

The "Save to Workspace" feature allows users to save SQL query results directly to their persistent workspace storage (`~/work/`) instead of downloading files to their browser. This is useful in JupyterHub environments where users have persistent storage mounted at `~/work/`. The feature supports both standard and streaming modes for large datasets.

## Components

The feature consists of three main components:

1. **Backend Command** - `superset/commands/sql_lab/save_to_workspace.py`
2. **API Endpoints** - `superset/sqllab/api.py`
3. **Frontend Modal** - `superset-frontend/src/SqlLab/components/SaveToWorkspaceModal/index.tsx`

## File Structure

### New Files

- `superset/commands/sql_lab/save_to_workspace.py` - Command class for saving results, sanitization utilities, and progress tracking
- `superset-frontend/src/SqlLab/components/SaveToWorkspaceModal/index.tsx` - React modal component with form handling and progress polling

### Modified Files

- `superset/sqllab/api.py` - Added save_to_workspace and progress endpoints
- `superset/sqllab/schemas.py` - Added SaveToWorkspaceSchema and SaveToWorkspaceResponseSchema
- `superset-frontend/src/SqlLab/components/ResultSet/index.tsx` - Added SaveToWorkspaceModal integration and "Save to Workspace" button

## Usage

After executing a SQL query in SQL Lab, users can click the "Save to Workspace" button to open a modal where they configure the filename and subfolder. The file is then saved to `~/work/[subfolder]/[filename]`, with optional streaming mode for large datasets that provides progress tracking.
