"""Deterministic Knowledge Pack builders for SAP Domain and SQL Semantics agents.

No LLM calls. Packs are compact, capped in size, and carry source and verification flags.
"""

from __future__ import annotations

import re
import warnings
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

warnings.filterwarnings(
    "ignore",
    message='Field name "construct" in "SqlPackUnresolvedItem" shadows an attribute in parent "BaseModel"',
)

if TYPE_CHECKING:
    from app.knowledge.store import KnowledgeStore
    from app.parser.models import ParsedQuery


# ==============================================================================
# Knowledge Pack Schemas
# ==============================================================================


class SapPackResolvedItem(BaseModel):
    """An item resolved deterministically from the SAP knowledge store."""

    model_config = ConfigDict(extra="forbid")

    table: str
    field: str | None = None
    level: str
    description: str
    key_info: str | None = None
    source: str
    verified: bool


class SapPackUnresolvedItem(BaseModel):
    """An item that could not be resolved from standard prebuilt or catalog tables."""

    model_config = ConfigDict(extra="forbid")

    table: str
    field: str | None = None
    reason: str


class SapNamingFact(BaseModel):
    """Naming convention fact extracted by the parser."""

    model_config = ConfigDict(extra="forbid")

    table_name: str
    role: str
    prefix: str | None = None
    dataset: str | None = None
    sap_table: str | None = None
    zsource: str | None = None
    source_table: str | None = None
    action: str | None = None
    field: str | None = None


class SapKnowledgePack(BaseModel):
    """Compact deterministic knowledge pack supplied to the SAP Domain Agent."""

    model_config = ConfigDict(extra="forbid")

    resolved: list[SapPackResolvedItem] = Field(default_factory=list)
    unresolved: list[SapPackUnresolvedItem] = Field(default_factory=list)
    naming_facts: list[SapNamingFact] = Field(default_factory=list)
    custom_field_conventions: list[str] = Field(default_factory=list)
    mandt_note: str | None = None
    scope_filters: list[str] = Field(default_factory=list)
    update_targets: list[dict[str, Any]] = Field(default_factory=list)
    mapping_reference_tables: list[str] = Field(default_factory=list)


class SqlPackResolvedItem(BaseModel):
    """A SQL keyword or construct resolved from the SQL knowledge store."""

    model_config = ConfigDict(extra="forbid")

    keyword: str
    category: str
    technical_meaning: str
    tsql_example: str | None = None
    performance_notes: str | None = None
    source: str
    verified: bool


