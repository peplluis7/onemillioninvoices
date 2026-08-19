# Contributing

Changes to the clearing kernel should preserve deterministic ordering, integer-cent arithmetic, atomic source provenance, and independent replay. A pull request that changes a terminal result must include a test explaining the intended semantic change.

Before submitting:

```bash
python -m pip install -e '.[dev]'
pytest
ruff check src tests scripts
```

Do not commit identifiable invoice data, reversible company mappings, credentials, or proprietary workbook extracts.
