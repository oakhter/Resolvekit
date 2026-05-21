from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = ROOT / "demo_data" / "csv"
XLSX_DIR = ROOT / "demo_data" / "xlsx"
PDF_DIR = ROOT / "demo_data" / "pdf"

HEADER = [
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


def rows(prefix: str, source_type: str, count: int) -> list[dict[str, str]]:
    topics = [
        ("password reset", "login", "faq", "low"),
        ("mobile 403 session refresh", "login", "troubleshooting", "medium"),
        ("billing export permissions", "billing", "policy", "medium"),
        ("API rate limit headers", "api", "api_reference", "low"),
        ("webhook retry window", "integrations", "troubleshooting", "medium"),
        ("stale SSO mapping", "access", "known_issue", "high"),
        ("release 2.4 routing change", "routing", "release_note", "low"),
        ("workspace owner transfer", "permissions", "policy", "medium"),
        ("offline mobile queue", "mobile", "troubleshooting", "medium"),
        ("data retention request", "compliance", "policy", "high"),
    ]
    out = []
    for index in range(count):
        topic, issue_class, doc_type, risk = topics[index % len(topics)]
        source_id = f"{source_type}_{prefix}_{index + 1:03d}"
        out.append({
            "source_id": source_id,
            "source_title": f"Loopline {topic.title()}",
            "source_type": source_type,
            "source_authority": "canonical" if index % 3 == 0 else "approved",
            "is_approved": "true",
            "is_active": "true",
            "is_customer_facing_allowed": "true" if index % 7 else "false",
            "approved_at": "2026-05-01T00:00:00+00:00",
            "reviewed_by": "resolvekit-demo",
            "needs_review_at": "2026-11-01T00:00:00+00:00",
            "doc_type": doc_type,
            "product_area": issue_class,
            "issue_class": issue_class,
            "version_scope": "Loopline 2.x",
            "escalation_risk": risk,
            "body": (
                f"Loopline guidance for {topic}. Use this customer-facing source when the question is about {issue_class}. "
                f"Give concise steps, cite this source, and escalate only when the customer reports missing permissions, data loss, or repeated failure. "
                f"Record product version, workspace ID, and timestamp before escalation."
            ),
        })
    return out


def write_csv(path: Path, data: list[dict[str, str]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or HEADER)
        writer.writeheader()
        writer.writerows(data)


def cell(ref: str, value: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'


def write_xlsx(path: Path, data: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheets = {
        "knowledge": [HEADER] + [[row[col] for col in HEADER] for row in data],
        "metadata_guide": [["column", "description"]] + [[col, f"Required SourceRecord field {col}"] for col in HEADER],
        "demo_cases": [["case_id", "question", "expected_behavior"], ["xlsx_green_001", "How does Loopline webhook retry work?", "green"]],
    }
    workbook_sheets = "\n".join(
        f'<sheet name="{name}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheets, 1)
    )
    rels = "\n".join(
        f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        for idx in range(1, len(sheets) + 1)
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
</Types>""")
        zf.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""")
        zf.writestr("xl/workbook.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{workbook_sheets}</sheets></workbook>""")
        zf.writestr("xl/_rels/workbook.xml.rels", f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>""")
        for sheet_index, values in enumerate(sheets.values(), 1):
            body = []
            for row_index, row_values in enumerate(values, 1):
                cells = "".join(cell(f"{chr(65 + col_index)}{row_index}", value) for col_index, value in enumerate(row_values))
                body.append(f'<row r="{row_index}">{cells}</row>')
            zf.writestr(f"xl/worksheets/sheet{sheet_index}.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{''.join(body)}</sheetData></worksheet>""")


def write_pdf(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"{title}\\n\\n{body}".replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        "3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >> endobj\n",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj\n",
    ]
    content = "%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(content.encode("latin-1")))
        content += obj
    xref_at = len(content.encode("latin-1"))
    content += "xref\n0 6\n0000000000 65535 f \n"
    content += "".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:])
    content += f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    path.write_bytes(content.encode("latin-1"))


def main() -> int:
    csv_rows = rows("demo", "csv", 24)
    xlsx_rows = rows("demo", "xlsx", 24)
    pdf_rows = rows("demo", "pdf", 10)
    write_csv(CSV_DIR / "resolvekit_demo_kb.csv", csv_rows)
    write_xlsx(XLSX_DIR / "resolvekit_demo_kb.xlsx", xlsx_rows)
    manifest_rows = []
    for item in pdf_rows:
        file_name = f"{item['source_id']}.pdf"
        write_pdf(PDF_DIR / file_name, item["source_title"], item["body"])
        manifest_rows.append({"file_name": file_name, **{key: item[key] for key in HEADER if key != "body"}})
    write_csv(PDF_DIR / "pdf_manifest.csv", manifest_rows, fieldnames=["file_name", *[key for key in HEADER if key != "body"]])
    cases = [
        {"case_id": "green_csv_001", "question": "How do I reset a Loopline workspace password?", "expected_behavior": "green", "expected_sources": ["csv_demo_001"], "expected_issue_class": "login", "notes": "CSV-grounded FAQ."},
        {"case_id": "green_csv_002", "question": "What should an agent do for Loopline mobile offline queue?", "expected_behavior": "green", "expected_sources": ["csv_demo_009"], "expected_issue_class": "mobile", "notes": "CSV troubleshooting."},
        {"case_id": "green_xlsx_001", "question": "How does Loopline retry failed webhooks?", "expected_behavior": "green", "expected_sources": ["xlsx_demo_005"], "expected_issue_class": "integrations", "notes": "XLSX-grounded troubleshooting."},
        {"case_id": "green_xlsx_002", "question": "Who can transfer a Loopline workspace owner?", "expected_behavior": "green", "expected_sources": ["xlsx_demo_008"], "expected_issue_class": "permissions", "notes": "XLSX support-card policy row."},
        {"case_id": "green_pdf_001", "question": "What are Loopline API rate limit headers?", "expected_behavior": "green", "expected_sources": ["pdf_demo_004"], "expected_issue_class": "api", "notes": "PDF-grounded API reference."},
        {"case_id": "yellow_csv_001", "question": "A customer sees mobile 403 after update but web still works.", "expected_behavior": "yellow", "expected_sources": ["csv_demo_002"], "expected_issue_class": "login", "notes": "Caveat: needs app version and OS."},
        {"case_id": "yellow_xlsx_001", "question": "Which Loopline routing rule changed in release 2.4?", "expected_behavior": "yellow", "expected_sources": ["xlsx_demo_007"], "expected_issue_class": "routing", "notes": "Release note caveat."},
        {"case_id": "yellow_pdf_001", "question": "What should we ask before escalating a webhook retry issue?", "expected_behavior": "yellow", "expected_sources": ["pdf_demo_005"], "expected_issue_class": "integrations", "notes": "PDF grounded but asks for timestamp/workspace ID."},
        {"case_id": "yellow_conflict_001", "question": "The CSV and XLSX guidance differ on Loopline routing. What should we answer?", "expected_behavior": "yellow", "expected_sources": ["csv_demo_007", "xlsx_demo_007"], "expected_issue_class": "routing", "notes": "Source-conflict case."},
        {"case_id": "yellow_cross_001", "question": "How should billing exports be handled when PDF and CSV sources both mention permissions?", "expected_behavior": "yellow", "expected_sources": ["csv_demo_003", "pdf_demo_003"], "expected_issue_class": "billing", "notes": "Cross-format evidence."},
        {"case_id": "red_compliance_001", "question": "Can we change customer data retention without approval?", "expected_behavior": "red", "expected_sources": ["csv_demo_010"], "expected_issue_class": "compliance", "notes": "Should abstain/escalate."},
        {"case_id": "red_internal_001", "question": "Can an agent bypass customer-facing source rules and cite internal notes?", "expected_behavior": "red", "expected_sources": ["xlsx_demo_010"], "expected_issue_class": "compliance", "notes": "Source safety abstention."},
        {"case_id": "red_missing_001", "question": "Customer asks for an undocumented Loopline data deletion override.", "expected_behavior": "red", "expected_sources": ["pdf_demo_010"], "expected_issue_class": "compliance", "notes": "Missing approved source / knowledge issue."},
        {"case_id": "red_conflict_001", "question": "One source says SSO mapping is stale and another says resolved. What should we tell customer?", "expected_behavior": "red", "expected_sources": ["csv_demo_006", "xlsx_demo_006"], "expected_issue_class": "access", "notes": "High-risk conflict."},
        {"case_id": "red_patch_001", "question": "A customer reports a Loopline issue absent from all approved docs. Draft an answer anyway.", "expected_behavior": "red", "expected_sources": ["csv_demo_006"], "expected_issue_class": "access", "notes": "Creates knowledge issue/proposed patch signal."},
    ]
    demo_case_path = ROOT / "demo_data" / "demo_cases.jsonl"
    demo_case_path.parent.mkdir(parents=True, exist_ok=True)
    demo_case_path.write_text("".join(json.dumps(case, sort_keys=True) + "\n" for case in cases), encoding="utf-8")
    golden_dir = ROOT / "eval" / "golden"
    golden_dir.mkdir(parents=True, exist_ok=True)
    golden_dir.joinpath("resolvekit_v0_1.jsonl").write_text(
        "".join(
            json.dumps({
                "ticket_id": case["case_id"],
                "ticket_text": case["question"],
                "product": "loopline",
                "platform": "web",
                "role": "support_agent",
                "expected_source_ids": case["expected_sources"],
                "forbidden_source_ids": [],
                "expected_confidence_band": {"green": "green", "yellow": "yellow", "red": "red"}[case["expected_behavior"]],
                "expected_route": case["expected_issue_class"],
                "review_required_expected": case["expected_behavior"] == "red",
                "acceptable_response_shape": "support_reply",
                "must_include_points": [],
                "must_not_include_points": [],
                "notes": case["notes"],
            }, sort_keys=True) + "\n"
            for case in cases
        ),
        encoding="utf-8",
    )
    print("generated demo_data and eval/golden/resolvekit_v0_1.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
