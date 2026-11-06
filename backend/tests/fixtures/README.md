# Test Fixtures

This directory contains test fixture files for parser, knowledge pack, and validation testing.

## Proprietary Sample Data (Excluded from Git)

The following files contain proprietary enterprise data quality rules and sample queries:

- `DQ_Queries.xlsx`: Original data quality workbook containing test queries across migration sheets.
- `examples.json`: Extracted structured representation of the workbook test queries.

If these files are absent from a clean repository clone, the automated test suite gracefully skips tests requiring them (via `pytest.skip`) without failing.

To run tests against the workbook examples, place `DQ_Queries.xlsx` and `examples.json` in this directory.

## Synthetic Fixtures (Tracked in Git)

- `redteam.json`: Synthetic prompt injection and security test cases.
