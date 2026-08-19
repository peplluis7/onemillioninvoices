# Public atomic-record schema

The reference implementation consumes a UTF-8 CSV with one row per retained source invoice. Monetary values are integer euro cents. Dates use ISO `YYYY-MM-DD` and are inclusive.

| Column | Required | Meaning |
|---|---:|---|
| `uid` | yes | Stable source-record identifier; unique within a run. |
| `debtor` | yes | Firm that owes the obligation. |
| `creditor` | yes | Firm entitled to receive payment. |
| `amount_cents` | yes | Positive original invoice amount in integer cents. |
| `issue_date` | yes | First day on which the record can support an operation. |
| `due_date` | yes | Last day on which the record can support an operation. |
| `status` | no | Validation or workflow status. |
| `source` | no | Source workbook, database, or provenance label. |
| `fingerprint` | no | Reproducible duplicate-audit hash. |
| `cohort` | no | Issue cohort used for cross-year attribution, for example `2023`. |

The package deliberately separates source-specific extraction from the clearing kernel. Raw company identifiers and invoice exports are not included. The `reproduction/frozen` directory contains the source-specific scripts used for the paper; they expect the private raw files to be mounted by the researcher.
