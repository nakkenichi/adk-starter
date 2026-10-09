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

"""Tests for the Cloud Storage export service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.models.exceptions import StorageError
from app.services.storage import EXPORTS_BUCKET_ENV, GCSStorageService


@pytest.mark.parametrize(
    ("bucket", "object_name", "expected_uri"),
    [
        ("exports", "report.xlsx", "gs://exports/report.xlsx"),
        ("exports", "/nested/dir/report.xlsx", "gs://exports/nested/dir/report.xlsx"),
        (" exports ", "  report.pdf  ", "gs://exports/report.pdf"),
    ],
)
def test_build_gcs_uri(bucket: str, object_name: str, expected_uri: str) -> None:
    assert GCSStorageService(bucket_name=bucket).build_gcs_uri(object_name) == expected_uri


@pytest.mark.parametrize(
    ("bucket", "object_name"),
    [("", "report.xlsx"), ("   ", "report.xlsx"), ("exports", ""), ("exports", " / ")],
)
def test_build_gcs_uri_rejects_invalid_input(bucket: str, object_name: str) -> None:
    with pytest.raises(StorageError):
        GCSStorageService(bucket_name=bucket).build_gcs_uri(object_name)


def test_bucket_name_defaults_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXPORTS_BUCKET_ENV, " bucket-from-env ")

    assert GCSStorageService().bucket_name == "bucket-from-env"


def test_bucket_name_is_empty_when_unconfigured() -> None:
    assert GCSStorageService().bucket_name == ""


def test_upload_file_uploads_with_injected_client(tmp_path: Path) -> None:
    local_file = tmp_path / "report.txt"
    local_file.write_text("hello", encoding="utf-8")
    client = MagicMock()

    uri = GCSStorageService(bucket_name="exports", client=client).upload_file(
        local_file, "/runs/report.txt", content_type="text/plain"
    )

    assert uri == "gs://exports/runs/report.txt"
    client.bucket.assert_called_once_with("exports")
    client.bucket.return_value.blob.assert_called_once_with("runs/report.txt")
    client.bucket.return_value.blob.return_value.upload_from_filename.assert_called_once_with(
        str(local_file), content_type="text/plain"
    )


def test_upload_file_creates_client_lazily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_file = tmp_path / "report.txt"
    local_file.write_text("hello", encoding="utf-8")
    client_factory = MagicMock()
    monkeypatch.setattr("app.services.storage.StorageClient", client_factory)
    service = GCSStorageService(bucket_name="exports")

    service.upload_file(local_file, "a.txt")
    service.upload_file(local_file, "b.txt")

    client_factory.assert_called_once_with()


@pytest.mark.parametrize("content", [None, ""])
def test_upload_file_rejects_missing_or_empty_files(tmp_path: Path, content: str | None) -> None:
    local_file = tmp_path / "report.txt"
    if content is not None:
        local_file.write_text(content, encoding="utf-8")
    client = MagicMock()

    with pytest.raises(StorageError):
        GCSStorageService(bucket_name="exports", client=client).upload_file(local_file, "r.txt")

    client.bucket.assert_not_called()
