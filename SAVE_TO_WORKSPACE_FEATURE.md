# Save to Workspace Feature

## Overview

The "Save to Workspace" feature allows users to save SQL query results directly to their persistent workspace storage (`~/work/`) instead of downloading files to their browser. This is particularly useful in JupyterHub environments where users have persistent storage mounted at `~/work/`.

This feature was implemented in commit `40e0df61e` and includes both API and frontend changes to support saving query results with optional streaming mode for large datasets.

## Architecture

### Components

The feature consists of three main components:

1. **Backend Command** (`superset/commands/sql_lab/save_to_workspace.py`)
   - Handles the core logic for saving query results to workspace storage
   - Supports both standard and streaming modes

2. **API Endpoints** (`superset/sqllab/api.py`)
   - REST API endpoints for saving results and tracking progress
   - Schema validation for request/response data

3. **Frontend Modal** (`superset-frontend/src/SqlLab/components/SaveToWorkspaceModal/index.tsx`)
   - User interface for configuring and initiating the save operation
   - Progress tracking for streaming exports

## API Changes

### New Endpoints

#### 1. Save to Workspace Endpoint

**Endpoint:** `POST /api/v1/sqllab/save_to_workspace/<client_id>/`

**Description:** Saves SQL query results to the user's workspace storage.

**Request Body:**
```json
{
  "filename": "export_20251208_154144.csv",
  "subfolder": "sql_exports",
  "streaming": false
}
```

**Parameters:**
- `filename` (required): The filename for the CSV file (will be sanitized)
- `subfolder` (optional, default: "sql_exports"): Subfolder within `~/work/` to save the file
- `streaming` (optional, default: false): Enable streaming mode for large datasets

**Response:**
```json
{
  "status": "success",
  "path": "/home/user/work/sql_exports/export_20251208_154144.csv",
  "row_count": 1234
}
```

**Permissions:** Requires `export_csv` permission

#### 2. Progress Tracking Endpoint

**Endpoint:** `GET /api/v1/sqllab/save_to_workspace_progress/<client_id>/`

**Description:** Polls the progress of an ongoing streaming export operation.

**Response:**
```json
{
  "processed": 50000,
  "total": 100000,
  "status": "exporting"
}
```

**Status Values:**
- `counting`: Counting total rows before export
- `exporting`: Export in progress
- `completed`: Export finished

**Permissions:** Requires `export_csv` permission

### Schema Definitions

#### SaveToWorkspaceSchema

Located in `superset/sqllab/schemas.py`:

```python
class SaveToWorkspaceSchema(Schema):
    filename = fields.String(required=True)
    subfolder = fields.String(load_default="sql_exports")
    streaming = fields.Boolean(load_default=False)
```

#### SaveToWorkspaceResponseSchema

```python
class SaveToWorkspaceResponseSchema(Schema):
    status = fields.String()
    path = fields.String()
    row_count = fields.Integer()
```

## Backend Implementation

### Command: SqlResultSaveToWorkspaceCommand

The command class (`superset/commands/sql_lab/save_to_workspace.py`) implements the core save functionality.

#### Key Methods

1. **`validate()`**: Validates that the query exists and the user has access
2. **`_get_workspace_path()`**: Constructs the full file path in `~/work/`
3. **`_save_from_cache()`**: Standard mode - uses cached results or re-runs query
4. **`_stream_to_file()`**: Streaming mode - re-executes query and writes in batches

#### Two Operation Modes

##### Standard Mode
- Uses cached query results if available
- Falls back to re-running the query if cache is unavailable
- Loads all data into memory (suitable for smaller result sets)
- Respects LIMIT clauses from the original query

##### Streaming Mode
- Re-executes the query with streaming cursor
- Writes rows in batches (10,000 rows per batch)
- Minimizes memory usage for large datasets
- **Intentionally ignores LIMIT clauses** to export all available data
- Provides progress tracking via in-memory store

#### Security Features

1. **Filename Sanitization** (`sanitize_filename()`):
   - Removes path separators and parent directory references (`..`)
   - Removes invalid characters, keeping only alphanumeric, dash, underscore, dot
   - Ensures filename ends with `.csv`
   - Prevents hidden files (removes leading dots)

2. **Subfolder Sanitization** (`sanitize_subfolder()`):
   - Removes parent directory references
   - Sanitizes each path component
   - Allows nested folders (single forward slashes)

3. **Path Construction**:
   - All files are saved under `~/work/` (JupyterHub persistent volume)
   - Directories are created automatically if they don't exist
   - Path traversal attacks are prevented through sanitization

#### Progress Tracking

Streaming mode uses an in-memory progress store:
```python
_progress_store: dict[str, dict[str, Any]] = {}
```

Format: `{client_id: {"processed": int, "total": int, "status": str}}`

The progress is updated during batch processing and can be polled via the progress endpoint.

## Frontend Implementation

### SaveToWorkspaceModal Component

The modal component (`superset-frontend/src/SqlLab/components/SaveToWorkspaceModal/index.tsx`) provides the user interface.

#### Features

