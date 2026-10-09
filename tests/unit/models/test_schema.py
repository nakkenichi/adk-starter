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

"""Tests for the Pydantic domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schema import MAX_TOPIC_LENGTH, ExampleRequest, ExampleResult


@pytest.mark.parametrize(
    ("raw_topic", "expected_topic"),
    [
        ("quarterly sales", "quarterly sales"),
        ("  padded topic  ", "padded topic"),
        ("x" * MAX_TOPIC_LENGTH, "x" * MAX_TOPIC_LENGTH),
    ],
)
def test_example_request_accepts_valid_topics(raw_topic: str, expected_topic: str) -> None:
    assert ExampleRequest(topic=raw_topic).topic == expected_topic


@pytest.mark.parametrize(
    "raw_topic",
    ["", "   ", "x" * (MAX_TOPIC_LENGTH + 1)],
)
def test_example_request_rejects_invalid_topics(raw_topic: str) -> None:
    with pytest.raises(ValidationError):
        ExampleRequest(topic=raw_topic)


def test_example_request_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ExampleRequest.model_validate({"topic": "sales", "unexpected": True})


def test_example_request_is_frozen() -> None:
    request = ExampleRequest(topic="sales")
    with pytest.raises(ValidationError):
        request.topic = "changed"  # type: ignore[misc]  # frozen model: assignment must fail


@pytest.mark.parametrize(("word_count", "is_valid"), [(1, True), (5, True), (0, False)])
def test_example_result_requires_positive_word_count(word_count: int, is_valid: bool) -> None:
    if is_valid:
        assert ExampleResult(topic="t", summary="s", word_count=word_count).word_count == word_count
    else:
        with pytest.raises(ValidationError):
            ExampleResult(topic="t", summary="s", word_count=word_count)
