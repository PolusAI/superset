# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import csv
import logging
import os
import re
from typing import Any, cast, TypedDict

from flask import current_app as app
from flask_babel import gettext as __

from superset import db, results_backend, results_backend_use_msgpack
from superset.commands.base import BaseCommand
from superset.errors import ErrorLevel, SupersetError, SupersetErrorType
from superset.exceptions import SupersetErrorException, SupersetSecurityException
from superset.models.sql_lab import Query
from superset.sql.parse import SQLScript
from superset.sqllab.limiting_factor import LimitingFactor
from superset.utils import core as utils
from superset.views.utils import _deserialize_results_payload

logger = logging.getLogger(__name__)

# Batch size for streaming mode (number of rows per batch)
STREAMING_BATCH_SIZE = 10000

# In-memory progress tracking for streaming exports
# Format: {client_id: {"processed": int, "total": int, "status": str}}
_progress_store: dict[str, dict[str, Any]] = {}


class SaveToWorkspaceResult(TypedDict):
    status: str
    path: str
    row_count: int


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent path traversal and invalid characters.

    Removes or replaces characters that could be used for path traversal
    or are invalid in filenames.
    """
    # Remove any path separators and parent directory references
    filename = os.path.basename(filename)

    # Remove potentially dangerous characters, keep alphanumeric, dash, underscore, dot
    filename = re.sub(r"[^\w\-.]", "_", filename)

    # Remove leading dots to prevent hidden files
    filename = filename.lstrip(".")

    # Ensure filename is not empty
    if not filename:
        filename = "export"

    # Ensure it ends with .csv
    if not filename.lower().endswith(".csv"):
        filename = filename + ".csv"

    return filename


def sanitize_subfolder(subfolder: str) -> str:
    """
    Sanitize a subfolder path to prevent path traversal.

    Only allows alphanumeric characters, dashes, underscores, and single
    forward slashes (for nested folders).
    """
    # Remove any parent directory references
    subfolder = subfolder.replace("..", "")

    # Remove leading/trailing slashes
    subfolder = subfolder.strip("/")

    # Split by slash, sanitize each part, and rejoin
    parts = subfolder.split("/")
    sanitized_parts = []
    for part in parts:
        # Remove potentially dangerous characters
        part = re.sub(r"[^\w\-]", "_", part)
        part = part.strip("_")
        if part:
            sanitized_parts.append(part)

    return "/".join(sanitized_parts) if sanitized_parts else "sql_exports"


def get_export_progress(client_id: str) -> dict[str, Any] | None:
    """
    Get the current export progress for a given client_id.

    Returns None if no export is in progress for this client_id.
    """
    return _progress_store.get(client_id)


class SqlResultSaveToWorkspaceCommand(BaseCommand):
    """
    Command to save SQL query results directly to the user's workspace storage.

    Supports two modes:
    1. Standard mode: Uses cached results or re-runs query, loads all data into memory
    2. Streaming mode: Re-executes query and writes rows in batches for large datasets
    """

    _client_id: str
    _query: Query
    _filename: str
    _subfolder: str
    _streaming: bool

    def __init__(
        self,
        client_id: str,
        filename: str,
        subfolder: str = "sql_exports",
        streaming: bool = False,
    ) -> None:
        self._client_id = client_id
        self._filename = sanitize_filename(filename)
        self._subfolder = sanitize_subfolder(subfolder)
        self._streaming = streaming

    def validate(self) -> None:
        self._query = (
            db.session.query(Query).filter_by(client_id=self._client_id).one_or_none()
        )
        if self._query is None:
            raise SupersetErrorException(
                SupersetError(
                    message=__(
                        "The query associated with these results could not be found. "
                        "You need to re-run the original query."
                    ),
                    error_type=SupersetErrorType.RESULTS_BACKEND_ERROR,
                    level=ErrorLevel.ERROR,
                ),
                status=404,
            )

        try:
            self._query.raise_for_access()
        except SupersetSecurityException as ex:
            raise SupersetErrorException(
                SupersetError(
                    message=__("Cannot access the query"),
                    error_type=SupersetErrorType.QUERY_SECURITY_ACCESS_ERROR,
                    level=ErrorLevel.ERROR,
                ),
                status=403,
            ) from ex

    def _get_workspace_path(self) -> str:
        """
        Get the full path for saving the file in the user's workspace.

        JupyterHub mounts persistent volume at ~/work
        If subfolder is empty, save directly to ~/work
        """
        work_dir = os.path.expanduser("~/work")
        
        # If subfolder is empty or just whitespace, save directly to work_dir
        if not self._subfolder or not self._subfolder.strip():
            full_dir = work_dir
        else:
            full_dir = os.path.join(work_dir, self._subfolder)

        # Create directory if it doesn't exist
        os.makedirs(full_dir, exist_ok=True)

        return os.path.join(full_dir, self._filename)

    def _get_sql_and_limit(self) -> tuple[str, int | None]:
        """Get the SQL to execute and any limit to apply."""
        if self._query.select_sql:
            return self._query.select_sql, None

        sql = self._query.executed_sql
        script = SQLScript(sql, self._query.database.db_engine_spec.engine)
        limit = script.statements[-1].get_limit_value()

        if limit is not None and self._query.limiting_factor in {
            LimitingFactor.QUERY,
            LimitingFactor.DROPDOWN,
            LimitingFactor.QUERY_AND_DROPDOWN,
        }:
            # remove extra row from `increased_limit`
            limit -= 1

        return sql, limit

    def _save_from_cache(self, filepath: str) -> SaveToWorkspaceResult:
        """
        Save results using cached data or by re-running the query.

        This loads all data into memory, suitable for smaller result sets.
        """
        import pandas as pd

        from superset.utils import csv as csv_utils

        blob = None
        if results_backend and self._query.results_key:
            logger.info(
                "Fetching results from backend [%s]", self._query.results_key
            )
            blob = results_backend.get(self._query.results_key)

        if blob:
            logger.info("Decompressing cached results")
            payload = utils.zlib_decompress(
                blob, decode=not results_backend_use_msgpack
            )
            obj = _deserialize_results_payload(
                payload, self._query, cast(bool, results_backend_use_msgpack)
            )

            df = pd.DataFrame(
                data=obj["data"],
                dtype=object,
                columns=[c["name"] for c in obj["columns"]],
            )
        else:
            logger.info("Running query to get results")
            sql, limit = self._get_sql_and_limit()
            df = self._query.database.get_df(
                sql,
                self._query.catalog,
                self._query.schema,
            )
            if limit is not None:
                df = df[:limit]

        # Write to file
        csv_string = csv_utils.df_to_escaped_csv(
            df, index=False, **app.config["CSV_EXPORT"]
        )
        encoding = app.config["CSV_EXPORT"].get("encoding", "utf-8")

        with open(filepath, "w", encoding=encoding) as f:
            f.write(csv_string)

        return {
            "status": "success",
            "path": filepath,
            "row_count": len(df.index),
        }

    def _stream_to_file(self, filepath: str) -> SaveToWorkspaceResult:
        """
        Stream query results directly to file in batches.

        Re-executes the query and writes rows in batches to minimize
        memory usage for large datasets.
        
        Note: Streaming mode intentionally ignores LIMIT clauses to export
        all available data. Use standard mode if you want to respect limits.
        """
        sql, limit = self._get_sql_and_limit()
        
        # In streaming mode, we want to get ALL data, so strip any LIMIT clause
        # Parse and remove LIMIT from the SQL
        script = SQLScript(sql, self._query.database.db_engine_spec.engine)
        if script.statements:
            # Use the last statement and remove its limit
            last_stmt = script.statements[-1]
            # Get the SQL without limit by reconstructing from the parsed statement
            # If there's a select_sql (user explicitly selected rows), use the base SQL
            if self._query.select_sql:
                sql = self._query.select_sql
            else:
                # For executed SQL, we need to strip the LIMIT clause
                # Simple approach: if we detected a limit, remove it via string manipulation
                if limit is not None:
                    # Remove LIMIT clause (this is a simplified approach)
                    import re
                    sql = re.sub(
                        r'\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?(\s*;?\s*)$',
                        r'\2',
                        sql,
                        flags=re.IGNORECASE
                    )
        
        encoding = app.config["CSV_EXPORT"].get("encoding", "utf-8")
        row_count = 0

        # Initialize progress tracking
        _progress_store[self._client_id] = {
            "processed": 0,
            "total": -1,  # Unknown initially
            "status": "counting",
        }

        # get_sqla_engine is a context manager that yields the engine
        with self._query.database.get_sqla_engine(
            catalog=self._query.catalog,
            schema=self._query.schema,
        ) as engine:
            with engine.connect() as connection:
                # First, get total count for progress tracking
                try:
                    count_sql = f"SELECT COUNT(*) as count FROM ({sql}) as subquery"
                    count_result = connection.execute(count_sql)
                    total_rows = count_result.scalar()
                    _progress_store[self._client_id]["total"] = total_rows
                    _progress_store[self._client_id]["status"] = "exporting"
                    logger.info(f"Total rows to export: {total_rows}")
                except Exception as e:
                    # If count fails, continue without it
                    logger.warning(f"Could not get row count: {e}")
                    _progress_store[self._client_id]["status"] = "exporting"

                # Execute query with streaming/server-side cursor
                result = connection.execution_options(stream_results=True).execute(
                    sql
                )

                with open(filepath, "w", encoding=encoding, newline="") as f:
                    writer: csv.writer | None = None
                    columns: list[str] | None = None

                    while True:
                        # Fetch rows in batches
                        rows = result.fetchmany(STREAMING_BATCH_SIZE)
                        if not rows:
                            break

                        # Write header on first batch
                        if writer is None:
                            columns = list(result.keys())
                            writer = csv.writer(f)
                            writer.writerow(columns)

                        # Write data rows (no limit check in streaming mode)
                        for row in rows:
                            writer.writerow(row)
                            row_count += 1

                        # Update progress
                        _progress_store[self._client_id]["processed"] = row_count

        # Clean up progress on completion
        _progress_store[self._client_id]["status"] = "completed"

        return {
            "status": "success",
            "path": filepath,
            "row_count": row_count,
        }

    def run(self) -> SaveToWorkspaceResult:
        """
        Execute the save to workspace command.

        Uses streaming mode if requested, otherwise uses cached results.
        """
        self.validate()

        filepath = self._get_workspace_path()
        logger.info(
            "Saving query results to workspace: %s (streaming=%s)",
            filepath,
            self._streaming,
        )

        try:
            if self._streaming:
                return self._stream_to_file(filepath)
            else:
                return self._save_from_cache(filepath)
        except Exception as ex:
            logger.exception("Failed to save results to workspace")
            raise SupersetErrorException(
                SupersetError(
                    message=__("Failed to save results to workspace: %(error)s", error=str(ex)),
                    error_type=SupersetErrorType.GENERIC_BACKEND_ERROR,
                    level=ErrorLevel.ERROR,
                ),
                status=500,
            ) from ex


