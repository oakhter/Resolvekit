from __future__ import annotations

import csv
import hashlib
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from knowledge_loader.connectors import ConnectorError, PDFConnector

SOURCE_TYPES = {"csv", "xlsx", "pdf"}
SOURCE_AUTHORITIES = {"canonical", "approved", "conditional"}
DOC_TYPES = {"faq", "troubleshooting", "policy", "release_note", "known_issue", "api_reference"}
ESCALATION_RISKS = {"low", "medium", "high"}
REQUIRED_COLUMNS = [
    "source_id",
    "source_title",
    "source_type",
    "source_authority",
    "is_approved",
    "is_active",
    "is_customer_facing_allowed",
    "approved_at",
    "reviewed_by",
    "needs_review_at",
    "doc_type",
    "product_area",
    "issue_class",
    "version_scope",
    "escalation_risk",
    "body",
]


@dataclass(frozen=True)
class SourceValidationError:
    source_uri: str
    row_number: int = 0
    sheet_name: str = ""
    field: str = ""
    message: str = ""


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    source_title: str
    source_type: str
    source_uri: str
    source_authority: str
    is_approved: bool
    is_active: bool
    is_customer_facing_allowed: bool
    approved_at: str
    reviewed_by: str
    needs_review_at: str
    doc_type: str
    product_area: str
    issue_class: str
    version_scope: str
    escalation_risk: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    source_id: str
    source_type: str
    chunk_text: str
    chunk_index: int
    source_metadata: dict[str, Any]
    is_approved: bool
    is_active: bool
    is_customer_facing_allowed: bool


def load_source_records(path: str | Path, *, manifest_path: str | Path | None = None) -> tuple[list[SourceRecord], list[SourceValidationError]]:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix == ".csv":
        return load_csv_records(source_path)
    if suffix == ".xlsx":
        return load_xlsx_records(source_path)
    if suffix == ".pdf":
        if not manifest_path:
            manifest_path = source_path.parent / "pdf_manifest.csv"
        return load_pdf_record(source_path, Path(manifest_path))
    return [], [SourceValidationError(str(source_path), message=f"Unsupported alpha source format: {suffix}")]


def load_csv_records(path: Path) -> tuple[list[SourceRecord], list[SourceValidationError]]:
    records: list[SourceRecord] = []
    errors: list[SourceValidationError] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [col for col in REQUIRED_COLUMNS if col not in (reader.fieldnames or [])]
        if missing:
            return [], [SourceValidationError(str(path), field="header", message=f"Missing required columns: {', '.join(missing)}")]
        for row_number, row in enumerate(reader, 2):
            record, row_errors = _record_from_row(row, source_uri=str(path), row_number=row_number)
            errors.extend(row_errors)
            if record:
                records.append(record)
    return records, errors


def load_xlsx_records(path: Path) -> tuple[list[SourceRecord], list[SourceValidationError]]:
    sheets = _read_xlsx_sheets(path)
    rows = sheets.get("knowledge")
    if not rows:
        return [], [SourceValidationError(str(path), sheet_name="knowledge", message="Missing knowledge sheet")]
    header = [str(value) for value in rows[0]]
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        return [], [SourceValidationError(str(path), sheet_name="knowledge", field="header", message=f"Missing required columns: {', '.join(missing)}")]
    records: list[SourceRecord] = []
    errors: list[SourceValidationError] = []
    for row_number, values in enumerate(rows[1:], 2):
        row = {header[index]: values[index] if index < len(values) else "" for index in range(len(header))}
        record, row_errors = _record_from_row(row, source_uri=str(path), row_number=row_number, sheet_name="knowledge")
        errors.extend(row_errors)
        if record:
            records.append(record)
    return records, errors


def load_pdf_record(path: Path, manifest_path: Path) -> tuple[list[SourceRecord], list[SourceValidationError]]:
    manifest_rows = {row.get("file_name", ""): row for row in _read_csv_rows(manifest_path)}
    row = manifest_rows.get(path.name)
    if not row:
        return [], [SourceValidationError(str(path), message=f"Missing manifest row in {manifest_path.name}")]
    try:
        documents, _preview = PDFConnector().parse(path, source_key="pdf", source_type="pdf", sample_limit=50)
    except ConnectorError as exc:
        return [], [SourceValidationError(str(path), message=f"{exc.code}: {exc}")]
    body = "\n\n".join(document.body for document in documents).strip()
    if not body:
        return [], [SourceValidationError(str(path), message="PDF extraction produced no text")]
    row = {**row, "source_type": "pdf", "body": body}
    record, errors = _record_from_row(row, source_uri=str(path), row_number=2)
    return ([record] if record else []), errors


def chunk_source_records(records: list[SourceRecord], *, chunk_size: int = 180, overlap: int = 30) -> tuple[list[EvidenceChunk], dict[str, Any]]:
    chunks: list[EvidenceChunk] = []
    skipped = 0
    for record in records:
        if not (record.is_active and record.is_approved and record.is_customer_facing_allowed):
            skipped += 1
            continue
        words = record.body.split()
        if not words:
            skipped += 1
            continue
        step = max(1, chunk_size - max(0, overlap))
        for index, start in enumerate(range(0, len(words), step)):
            text = " ".join(words[start:start + chunk_size]).strip()
            if not text:
                continue
            chunks.append(EvidenceChunk(
                chunk_id=_chunk_id(record.source_id, index, text),
                source_id=record.source_id,
                source_type=record.source_type,
                chunk_text=text,
                chunk_index=index,
                source_metadata=asdict(record),
                is_approved=record.is_approved,
                is_active=record.is_active,
                is_customer_facing_allowed=record.is_customer_facing_allowed,
            ))
            if start + chunk_size >= len(words):
                break
    return chunks, {"loaded_records": len(records), "chunked_count": len(chunks), "skipped_inactive_or_empty": skipped}