1. **Form Fields:**
   - Filename input (auto-generated with timestamp if empty)
   - Subfolder input (defaults to "sql_exports")
   - Streaming mode checkbox

2. **Path Preview:**
   - Shows the full path where the file will be saved
   - Updates in real-time as user types

3. **Auto-Configuration:**
   - Auto-enables streaming mode if query returned 1000+ rows
   - Generates default filename with timestamp format: `YYYYMMDD_HHmmss.csv`

4. **Progress Tracking:**
   - For streaming mode: polls progress endpoint every second
   - Displays progress bar with percentage and row counts
   - Shows status messages ("Counting total rows...", "Exporting: X / Y rows")

5. **Error Handling:**
   - Displays error toasts on failure
   - Shows success toast with row count and path on completion

#### Integration with ResultSet

The modal is integrated into the SQL Lab ResultSet component:

```typescript
<SaveToWorkspaceModal
  visible={showSaveToWorkspaceModal}
  onHide={() => setShowSaveToWorkspaceModal(false)}
  queryId={query.id}
  queryName={query?.tab ?? undefined}
  queryResultRows={query.rows}
/>
```

A "Save to Workspace" button is added to the ResultSet action buttons, visible when:
- CSV export is available (`csv` prop is true)
- User has export permissions (`canExportData` is true)

## File Structure

### New Files

1. `superset/commands/sql_lab/save_to_workspace.py` (401 lines)
   - Command class for saving results
   - Sanitization utilities
   - Progress tracking

2. `superset-frontend/src/SqlLab/components/SaveToWorkspaceModal/index.tsx` (372 lines)
   - React modal component
   - Form handling and validation
   - Progress polling logic

### Modified Files

1. `superset/sqllab/api.py`
   - Added `save_to_workspace()` endpoint
   - Added `get_save_to_workspace_progress()` endpoint
   - Imported command and schemas

2. `superset/sqllab/schemas.py`
   - Added `SaveToWorkspaceSchema`
   - Added `SaveToWorkspaceResponseSchema`

3. `superset-frontend/src/SqlLab/components/ResultSet/index.tsx`
   - Added SaveToWorkspaceModal import and usage
   - Added "Save to Workspace" button
   - Added state management for modal visibility

## Usage Flow

1. User executes a SQL query in SQL Lab
2. Results are displayed in the ResultSet component
3. User clicks "Save to Workspace" button
4. Modal opens with:
   - Pre-filled filename (timestamp-based)
   - Default subfolder ("sql_exports")
   - Streaming mode auto-enabled if result set is large (1000+ rows)
5. User can modify filename and subfolder, toggle streaming mode
6. Path preview shows where file will be saved
7. User clicks "Save"
8. Backend processes the request:
   - Standard mode: Uses cache or re-runs query, writes CSV
   - Streaming mode: Re-executes query, writes in batches with progress updates
9. Frontend polls progress (streaming mode only)
10. Success toast displays with row count and file path
11. File is available in user's workspace at `~/work/[subfolder]/[filename]`

## Configuration

### Default Settings

- **Default subfolder:** `sql_exports`
- **Streaming batch size:** 10,000 rows
- **Auto-enable streaming threshold:** 1,000 rows
- **Progress poll interval:** 1 second
- **File encoding:** UTF-8 (from `CSV_EXPORT` config)

### CSV Export Configuration

The feature uses Superset's existing CSV export configuration:
- Encoding: `app.config["CSV_EXPORT"].get("encoding", "utf-8")`
- Other CSV options: `app.config["CSV_EXPORT"]`

## Security Considerations

1. **Path Traversal Prevention:**
   - Filenames and subfolders are sanitized to prevent `../` attacks
   - Only safe characters are allowed
   - All files are restricted to `~/work/` directory

2. **Access Control:**
   - Requires `export_csv` permission
   - Validates user access to the query via `query.raise_for_access()`

3. **Input Validation:**
   - Schema validation on API requests
   - Frontend validation for required fields

## Error Handling

### Backend Errors

- **404:** Query not found - user needs to re-run the query
- **403:** Access denied - user doesn't have permission
- **500:** General error during save operation

### Frontend Errors

- Displays error toasts with user-friendly messages
- Handles network errors gracefully
- Progress polling errors are silently ignored (expected when export completes)

## Performance Considerations

### Standard Mode
- Suitable for result sets that fit in memory
- Uses cached results when available (faster)
- Falls back to query re-execution if cache unavailable

### Streaming Mode
- Designed for large datasets (millions of rows)
- Processes data in batches to minimize memory usage
- Progress tracking adds minimal overhead
- Re-executes query (cache is not used)

## Future Enhancements

Potential improvements:
- Support for other file formats (JSON, Parquet, etc.)
- Configurable batch sizes
- Background job processing for very large exports
- Export history/management UI
- Support for saving to other storage backends (S3, etc.)

## Testing

The feature should be tested for:
- Standard mode with cached results
- Standard mode with query re-execution
- Streaming mode with large datasets
- Progress tracking accuracy
- Security (path traversal attempts)
- Error handling (missing queries, permission errors)
- UI responsiveness during long-running exports

