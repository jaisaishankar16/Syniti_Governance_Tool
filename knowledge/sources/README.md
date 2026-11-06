# Knowledge Sources

This directory stores original source reference documents used to generate knowledge bases and vector indexes.

## Required Documents (Excluded from Git)

Raw binary reference documents are excluded from version control to maintain repository hygiene and respect proprietary distribution terms:

- `Naming_Conventions.docx`: Naming convention standards document.
- `sap_tables.pdf`: SAP table dictionary reference document.
- `sql_reference.pdf`: SQL syntax and dialect reference document.

## Index Regeneration

Once source documents are placed in this directory, generate vector search indexes and chunk manifests by running:

```bash
python scripts/rebuild_index.py
```