def source_validation_report(paths: list[str | Path]) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    errors: list[dict[str, Any]] = []
    for raw_path in paths:
        records, record_errors = load_source_records(raw_path)
        chunks, chunk_report = chunk_source_records(records)
        source_type = Path(raw_path).suffix.lower().lstrip(".")
        bucket = counts.setdefault(source_type, {"loaded": 0, "rejected": 0, "chunked": 0})
        bucket["loaded"] += len(records)
        bucket["rejected"] += len(record_errors)
        bucket["chunked"] += len(chunks)
        errors.extend(asdict(error) for error in record_errors)
        if chunk_report["skipped_inactive_or_empty"]:
            bucket["rejected"] += int(chunk_report["skipped_inactive_or_empty"])
    return {"counts_by_format": counts, "validation_errors": errors}


def _record_from_row(
    row: dict[str, Any],
    *,
    source_uri: str,
    row_number: int,
    sheet_name: str = "",
) -> tuple[SourceRecord | None, list[SourceValidationError]]:
    errors: list[SourceValidationError] = []
    cleaned = {key: str(value or "").strip() for key, value in row.items()}
    for field_name in REQUIRED_COLUMNS:
        if not cleaned.get(field_name):
            errors.append(SourceValidationError(source_uri, row_number, sheet_name, field_name, "Required field is missing"))
    for field_name, allowed in {
        "source_type": SOURCE_TYPES,
        "source_authority": SOURCE_AUTHORITIES,
        "doc_type": DOC_TYPES,
        "escalation_risk": ESCALATION_RISKS,
    }.items():
        value = cleaned.get(field_name, "")
        if value and value not in allowed:
            errors.append(SourceValidationError(source_uri, row_number, sheet_name, field_name, f"Invalid value: {value}"))
    booleans = {}
    for field_name in ("is_approved", "is_active", "is_customer_facing_allowed"):
        try:
            booleans[field_name] = _parse_bool(cleaned.get(field_name, ""))
        except ValueError as exc:
            errors.append(SourceValidationError(source_uri, row_number, sheet_name, field_name, str(exc)))
    for field_name in ("approved_at", "needs_review_at"):
        value = cleaned.get(field_name, "")
        if value and not _is_datetime(value):
            errors.append(SourceValidationError(source_uri, row_number, sheet_name, field_name, "Invalid datetime"))
    if booleans.get("is_approved") and (not cleaned.get("approved_at") or not cleaned.get("reviewed_by")):
        errors.append(SourceValidationError(source_uri, row_number, sheet_name, "approved_at/reviewed_by", "Approved sources require approved_at and reviewed_by"))
    if errors:
        return None, errors
    return SourceRecord(
        source_id=cleaned["source_id"],
        source_title=cleaned["source_title"],
        source_type=cleaned["source_type"],
        source_uri=source_uri,
        source_authority=cleaned["source_authority"],
        is_approved=booleans["is_approved"],
        is_active=booleans["is_active"],
        is_customer_facing_allowed=booleans["is_customer_facing_allowed"],
        approved_at=cleaned["approved_at"],
        reviewed_by=cleaned["reviewed_by"],
        needs_review_at=cleaned["needs_review_at"],
        doc_type=cleaned["doc_type"],
        product_area=cleaned["product_area"],
        issue_class=cleaned["issue_class"],
        version_scope=cleaned["version_scope"],
        escalation_risk=cleaned["escalation_risk"],
        body=cleaned["body"],
        metadata={key: value for key, value in cleaned.items() if key not in REQUIRED_COLUMNS},
    ), []


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    raise ValueError("Boolean must be explicit true/false")


def _is_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _chunk_id(source_id: str, index: int, text: str) -> str:
    digest = hashlib.sha256(f"{source_id}:{index}:{text}".encode("utf-8")).hexdigest()[:12]
    return f"{source_id}:{index}:{digest}"


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_xlsx_sheets(path: Path) -> dict[str, list[list[str]]]:
    ns = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with zipfile.ZipFile(path) as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in root.findall(".//x:si", ns):
                shared_strings.append("".join(node.text or "" for node in item.findall(".//x:t", ns)))
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("rel:Relationship", ns)}
        sheets: dict[str, list[list[str]]] = {}
        for sheet in workbook.findall(".//x:sheet", ns):
            name = sheet.attrib.get("name", "Sheet")
            rel_id = sheet.attrib.get(f"{{{ns['r']}}}id", "")
            target = targets.get(rel_id, "")
            if not target:
                continue
            sheet_path = "xl/" + target.lstrip("/")
            root = ET.fromstring(zf.read(sheet_path))
            rows = []
            for row in root.findall(".//x:sheetData/x:row", ns):
                values = []
                for cell in row.findall("x:c", ns):
                    value = cell.find("x:v", ns)
                    if value is None or value.text is None:
                        inline = cell.find(".//x:t", ns)
                        values.append(inline.text if inline is not None and inline.text else "")
                    elif cell.attrib.get("t") == "s":
                        index = int(value.text)
                        values.append(shared_strings[index] if index < len(shared_strings) else "")
                    else:
                        values.append(value.text)
                rows.append(values)
            sheets[name] = rows
    return sheets
