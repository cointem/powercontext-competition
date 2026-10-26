# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Lossless, primary-key-addressed CSV evidence."""

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TextEvidence


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    if not document_id or document_id != document_id.strip() or not primary_key_value.strip():
        raise ValueError("Invalid CSV identity")  # noqa: TRY003
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > 256:
        raise ValueError("CSV identity exceeds 256 characters")  # noqa: TRY003
    return identity


class CSVDocumentInput(BaseModel):
    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row(self):
        csv_row_source_id(self.document_id, self.primary_key_value)
        _validate_header(self.columns, self.primary_key)
        if len(self.columns) != len(self.values):
            raise ValueError("CSV row width differs from header")  # noqa: TRY003
        if self.values[self.columns.index(self.primary_key)] != self.primary_key_value:
            raise ValueError("CSV primary key differs from row value")  # noqa: TRY003
        return self


def _validate_header(columns: tuple[str, ...], primary_key: str) -> None:
    if not columns or any(not column.strip() for column in columns) or len(set(columns)) != len(columns):
        raise ValueError("CSV header must contain unique nonblank columns")  # noqa: TRY003
    if not primary_key or primary_key not in columns:
        raise ValueError("CSV primary key must match a column")  # noqa: TRY003


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    if not value.document_id or value.document_id != value.document_id.strip():
        raise ValueError("CSV document ID must be nonempty and trimmed")  # noqa: TRY003
    try:
        reader = csv.reader(io.StringIO(value.content.decode("utf-8-sig"), newline=""), strict=True)
        columns = tuple(next(reader, ()))
        _validate_header(columns, value.primary_key)
        key_index = columns.index(value.primary_key)
        rows = []
        seen = set()
        for record in reader:
            if not record:
                continue
            if len(record) != len(columns):
                raise ValueError("CSV row width differs from header")  # noqa: TRY003
            key = record[key_index]
            if key in seen:
                raise ValueError("Duplicate CSV primary key")  # noqa: TRY003
            seen.add(key)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=key,
                    columns=columns,
                    values=tuple(record),
                )
            )
        return tuple(rows)
    except (UnicodeError, csv.Error) as error:
        raise ValueError("Invalid CSV document") from error  # noqa: TRY003


class CSVRowSource(Source, CSVRowInput):
    source_id: str


class _CSVRowProjection:
    name = "powercontext.text-evidence"
    version = "1"
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class _CSVRowDefinition:
    name = "csv-row"
    version = "1"
    input_class = CSVRowInput
    source_class = CSVRowSource
    projections = (_CSVRowProjection(),)

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            **value.model_dump(), source_id=identity, name=identity, materialization=SourceMaterialization.CAPTURED
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput.model_validate(source.model_dump())


CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(_CSVRowDefinition(), projections=(_CSVRowProjection(),))