class SqlPackUnresolvedItem(BaseModel):
    """A SQL construct detected in query that does not exist in the keyword catalog."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    construct: str  # type: ignore[assignment]
    reason: str


class SqlKnowledgePack(BaseModel):
    """Compact deterministic knowledge pack supplied to the SQL Semantics Agent."""

    model_config = ConfigDict(extra="forbid")

    resolved: list[SqlPackResolvedItem] = Field(default_factory=list)
    unresolved: list[SqlPackUnresolvedItem] = Field(default_factory=list)


# ==============================================================================
# Builder Functions
# ==============================================================================

CUSTOM_FIELD_CONVENTIONS = [
    "Z<FIELD>: Custom or legacy field staged for migration",
    "<FIELD>NEW: Target column updated in Syniti rule view projection",
    "ZACTIVE: Syniti staging active-record flag (typically 1 = Active)",
    "ZLEGACYWERKS: Legacy system plant code before mapping",
]


def build_sap_pack(
    parsed: ParsedQuery,
    store: KnowledgeStore,
    max_items: int = 40,
    max_desc_len: int = 300,
) -> SapKnowledgePack:
    """Builds a compact, deterministic SAP knowledge pack for the query."""
    resolved: list[SapPackResolvedItem] = []
    unresolved: list[SapPackUnresolvedItem] = []
    naming_facts: list[SapNamingFact] = []
    mapping_reference_tables: list[str] = []

    # 1. Identify mapping/reference tables and collect naming facts
    for t in parsed.tables:
        if t.role in ("mapping_reference", "external_source"):
            mapping_reference_tables.append(t.raw_name)

        naming = t.naming
        naming_facts.append(
            SapNamingFact(
                table_name=t.raw_name,
                role=t.role,
                prefix=naming.prefix if naming else None,
                dataset=naming.dataset if naming else None,
                sap_table=naming.sap_table if naming else t.sap_table,
                zsource=naming.zsource if naming else None,
                source_table=naming.source_table if naming else None,
                action=naming.action if naming else None,
                field=naming.field if naming else None,
            )
        )

    if parsed.view_naming:
        vn = parsed.view_naming
        naming_facts.append(
            SapNamingFact(
                table_name=parsed.view_name or "VIEW",
                role=vn.object_kind,
                prefix=vn.prefix,
                dataset=vn.dataset,
                sap_table=vn.sap_table,
                zsource=vn.zsource,
                source_table=vn.source_table,
                action=vn.action,
                field=vn.field,
            )
        )

    # 2. Look up SAP tables (EXCLUDING mapping/reference tables)
    resolved_tables_set: set[str] = set()
    sap_tables_to_lookup: list[str] = []

    # Collect sap_tables from tables and view_naming
    if parsed.view_naming and parsed.view_naming.sap_table:
        sap_tables_to_lookup.append(parsed.view_naming.sap_table)

    for t in parsed.tables:
        if t.raw_name in mapping_reference_tables:
            continue
        st = t.sap_table or (t.naming.sap_table if t.naming else None)
        if st and st not in sap_tables_to_lookup:
            sap_tables_to_lookup.append(st)

    for tbl_name in sap_tables_to_lookup:
        res = store.lookup_sap(tbl_name)
        if res.found:
            resolved_tables_set.add(tbl_name)
            desc = ""
            key_info = None
            if res.entry:
                desc = str(res.entry.get("description", ""))[:max_desc_len]
                key_fields = res.entry.get("key_fields")
                if key_fields:
                    key_info = f"Key fields: {', '.join(key_fields)}"
                elif res.entry.get("sections"):
                    key_info = f"Sections: {', '.join(res.entry.get('sections', []))}"

            final_desc = desc or f"SAP table {tbl_name}"
            if not res.verified:
                final_desc = f"{final_desc} [UNVERIFIED - Lower Confidence]"

            resolved.append(
                SapPackResolvedItem(
                    table=tbl_name,
                    field=None,
                    level=res.level,
                    description=final_desc,
                    key_info=key_info,
                    source=res.source or "catalog",
                    verified=res.verified,
                )
            )
        else:
            unresolved.append(
                SapPackUnresolvedItem(
                    table=tbl_name,
                    field=None,
                    reason=res.notes or "SAP table not found in prebuilt or catalog",
                )
            )

    # 3. Look up fields
    driving_sap_table = (
        parsed.view_naming.sap_table
        if parsed.view_naming and parsed.view_naming.sap_table
        else (sap_tables_to_lookup[0] if sap_tables_to_lookup else "MARA")
    )

    query_sap_tables: list[str] = []
    for t in parsed.tables:
        if t.raw_name in mapping_reference_tables:
            continue
        st = t.sap_table or (t.naming.sap_table if t.naming else None)
        if st and st not in query_sap_tables:
            query_sap_tables.append(st)

    if parsed.view_naming and parsed.view_naming.sap_table:
        vn_st = parsed.view_naming.sap_table
        if vn_st not in query_sap_tables:
            query_sap_tables.append(vn_st)

    seen_fields: set[tuple[str, str]] = set()

    for col in parsed.columns:
        fld_clean = col.column.upper()

        # Handle <FIELD>NEW convention directly (e.g. ZSTRGRNEW -> derived target value for ZSTRGR)
        if fld_clean.endswith("NEW") and len(fld_clean) > 3:
            base_field = fld_clean[:-3]
            target_tbl = driving_sap_table
            if col.table:
                matching_ref = next(
                    (t for t in parsed.tables if t.raw_name == col.table or t.alias == col.table),
                    None,
                )
                if matching_ref and matching_ref.role in ("mapping_reference", "external_source"):
                    continue
                if matching_ref and matching_ref.sap_table:
                    target_tbl = matching_ref.sap_table

            if (target_tbl, fld_clean) not in seen_fields:
                seen_fields.add((target_tbl, fld_clean))
                resolved.append(
                    SapPackResolvedItem(
                        table=target_tbl,
                        field=fld_clean,
                        level="field",
                        description=f"derived target value for {base_field}",
                        key_info=f"Matches convention <FIELD>NEW: target column updated in rule view projection (base field: {base_field})",
                        source="custom_convention",
                        verified=True,
                    )
                )
            continue

        # Qualified column
        if col.table:
            matching_ref = next(
                (t for t in parsed.tables if t.raw_name == col.table or t.alias == col.table),
                None,
            )
            if matching_ref and matching_ref.role in ("mapping_reference", "external_source"):
                continue
            target_tbl = (
                matching_ref.sap_table
                if (matching_ref and matching_ref.sap_table)
                else col.table.upper()
            )

            if (target_tbl, fld_clean) in seen_fields:
                continue
            seen_fields.add((target_tbl, fld_clean))

            fld_res = store.lookup_sap(target_tbl, fld_clean)
            if fld_res.found:
                desc = ""
                if fld_res.entry:
                    desc = str(
                        fld_res.entry.get("business_meaning")
                        or fld_res.entry.get("description")
                        or ""
                    )[:max_desc_len]

                final_fld_desc = desc or f"Field {fld_clean}"
                if not fld_res.verified:
                    final_fld_desc = f"{final_fld_desc} [UNVERIFIED - Lower Confidence]"

                resolved.append(
                    SapPackResolvedItem(
                        table=target_tbl,
                        field=fld_clean,
                        level=fld_res.level,
                        description=final_fld_desc,
                        key_info=fld_res.notes,
                        source=fld_res.source or "common_fields",
                        verified=fld_res.verified,
                    )
                )
            else:
                unresolved.append(
                    SapPackUnresolvedItem(
                        table=target_tbl,
                        field=fld_clean,
                        reason=fld_res.notes or "Field not found in SAP knowledge base",
                    )
                )
        else:
            # Unqualified column
            # 1. If this field was already qualified and resolved for a query table earlier, skip duplicate
            already_resolved = [
                r for r in resolved if r.field == fld_clean and r.table in query_sap_tables
            ]
            if len(already_resolved) == 1:
                continue

            # 2. Check which query table's known fields contain it
            matching_tables: list[tuple[str, Any]] = []
            for cand_tbl in query_sap_tables:
                res = store.lookup_sap(cand_tbl, fld_clean)
                if res.found:
                    matching_tables.append((cand_tbl, res))

            if len(matching_tables) == 1:
                target_tbl, fld_res = matching_tables[0]
                if (target_tbl, fld_clean) in seen_fields:
                    continue
                seen_fields.add((target_tbl, fld_clean))

                desc = ""
                if fld_res.entry:
                    desc = str(
                        fld_res.entry.get("business_meaning")
                        or fld_res.entry.get("description")
                        or ""
                    )[:max_desc_len]

                final_fld_desc = desc or f"Field {fld_clean}"
                if not fld_res.verified:
                    final_fld_desc = f"{final_fld_desc} [UNVERIFIED - Lower Confidence]"

                resolved.append(
                    SapPackResolvedItem(
                        table=target_tbl,
                        field=fld_clean,
                        level=fld_res.level,
                        description=final_fld_desc,
                        key_info=fld_res.notes,
                        source=fld_res.source or "common_fields",
                        verified=fld_res.verified,
                    )
                )
            elif len(matching_tables) > 1:
                # Genuinely ambiguous across multiple query tables -> list candidates
                candidate_names = [m[0] for m in matching_tables]
                key_pair = ("/".join(candidate_names), fld_clean)
                if key_pair not in seen_fields:
                    seen_fields.add(key_pair)
                    unresolved.append(
                        SapPackUnresolvedItem(
                            table="/".join(candidate_names),
                            field=fld_clean,
                            reason=f"Ambiguous unqualified column: candidates are {', '.join(candidate_names)}",
                        )
                    )
            else:
                # Not found in any query table
                target_tbl = driving_sap_table
                if (target_tbl, fld_clean) in seen_fields:
                    continue
                seen_fields.add((target_tbl, fld_clean))
                fld_res = store.lookup_sap(target_tbl, fld_clean)
                unresolved.append(
                    SapPackUnresolvedItem(
                        table=target_tbl,
                        field=fld_clean,
                        reason=fld_res.notes
                        or f"Field {fld_clean} not found in query tables ({', '.join(query_sap_tables)})",
                    )
                )

    # 4. Mandatory client handling note
    mandt_lookup = store.lookup_sap("", "MANDT")
    if mandt_lookup.entry:
        desc = str(mandt_lookup.entry.get("description", "")).strip()
        bm = str(mandt_lookup.entry.get("business_meaning", "")).strip()
        if desc and bm:
            mandt_note = f"{desc}: {bm}"
        elif bm:
            mandt_note = bm
        elif desc:
            mandt_note = desc
        else:
            mandt_note = "Client ID (Mandant)"
    else:
        mandt_note = "Client ID (Mandant)"

    # Deduplicate source_fields in update_targets while preserving order
    formatted_targets: list[dict[str, Any]] = []
    for u in parsed.update_targets[:max_items]:
        u_dict = u.model_dump()
        if "source_fields" in u_dict and isinstance(u_dict["source_fields"], list):
            u_dict["source_fields"] = list(dict.fromkeys(u_dict["source_fields"]))
        formatted_targets.append(u_dict)

    return SapKnowledgePack(
        resolved=resolved[:max_items],
        unresolved=unresolved[:max_items],
        naming_facts=naming_facts[:max_items],
        custom_field_conventions=CUSTOM_FIELD_CONVENTIONS,
        mandt_note=mandt_note,
        scope_filters=parsed.scope_filters[:max_items],
        update_targets=formatted_targets,
        mapping_reference_tables=sorted(set(mapping_reference_tables)),
    )


def build_sql_pack(
    parsed: ParsedQuery,
    store: KnowledgeStore,
    max_items: int = 40,
    max_meaning_len: int = 300,
) -> SqlKnowledgePack:
    """Builds a compact, deterministic SQL knowledge pack for the query."""
    resolved: list[SqlPackResolvedItem] = []
    unresolved: list[SqlPackUnresolvedItem] = []
    detected_constructs: list[str] = []

    # Statement / View creation
    if parsed.view_name:
        detected_constructs.append("CREATE VIEW")
    elif parsed.statement_type in ("INSERT", "UPDATE", "DELETE", "MERGE"):
        detected_constructs.append(parsed.statement_type)

    # Joins
    for j in parsed.joins:
        j_type = (j.type or "INNER").upper()
        if j_type in ("INNER", "INNER JOIN"):
            detected_constructs.append("INNER JOIN")
        elif j_type in ("LEFT", "LEFT OUTER", "LEFT JOIN", "LEFT OUTER JOIN"):
            detected_constructs.append("LEFT JOIN")
        elif j_type in ("RIGHT", "RIGHT OUTER", "RIGHT JOIN", "RIGHT OUTER JOIN"):
            detected_constructs.append("RIGHT JOIN")
        elif j_type in ("FULL", "FULL OUTER", "FULL JOIN", "FULL OUTER JOIN"):
            detected_constructs.append("FULL JOIN")
        elif j_type in ("CROSS", "CROSS JOIN"):
            detected_constructs.append("CROSS JOIN")
        elif j_type in ("CROSS APPLY", "APPLY"):
            detected_constructs.append("CROSS APPLY")
        elif j_type == "OUTER APPLY":
            detected_constructs.append("OUTER APPLY")

    # CTEs
    if parsed.ctes:
        detected_constructs.append("WITH CTE")
        if any(c.recursive for c in parsed.ctes):
            detected_constructs.append("recursive CTE")

    # Subqueries
    if parsed.subqueries:
        detected_constructs.append("subqueries")

    # Filters
    if parsed.filters or parsed.scope_filters:
        detected_constructs.append("WHERE")

    # Set operations
    for s in parsed.set_operations:
        s_upper = s.upper()
        if "UNION ALL" in s_upper:
            detected_constructs.append("UNION ALL")
        elif "UNION" in s_upper:
            detected_constructs.append("UNION")
        elif "EXCEPT" in s_upper:
            detected_constructs.append("EXCEPT")
        elif "INTERSECT" in s_upper:
            detected_constructs.append("INTERSECT")

    # Window functions
    for wf in parsed.window_functions:
        wf_upper = wf.upper()
        if "ROW_NUMBER" in wf_upper:
            detected_constructs.append("ROW_NUMBER")
        if "RANK" in wf_upper and "DENSE_RANK" not in wf_upper:
            detected_constructs.append("RANK")
        if "DENSE_RANK" in wf_upper:
            detected_constructs.append("DENSE_RANK")
        if "NTILE" in wf_upper:
            detected_constructs.append("NTILE")
        if "LEAD" in wf_upper:
            detected_constructs.append("LEAD")
        if "LAG" in wf_upper:
            detected_constructs.append("LAG")

    # Aggregations
    if parsed.aggregations or parsed.group_by:
        detected_constructs.append("GROUP BY")
    for agg in parsed.aggregations:
        agg_upper = agg.upper()
        for fn in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
            if fn in agg_upper:
                detected_constructs.append(fn)

    # Derivations / Update targets
    for ut in parsed.update_targets:
        if ut.expression_kind == "case":
            detected_constructs.append("CASE")
        elif ut.expression_kind == "concat":
            detected_constructs.append("+")

    # SQL text pattern matching for standard constructs
    norm_upper = parsed.normalized_sql.upper()
    check_patterns = [
        (r"\bCASE\b", "CASE"),
        (r"\bISNULL\b", "ISNULL"),
        (r"\bCOALESCE\b", "COALESCE"),
        (r"\bNULLIF\b", "NULLIF"),
        (r"\bIIF\b", "IIF"),
        (r"\bCAST\b", "CAST"),
        (r"\bCONVERT\b", "CONVERT"),
        (r"\bTRY_CAST\b", "TRY_CAST"),
        (r"\bSUBSTRING\b", "SUBSTRING"),
        (r"\bCHARINDEX\b", "CHARINDEX"),
        (r"\bSTUFF\b", "STUFF"),
        (r"\bLEN\b", "LEN"),
        (r"\bLTRIM\b", "LTRIM"),
        (r"\bRTRIM\b", "RTRIM"),
        (r"\bUPPER\b", "UPPER"),
        (r"\bLOWER\b", "LOWER"),
        (r"\bNOT\s+EXISTS\b", "NOT EXISTS"),
        (r"\bEXISTS\b", "EXISTS"),
        (r"\bNOT\s+IN\b", "NOT IN"),
        (r"\bIN\b", "IN"),
        (r"\bLIKE\b", "LIKE"),
        (r"\bCROSS\s+APPLY\b", "CROSS APPLY"),
        (r"\bOUTER\s+APPLY\b", "OUTER APPLY"),
        (r"\bHAVING\b", "HAVING"),
        (r"\bORDER\s+BY\b", "ORDER BY"),
        (r"\bDISTINCT\b", "DISTINCT"),
        (r"\bTOP\b", "TOP"),
        (r"\bAND\b", "AND"),
        (r"\bOR\b", "OR"),
        (r"\bNOLOCK\b", "NOLOCK"),
    ]

    for pattern, term in check_patterns:
        if re.search(pattern, norm_upper) and term not in detected_constructs:
            detected_constructs.append(term)

    # String concatenation
    if "+" in parsed.normalized_sql and "+" not in detected_constructs:
        detected_constructs.append("+")

    # Temp tables
    if "##" in parsed.normalized_sql:
        detected_constructs.append("##temp tables")
    elif "#" in parsed.normalized_sql:
        detected_constructs.append("#temp tables")

    # Look up each construct
    seen_kw: set[str] = set()
    for construct in detected_constructs:
        construct_clean = construct.strip().upper()
        if construct_clean in seen_kw:
            continue
        seen_kw.add(construct_clean)

        kw = store.lookup_sql_keyword(construct_clean)
        if kw:
            meaning = kw.technical_meaning[:max_meaning_len]
            if not kw.verified:
                meaning = f"{meaning} [UNVERIFIED - Lower Confidence]"

            resolved.append(
                SqlPackResolvedItem(
                    keyword=kw.keyword,
                    category=kw.category,
                    technical_meaning=meaning,
                    tsql_example=kw.tsql_example[:200] if kw.tsql_example else None,
                    performance_notes=kw.performance_notes[:200] if kw.performance_notes else None,
                    source=kw.source.value,
                    verified=kw.verified,
                )
            )
        else:
            unresolved.append(
                SqlPackUnresolvedItem(
                    construct=construct_clean,
                    reason="Construct not present in prebuilt SQL keywords",
                )
            )

    return SqlKnowledgePack(
        resolved=resolved[:max_items],
        unresolved=unresolved[:max_items],
    )
