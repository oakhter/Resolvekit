from __future__ import annotations

from dataclasses import dataclass, field
import csv
from html.parser import HTMLParser
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Protocol


class ConnectorError(RuntimeError):
    def __init__(self, message: str, *, code: str = "connector_error", warnings: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.warnings = warnings or []


@dataclass(frozen=True)
class SourceSection:
    section_id: str
    heading_path: str
    text: str
    page_or_sheet_ref: str = ""
    row_ref: str = ""
    parent_section_id: str = ""


@dataclass(frozen=True)
class SourceDocument:
    source_key: str
    source_type: str
    source_category: str
    source_path: str
    source_url: str
    title: str
    body: str
    sections: list[SourceSection]
    product: str = ""
    platform: str = ""
    role: str = ""
    version_or_date: str = ""
    applies_when: str = ""
    source_license: str = ""
    attribution_required: bool = False
    attribution_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceConnector(Protocol):
    file_types: tuple[str, ...]

    def parse(
        self,
        path: str | Path,
        *,
        source_key: str,
        source_type: str,
        column_mapping: dict[str, str] | None = None,
        sample_limit: int = 25,
    ) -> tuple[list[SourceDocument], dict[str, Any]]:
        ...


def _mapped(row: dict[str, Any], canonical: str, mapping: dict[str, str]) -> str:
    source_col = mapping.get(canonical, canonical)
    value = row.get(source_col, row.get(canonical, ""))
    return "" if value is None else str(value)


def _first(row: dict[str, Any], fields: list[str], mapping: dict[str, str]) -> str:
    for field in fields:
        value = _mapped(row, field, mapping).strip()
        if value:
            return value
    return ""


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "required"}


class CSVConnector:
    file_types = (".csv",)

    def parse(
        self,
        path: str | Path,
        *,
        source_key: str,
        source_type: str,
        column_mapping: dict[str, str] | None = None,
        sample_limit: int = 25,
    ) -> tuple[list[SourceDocument], dict[str, Any]]:
        mapping = column_mapping or {}
        source_path = Path(path)
        with source_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            columns = list(reader.fieldnames or [])
            rows = []
            for idx, row in enumerate(reader, 1):
                if idx > sample_limit:
                    break
                rows.append((idx, dict(row)))

        documents = []
        for row_number, row in rows:
            title = _first(row, ["title", "policy_name", "issue_title"], mapping)
            body = _first(row, ["content", "body", "symptoms", "customer_message"], mapping)
            source_url = _mapped(row, "url", mapping)
            section = SourceSection(
                section_id=f"row-{row_number}",
                heading_path=title,
                text="\n\n".join(part for part in [title, body] if part),
                row_ref=str(row_number),
            )
            documents.append(SourceDocument(
                source_key=source_key,
                source_type=source_type or source_key,
                source_category=source_key,
                source_path=str(source_path),
                source_url=source_url,
                title=title,
                body=body,
                sections=[section] if section.text else [],
                product=_mapped(row, "product", mapping),
                platform=_first(row, ["platform", "affected_platform"], mapping),
                role=_first(row, ["role", "permission"], mapping),
                version_or_date=_first(row, ["version", "release_date", "updated_at"], mapping),
                applies_when=_first(row, ["applies_when", "workaround"], mapping),
                source_license=_mapped(row, "source_license", mapping),
                attribution_required=_as_bool(_mapped(row, "attribution_required", mapping)),
                attribution_text=_mapped(row, "attribution_text", mapping),
                metadata={"row_number": row_number, "raw_row": row, "status": _mapped(row, "status", mapping)},
            ))
        return documents, {"detected_columns": columns, "sample_raw_rows": [row for _, row in rows], "warnings": []}


class _TextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "nav", "footer"}:
            self._skip_depth += 1
        if tag in {"h1", "h2", "h3", "p", "li", "tr"} and not self._skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "nav", "footer"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"h1", "h2", "h3", "p", "li", "tr"} and not self._skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str):
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
        elif not self._skip_depth:
            self.parts.append(text)


