# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Object storage export service for user-facing files produced by the agent."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

from google.cloud.storage import Client as StorageClient

from app.models.exceptions import StorageError

EXPORTS_BUCKET_ENV: Final[str] = "EXPORTS_BUCKET_NAME"

_logger: Final[logging.Logger] = logging.getLogger(__name__)


class GCSStorageService:
    """Uploads exported files to the configured storage bucket."""

    def __init__(
        self,
        *,
        bucket_name: str | None = None,
        client: StorageClient | None = None,
    ) -> None:
        """Initializes the service.

        Args:
            bucket_name: Target bucket. Defaults to the EXPORTS_BUCKET_NAME environment variable.
            client: Storage client to use. Created lazily on first upload when omitted.
        """
        resolved = os.environ.get(EXPORTS_BUCKET_ENV, "") if bucket_name is None else bucket_name
        self._bucket_name: str = resolved.strip()
        self._client: StorageClient | None = client

    @property
    def bucket_name(self) -> str:
        """Returns the configured bucket name (empty when unconfigured)."""
        return self._bucket_name

    def build_gcs_uri(self, object_name: str) -> str:
        """Builds the canonical gs:// URI for an object in the configured bucket.

        Args:
            object_name: Object key within the bucket; leading slashes are ignored.

        Returns:
            The URI in the form 'gs://<bucket>/<object_name>'.

        Raises:
            StorageError: If the bucket is not configured or object_name is empty.
        """
        if not self._bucket_name:
            raise StorageError(f"{EXPORTS_BUCKET_ENV} is not configured.")
        clean_object = _normalize_object_name(object_name)
        return f"gs://{self._bucket_name}/{clean_object}"

    def upload_file(
        self,
        local_path: Path,
        object_name: str,
        *,
        content_type: str | None = None,
    ) -> str:
        """Uploads a local file and returns its gs:// URI.

        Args:
            local_path: Path of the file to upload.
            object_name: Destination object key within the bucket.
            content_type: Optional MIME type stored with the object.

        Returns:
            The gs:// URI of the uploaded object.

        Raises:
            StorageError: If the bucket or object name is invalid, or the file is missing/empty.
        """
        uri = self.build_gcs_uri(object_name)
        clean_object = uri.removeprefix(f"gs://{self._bucket_name}/")
        if not local_path.is_file():
            raise StorageError(f"File to upload does not exist: {local_path}")
        size = local_path.stat().st_size
        if size == 0:
            raise StorageError(f"File to upload is empty: {local_path}")

        try:
            if self._client is None:
                self._client = StorageClient()
            blob = self._client.bucket(self._bucket_name).blob(clean_object)
            blob.upload_from_filename(str(local_path), content_type=content_type)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to upload {local_path.name} to {uri}: {exc}") from exc
        _logger.info("Uploaded %s (%d bytes) to %s", local_path.name, size, uri)
        return uri


def _normalize_object_name(object_name: str) -> str:
    """Strips whitespace and leading slashes from `object_name`, raising `StorageError` if empty."""
    clean = object_name.strip().lstrip("/")
    if not clean:
        raise StorageError("object_name must not be empty.")
    return clean
