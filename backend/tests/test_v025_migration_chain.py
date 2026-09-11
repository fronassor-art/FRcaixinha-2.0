from pathlib import Path
import re


def test_single_alembic_head_after_security_reconciliation():
    import subprocess

    result = subprocess.run(
        ["alembic", "heads"],
        capture_output=True,
        text=True,
        check=True,
    )

    heads = [
        line.split()[0]
        for line in result.stdout.splitlines()
        if line.strip()
    ]

    assert heads == ["d04267d34476"]

    root = Path(__file__).parents[1] / "alembic" / "versions"
    rows = {}

    for path in root.glob("*.py"):
        text = path.read_text()
        rev = re.search(
            r"revision\s*=\s*[\"']([^\"']+)",
            text,
        )
        down = re.search(
            r"down_revision\s*=\s*[\"']([^\"']+)",
            text,
        )

        if rev:
            rows[rev.group(1)] = down.group(1) if down else None

    assert rows["0006_security_v11"] == "0005_financial_operations"
    assert rows["0007_notifications"] == "0006_security_v11"
    assert rows["0010_security_reconciliation"] == "0009_loan_financial_engine"
