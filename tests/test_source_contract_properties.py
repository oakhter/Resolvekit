import csv

from knowledge_loader.source_contract import source_validation_report


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


def valid_row():
    return {
        "source_id": "prop_001",
        "source_title": "Property Test",
        "source_type": "csv",
        "source_authority": "canonical",
        "is_approved": "true",
        "is_active": "true",
        "is_customer_facing_allowed": "true",
        "approved_at": "2026-01-01",
        "reviewed_by": "support_ops",
        "needs_review_at": "2027-01-01",
        "doc_type": "faq",
        "product_area": "login",
        "issue_class": "password_reset",
        "version_scope": "v1",
        "escalation_risk": "low",
        "body": "Use the approved password reset flow from account settings.",
    }


def write_csv(path, fieldnames, row):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fieldnames})


def test_each_required_source_column_is_enforced(tmp_path):
    for column in REQUIRED_COLUMNS:
        path = tmp_path / f"missing_{column}.csv"
        fields = [field for field in REQUIRED_COLUMNS if field != column]
        write_csv(path, fields, valid_row())

        report = source_validation_report([path])

        assert report["validation_errors"], column
        assert column in report["validation_errors"][0]["message"]