class HTMLConnector:
    file_types = (".html", ".htm")

    def parse(
        self,
        path: str | Path,
        *,
        source_key: str,
        source_type: str,
        column_mapping: dict[str, str] | None = None,
        sample_limit: int = 25,
    ) -> tuple[list[SourceDocument], dict[str, Any]]:
        source_path = Path(path)
        parser = _TextHTMLParser()
        parser.feed(source_path.read_text(encoding="utf-8", errors="replace"))
        body = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
        if len(body.split()) < 5:
            raise ConnectorError("HTML extraction produced too little text.", code="low_quality_extraction")
        title = parser.title or source_path.stem.replace("_", " ").title()
        section = SourceSection("html-main", title, body, page_or_sheet_ref=source_path.name)
        document = SourceDocument(
            source_key=source_key,
            source_type=source_type or source_key,
            source_category=source_key,
            source_path=str(source_path),
            source_url=str(source_path),
            title=title,
            body=body,
            sections=[section],
            metadata={"format": "html"},
        )
        return [document], {"detected_columns": [], "sample_raw_rows": [], "warnings": []}


class DOCXConnector:
    file_types = (".docx",)

    def parse(
        self,
        path: str | Path,
        *,
        source_key: str,
        source_type: str,
        column_mapping: dict[str, str] | None = None,
        sample_limit: int = 25,
    ) -> tuple[list[SourceDocument], dict[str, Any]]:
        source_path = Path(path)
        try:
            with zipfile.ZipFile(source_path) as zf:
                xml = zf.read("word/document.xml")
        except Exception as exc:
            raise ConnectorError(f"DOCX extraction failed closed: {exc}", code="docx_extract_failed") from exc
        root = ET.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for para in root.findall(".//w:p", ns):
            text = "".join(node.text or "" for node in para.findall(".//w:t", ns)).strip()
            if text:
                paragraphs.append(text)
        body = "\n\n".join(paragraphs)
        if len(body.split()) < 5:
            raise ConnectorError("DOCX extraction produced too little text.", code="low_quality_extraction")
        title = paragraphs[0] if paragraphs else source_path.stem.replace("_", " ").title()
        section = SourceSection("docx-main", title, body, page_or_sheet_ref=source_path.name)
        return [SourceDocument(source_key, source_type or source_key, source_key, str(source_path), "", title, body, [section], metadata={"format": "docx"})], {"detected_columns": [], "sample_raw_rows": [], "warnings": []}


class XLSXConnector:
    file_types = (".xlsx",)

    def parse(
        self,
        path: str | Path,
        *,
        source_key: str,
        source_type: str,
        column_mapping: dict[str, str] | None = None,
        sample_limit: int = 25,
    ) -> tuple[list[SourceDocument], dict[str, Any]]:
        source_path = Path(path)
        try:
            sheets = _read_xlsx_sheets(source_path, sample_limit)
        except Exception as exc:
            raise ConnectorError(f"XLSX extraction failed closed: {exc}", code="xlsx_extract_failed") from exc
        mapping = column_mapping or {}
        documents: list[SourceDocument] = []
        columns: list[str] = []
        raw_rows: list[dict[str, Any]] = []
        for sheet_name, sheet_rows in sheets.items():
            if not sheet_rows:
                continue
            header = [str(col) for col in sheet_rows[0]]
            columns.extend(header)
            for idx, values in enumerate(sheet_rows[1:sample_limit + 1], 1):
                raw = {header[i]: values[i] if i < len(values) else "" for i in range(len(header))}
                raw_rows.append(raw)
                title = _first(raw, ["title", "policy_name", "issue_title"], mapping) or f"{sheet_name} row {idx + 1}"
                body = _first(raw, ["content", "body", "symptoms", "customer_message"], mapping)
                section = SourceSection(f"{sheet_name}-{idx + 1}", title, "\n\n".join(part for part in [title, body] if part), page_or_sheet_ref=sheet_name, row_ref=str(idx + 1))
                documents.append(SourceDocument(
                    source_key, source_type or source_key, source_key, str(source_path), _mapped(raw, "url", mapping),
                    title, body, [section] if section.text else [],
                    product=_mapped(raw, "product", mapping),
                    platform=_first(raw, ["platform", "affected_platform"], mapping),
                    role=_first(raw, ["role", "permission"], mapping),
                    version_or_date=_first(raw, ["version", "release_date", "updated_at"], mapping),
                    applies_when=_first(raw, ["applies_when", "workaround"], mapping),
                    source_license=_mapped(raw, "source_license", mapping),
                    attribution_required=_as_bool(_mapped(raw, "attribution_required", mapping)),
                    attribution_text=_mapped(raw, "attribution_text", mapping),
                    metadata={"sheet": sheet_name, "row_number": int(idx) + 1, "raw_row": raw, "status": _mapped(raw, "status", mapping)},
                ))
        return documents, {"detected_columns": sorted(set(columns)), "sample_raw_rows": raw_rows[:sample_limit], "warnings": []}


