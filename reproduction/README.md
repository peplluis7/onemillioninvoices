# Frozen paper-reproduction programs

These programs preserve the source-specific experiment pipeline used to produce the reported annual evidence. They include OOXML field extraction, harmonized-year loading, complete-candidate bounded-cycle benchmarking, the optimized large-year daily executor, 2023 issue-year attribution, annual figure generation, and independent replay.

They expect confidential source workbooks and intermediate artifacts below the directory specified by `TEMPORAL_INVOICE_ROOT`. No raw invoice data, reversible company map, credentials, or identifiable operation logs are distributed here.

The modular package under `src/` is the maintained public interface. The frozen directory exists to preserve historical provenance and may use source-specific field names or compiled helpers. It should not be silently refactored. Corrections to a published experiment should be placed in a new versioned reproduction directory and documented in release notes.
