"""Command line interface for the knowledge layer."""

from __future__ import annotations

import argparse
import json

from pydantic import TypeAdapter

from app.knowledge.models import RetrievalHit
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.sap_catalog_ingest import SapCatalogIngest
from app.knowledge.settings import get_settings
from app.knowledge.store import KnowledgeStore
from app.knowledge.vector_index import VectorIndex


def cmd_lookup(args: argparse.Namespace) -> None:
    store = KnowledgeStore()
    result = store.lookup_sap(table=args.table, field=args.field)
    print(result.model_dump_json(indent=2))


def cmd_search_sap(args: argparse.Namespace) -> None:
    retriever = SapRetriever()
    result = retriever.search(query=args.query, k=args.k, module_filter=args.module)
    print(result.model_dump_json(indent=2))


def cmd_search_sql(args: argparse.Namespace) -> None:
    retriever = SqlDocRetriever()
    hits = retriever.search(query=args.query, k=args.k, dialect=args.dialect)
    print(TypeAdapter(list[RetrievalHit]).dump_json(hits, indent=2).decode("utf-8"))


def cmd_coverage(args: argparse.Namespace) -> None:
    settings = get_settings()
    # Check coverage of known SAP tables
    table_names_path = settings.knowledge_dir / "sap_table_names.json"
    candidate_tables: list[str] = []
    if table_names_path.exists():
        try:
            with open(table_names_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, str):
                            candidate_tables.append(item)
                        elif isinstance(item, dict):
                            name = item.get("name") or item.get("table")
                            if name:
                                candidate_tables.append(name)
                elif isinstance(data, dict):
                    for item in data.get("tables", []):
                        if isinstance(item, str):
                            candidate_tables.append(item)
                        elif isinstance(item, dict):
                            name = item.get("name") or item.get("table")
                            if name:
                                candidate_tables.append(name)
        except (OSError, json.JSONDecodeError):
            candidate_tables = []

    if not candidate_tables:
        candidate_tables = [
            "MARA",
            "MARC",
            "MARD",
            "MAKT",
            "MBEW",
            "MVKE",
            "KNA1",
            "KNB1",
            "KNVV",
            "LFA1",
            "LFB1",
            "LFM1",
            "EKKO",
            "EKPO",
            "EKBE",
            "EINA",
            "EINE",
            "VBAK",
            "VBAP",
            "VBRK",
            "VBRP",
            "LIKP",
            "LIPS",
            "BKPF",
            "BSEG",
            "BUT000",
            "BUT100",
            "ADRC",
            "T001",
            "T001W",
            "T001L",
            "T023",
            "T024",
            "TVKO",
            "CDHDR",
            "CDPOS",
        ]

    ingestor = SapCatalogIngest(settings=settings)
    report = ingestor.coverage_check(candidate_tables)
    print(json.dumps(report, indent=2))


def cmd_stats(args: argparse.Namespace) -> None:
    settings = get_settings()
    store = KnowledgeStore(settings=settings)

    sap_idx = VectorIndex(
        "sap_docs", settings.sap_index_dir, settings.sap_pdf_path, settings=settings
    )
    sql_idx = VectorIndex(
        "sql_docs", settings.sql_index_dir, settings.sql_pdf_path, settings=settings
    )

    sap_stale, sap_reason = sap_idx.is_stale()
    sql_stale, sql_reason = sql_idx.is_stale()

    sap_chunks = 0
    if sap_idx.manifest_path.exists():
        try:
            with open(sap_idx.manifest_path, "r", encoding="utf-8") as f:
                sap_chunks = int(json.load(f).get("chunk_count", 0))
        except (OSError, json.JSONDecodeError, ValueError):
            sap_chunks = 0

    sql_chunks = 0
    if sql_idx.manifest_path.exists():
        try:
            with open(sql_idx.manifest_path, "r", encoding="utf-8") as f:
                sql_chunks = int(json.load(f).get("chunk_count", 0))
        except (OSError, json.JSONDecodeError, ValueError):
            sql_chunks = 0

    stats = {
        "prebuilt_sap_tables": len(store._prebuilt_sap),
        "prebuilt_common_fields": len(store._common_fields),
        "prebuilt_sql_keywords": len(store._prebuilt_sql),
        "catalog_tables": len(store._catalog_tables),
        "learned_sap_entries": len(store.sap_learned_store.get_entries()),
        "learned_sql_entries": len(store.sql_learned_store.get_entries()),
        "vector_indexes": {
            "sap_docs": {
                "built": sap_idx.is_built(),
                "stale": sap_stale,
                "reason": sap_reason,
                "chunks": sap_chunks,
            },
            "sql_docs": {
                "built": sql_idx.is_built(),
                "stale": sql_stale,
                "reason": sql_reason,
                "chunks": sql_chunks,
            },
        },
    }
    print(json.dumps(stats, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge layer CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # lookup
    p_lookup = subparsers.add_parser("lookup", help="Look up SAP table or field")
    p_lookup.add_argument("table", help="SAP table name")
    p_lookup.add_argument("field", nargs="?", default=None, help="SAP field name (optional)")

    # search-sap
    p_search_sap = subparsers.add_parser("search-sap", help="Search SAP documentation")
    p_search_sap.add_argument("query", help="Search query")
    p_search_sap.add_argument("-k", type=int, default=5, help="Top k results")
    p_search_sap.add_argument("--module", default=None, help="Module filter (e.g. MM, SD, FI/CO)")

    # search-sql
    p_search_sql = subparsers.add_parser("search-sql", help="Search SQL documentation")
    p_search_sql.add_argument("query", help="Search query")
    p_search_sql.add_argument("-k", type=int, default=5, help="Top k results")
    p_search_sql.add_argument("--dialect", default="tsql", help="Target dialect (default: tsql)")

    # coverage
    subparsers.add_parser("coverage", help="Check SAP table coverage")

    # stats
    subparsers.add_parser("stats", help="Show knowledge layer statistics")

    args = parser.parse_args()

    if args.command == "lookup":
        cmd_lookup(args)
    elif args.command == "search-sap":
        cmd_search_sap(args)
    elif args.command == "search-sql":
        cmd_search_sql(args)
    elif args.command == "coverage":
        cmd_coverage(args)
    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