class PDFConnector:
    file_types = (".pdf",)

    def parse(
        self,
        path: str | Path,
        *,
        source_key: str,
        source_type: str,
        column_mapping: dict[str, str] | None = None,
        sample_limit: int = 25,
    ) -> tuple[list[SourceDocument], dict[str, Any]]:
        source_path = Path(path)
        try:
            from pypdf import PdfReader
        except Exception as exc:
            page_texts = [(1, _fallback_pdf_text(source_path))]
            warnings = [f"pypdf unavailable; used limited text fallback: {exc}"]
        else:
            warnings = []
            try:
                reader = PdfReader(str(source_path))
                page_texts = []
                for idx, page in enumerate(reader.pages[:sample_limit], 1):
                    text = (page.extract_text() or "").strip()
                    if text:
                        page_texts.append((idx, text))
            except Exception as exc:
                fallback = _fallback_pdf_text(source_path)
                page_texts = [(1, fallback)] if fallback else []
                warnings.append(f"pypdf extraction failed; used limited text fallback: {exc}")
        body = "\n\n".join(text for _, text in page_texts)
        if len(body.split()) < 5:
            raise ConnectorError("PDF extraction produced too little text.", code="low_quality_extraction")
        title = source_path.stem.replace("_", " ").title()
        sections = [SourceSection(f"page-{idx}", title, text, page_or_sheet_ref=f"page {idx}") for idx, text in page_texts]
        return [SourceDocument(source_key, source_type or source_key, source_key, str(source_path), "", title, body, sections, metadata={"format": "pdf"})], {"detected_columns": [], "sample_raw_rows": [], "warnings": warnings}


CONNECTORS: tuple[SourceConnector, ...] = (
    CSVConnector(),
    HTMLConnector(),
    DOCXConnector(),
    XLSXConnector(),
    PDFConnector(),
)


def get_connector_for_path(path: str | Path) -> SourceConnector:
    suffix = Path(path).suffix.lower()
    for connector in CONNECTORS:
        if suffix in connector.file_types:
            return connector
    raise ConnectorError(f"Unsupported source file extension: {suffix or '(none)'}.", code="unsupported_extension")


def _xlsx_cell_text(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t", "")
    value = cell.find("x:v", ns)
    if value is None or value.text is None:
        inline = cell.find(".//x:t", ns)
        return inline.text if inline is not None and inline.text else ""
    if cell_type == "s":
        idx = int(value.text)
        return shared_strings[idx] if idx < len(shared_strings) else ""
    return value.text


def _read_xlsx_sheets(path: Path, sample_limit: int) -> dict[str, list[list[str]]]:
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
                values = [_xlsx_cell_text(cell, shared_strings, ns) for cell in row.findall("x:c", ns)]
                rows.append(values)
                if len(rows) > sample_limit:
                    break
            sheets[name] = rows
        return sheets


def _fallback_pdf_text(path: Path) -> str:
    raw = path.read_bytes().decode("latin-1", errors="ignore")
    literals = []
    current = []
    inside = False
    escaped = False
    for ch in raw:
        if inside:
            if escaped:
                current.append(ch)
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == ")":
                text = "".join(current).strip()
                if text:
                    literals.append(text)
                current = []
                inside = False
            else:
                current.append(ch)
        elif ch == "(":
            inside = True
    return " ".join(literals)
