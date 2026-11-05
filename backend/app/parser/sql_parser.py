"""Deterministic SQL parser for T-SQL with Syniti conventions and SAP catalog integration."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import sqlglot
from sqlglot import exp

from .models import (
    ColumnInfo,
    Complexity,
    CTEInfo,
    JoinInfo,
    ParsedQuery,
    ParseError,
    RiskFlag,
    TableInfo,
    UpdateTarget,
)
from .naming import SynitiNamingEngine
from .risk import evaluate_complexity_and_risk
from .sap_catalog import SapTableCatalog

logger = logging.getLogger(__name__)
# Pin sqlglot version and log it
logger.info("SqlParser initialized using sqlglot version: %s", sqlglot.__version__)


class SqlParser:
    """Deterministic T-SQL parser without LLM dependencies."""

    def __init__(
        self,
        max_length: int = 200_000,
        max_statements: int = 100,
        max_nesting_depth: int = 50,
        naming_engine: SynitiNamingEngine | None = None,
        sap_catalog: SapTableCatalog | None = None,
    ) -> None:
        self.max_length = max_length
        self.max_statements = max_statements
        self.max_nesting_depth = max_nesting_depth
        self.naming_engine = naming_engine or SynitiNamingEngine()
        self.sap_catalog = sap_catalog or SapTableCatalog()

    def parse(self, sql: str) -> ParsedQuery:
        """Parses a T-SQL query string into a structured ParsedQuery."""
        if not sql or not sql.strip():
            raise ParseError("SQL input is empty")

        # 1. Enforce length limits
        if len(sql) > self.max_length:
            raise ParseError(
                f"SQL input exceeds maximum allowed length ({len(sql)} > {self.max_length})"
            )

        flags: list[str] = []
        warnings: list[str] = []

        # 2. Extract comments and remove them from normalized SQL
        cleaned_sql, comments = self._extract_and_strip_comments(sql)

        # 3. Pre-normalize single-bracket dot quirk: [TABLE_NAME.FIELD] -> [TABLE_NAME].[FIELD]
        cleaned_sql, quirk_found = self._normalize_single_bracket_quirk(cleaned_sql)
        if quirk_found:
            flags.append("QUIRK_DOT_INSIDE_BRACKETS")

        # 4. Check for batches (GO separators or semicolons)
        statements = self._split_batches(cleaned_sql)
        if len(statements) > self.max_statements:
            raise ParseError(
                f"Batch contains too many statements ({len(statements)} > {self.max_statements})"
            )
        if len(statements) > 1:
            flags.append("BATCH_STATEMENT")

        primary_sql = statements[0] if statements else cleaned_sql
        wrapper_hash: str | None = None
        statement_type = "SELECT"
        view_name: str | None = None
        view_schema: str | None = None
        inner_tree: exp.Expression | None = None
        root_tree: Any = None

        # 5. Parse with sqlglot
        try:
            root_tree = sqlglot.parse_one(primary_sql, read="tsql")
            if root_tree is None:
                raise ParseError("Failed to parse SQL AST")
        except Exception as e:
            if isinstance(e, ParseError):
                raise
            # Check if this is a procedure or generic command that can be fallback-extracted
            upper_sql = primary_sql.upper().strip()
            if any(
                upper_sql.startswith(cmd)
                for cmd in ("CREATE PROCEDURE", "ALTER PROCEDURE", "EXEC", "EXECUTE")
            ):
                return self._fallback_parse(
                    primary_sql=primary_sql,
                    cleaned_sql=cleaned_sql,
                    comments=comments,
                    flags=flags,
                    warning_msg=f"Fallback parsing used for procedural statement: {e}",
                )
            # Otherwise invalid SQL syntax
            line = getattr(e, "line", None)
            col = getattr(e, "col", None)
            raise ParseError(f"Syntax error parsing SQL: {e}", line=line, column=col) from e

        # Handle generic Command node from sqlglot
        if isinstance(root_tree, exp.Command):
            return self._fallback_parse(
                primary_sql=primary_sql,
                cleaned_sql=cleaned_sql,
                comments=comments,
                flags=flags,
                warning_msg="Fallback parsing used for generic Command AST node",
            )

        # Check depth
        depth = self._calculate_depth(root_tree)
        if depth > self.max_nesting_depth:
            raise ParseError(
                f"Query nesting depth ({depth}) exceeds limit ({self.max_nesting_depth})"
            )

        # 6. View unwrapping (CREATE VIEW / CREATE OR ALTER VIEW / ALTER VIEW)
        if (
            isinstance(root_tree, exp.Create)
            and (root_tree.args.get("kind") or "").upper() == "VIEW"
        ):
            is_replace = bool(root_tree.args.get("replace"))
            statement_type = "CREATE_OR_ALTER_VIEW" if is_replace else "CREATE_VIEW"
            view_schema, view_name = self._extract_schema_and_name(root_tree.this)
            inner_tree = root_tree.expression
            wrapper_hash = hashlib.sha256(cleaned_sql.encode("utf-8")).hexdigest()
        elif (
            isinstance(root_tree, exp.Alter)
            and (root_tree.args.get("kind") or "").upper() == "VIEW"
        ):
            statement_type = "ALTER_VIEW"
            view_schema, view_name = self._extract_schema_and_name(root_tree.this)
            actions = root_tree.args.get("actions") or []
            inner_tree = actions[0] if actions else root_tree.expression
            wrapper_hash = hashlib.sha256(cleaned_sql.encode("utf-8")).hexdigest()
        elif (
            isinstance(root_tree, exp.Create)
            and (root_tree.args.get("kind") or "").upper() == "PROCEDURE"
        ):
            return self._fallback_parse(
                primary_sql=primary_sql,
                cleaned_sql=cleaned_sql,
                comments=comments,
                flags=flags,
                warning_msg="Fallback parsing used for stored procedure DDL",
            )
        else:
            inner_tree = root_tree
            statement_type = self._determine_statement_type(root_tree)

        if inner_tree is None:
            inner_tree = root_tree

        assert inner_tree is not None

        view_naming = self.naming_engine.parse_name(view_name, is_view=True) if view_name else None

        inner_sql = inner_tree.sql(dialect="tsql")
        query_hash = hashlib.sha256(inner_sql.encode("utf-8")).hexdigest()

        # 7. Extract CTEs
        ctes, recursive_found = self._extract_ctes(inner_tree)
        if recursive_found:
            flags.append("RECURSIVE_CTE")

        cte_names = {c.name.upper() for c in ctes}

        # 8. Extract Tables
        tables = self._extract_tables(inner_tree, cte_names)
        driving_table = tables[0].raw_name if tables else None

        # 9. Extract Joins
        joins = self._extract_joins(inner_tree)

        # 10. Extract Columns
        columns = self._extract_columns(inner_tree)

        # 11. Extract Filters and Scope Filters
        filters, scope_filters = self._extract_filters(inner_tree)

        # 12. Extract Group By, Order By, Aggregations, Window Functions, Set Ops, Top
        group_by = [e.sql("tsql") for e in inner_tree.find_all(exp.Group)]
        order_by = [e.sql("tsql") for e in inner_tree.find_all(exp.Order)]
        aggregations = self._extract_aggregations(inner_tree)
        window_functions = self._extract_window_functions(inner_tree)
        set_operations = self._extract_set_operations(inner_tree)
        subqueries = self._extract_subqueries(inner_tree)
        top_limit = self._extract_top_limit(inner_tree)

        # 13. Extract Update Targets (<expression> AS <FIELD>NEW)
        update_targets = self._extract_update_targets(inner_tree)

        # 14. Complexity & Risk
        complexity, risk_flags = evaluate_complexity_and_risk(
            expression=inner_tree,
            raw_sql=primary_sql,
            tables=tables,
            joins=joins,
            filters=filters,
            ctes=ctes,
            window_functions=window_functions,
            set_operations=set_operations,
            statement_count=len(statements),
        )

        # 15. Technical Slices (SQL Part and SAP Part)
        sql_part = self._build_sql_part(
            statement_type=statement_type,
            driving_table=driving_table,
            tables=tables,
            columns=columns,
            joins=joins,
            filters=filters,
            scope_filters=scope_filters,
            group_by=group_by,
            order_by=order_by,
            aggregations=aggregations,
            window_functions=window_functions,
            ctes=ctes,
            set_operations=set_operations,
            top_limit=top_limit,
            update_targets=update_targets,
            complexity=complexity,
            risk_flags=risk_flags,
            normalized_sql=cleaned_sql,
        )

        sap_part = self._build_sap_part(
            tables=tables,
            columns=columns,
            filters=filters,
            update_targets=update_targets,
        )

        return ParsedQuery(
            statement_type=statement_type,
            view_name=view_name,
            view_schema=view_schema,
            view_naming=view_naming,
            driving_table=driving_table,
            tables=tables,
            columns=columns,
            joins=joins,
            filters=filters,
            scope_filters=scope_filters,
            group_by=group_by,
            order_by=order_by,
            aggregations=aggregations,
            window_functions=window_functions,
            ctes=ctes,
            subqueries=subqueries,
            set_operations=set_operations,
            top_limit=top_limit,
            update_targets=update_targets,
            comments=comments,
            flags=flags,
            normalized_sql=cleaned_sql,
            query_hash=query_hash,
            wrapper_hash=wrapper_hash,
            parse_quality="full",
            warnings=warnings,
            ast_json={"type": type(inner_tree).__name__},
            sql_part=sql_part,
            sap_part=sap_part,
            complexity=complexity,
            risk_flags=risk_flags,
        )

    # --------------------------------------------------------------------------
    # Helper Methods
    # --------------------------------------------------------------------------

    @staticmethod
    def _extract_and_strip_comments(sql: str) -> tuple[str, list[str]]:
        """Removes comments from SQL safely without touching comments inside strings/brackets."""
        comments: list[str] = []
        pos = 0
        n = len(sql)
        out: list[str] = []

        while pos < n:
            # Check string literal '...'
            if sql[pos] == "'":
                start = pos
                pos += 1
                while pos < n:
                    if sql[pos] == "'":
                        if pos + 1 < n and sql[pos + 1] == "'":
                            pos += 2
                        else:
                            pos += 1
                            break
                    else:
                        pos += 1
                out.append(sql[start:pos])
                continue

            # Check bracketed identifier [...]
            if sql[pos] == "[":
                start = pos
                pos += 1
                while pos < n and sql[pos] != "]":
                    pos += 1
                if pos < n and sql[pos] == "]":
                    pos += 1
                out.append(sql[start:pos])
                continue

            # Check block comment /* ... */
            if pos + 1 < n and sql[pos : pos + 2] == "/*":
                start = pos
                pos += 2
                while pos + 1 < n and sql[pos : pos + 2] != "*/":
                    pos += 1
                if pos + 1 < n:
                    pos += 2
                comment_text = sql[start:pos]
                comments.append(comment_text)
                out.append(" ")
                continue

            # Check line comment -- ...
            if pos + 1 < n and sql[pos : pos + 2] == "--":
                start = pos
                while pos < n and sql[pos] not in ("\r", "\n"):
                    pos += 1
                comment_text = sql[start:pos]
                comments.append(comment_text)
                out.append("\n")
                continue

            out.append(sql[pos])
            pos += 1

        cleaned = "".join(out)
        # Normalize excessive whitespace but preserve lines
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
        return cleaned, comments

    @staticmethod
    def _normalize_single_bracket_quirk(sql: str) -> tuple[str, bool]:
        """Normalizes [TABLE.COLUMN] into [TABLE].[COLUMN]."""
        pattern = re.compile(r"\[([A-Za-z0-9_#]+)\.([A-Za-z0-9_#]+)\]")
        if pattern.search(sql):
            normalized = pattern.sub(r"[\1].[\2]", sql)
            return normalized, True
        return sql, False

    @staticmethod
    def _split_batches(sql: str) -> list[str]:
        """Splits SQL on GO batch separators and major statement boundaries."""
        # Split on GO lines
        go_splits = re.split(r"(?im)^\s*GO\s*$", sql)
        statements: list[str] = []
        for chunk in go_splits:
            chunk = chunk.strip()
            if chunk:
                # Also split by semicolon if multiple statements
                semi_splits = [s.strip() for s in re.split(r";\s*", chunk) if s.strip()]
                statements.extend(semi_splits if semi_splits else [chunk])
        return statements or [sql]

    @staticmethod
    def _calculate_depth(node: exp.Expression) -> int:
        """Calculates AST maximum nesting depth."""
        depth = 0
        current_level: list[exp.Expression] = [node]
        while current_level:
            depth += 1
            next_level: list[exp.Expression] = []
            for item in current_level:
                next_level.extend(item.iter_expressions())
            current_level = next_level
        return depth

    @staticmethod
    def _extract_schema_and_name(table_expr: Any) -> tuple[str | None, str | None]:
        """Extracts schema and object name from a Table or Identifier AST node."""
        if isinstance(table_expr, exp.Table):
            name = table_expr.name
            schema = table_expr.db if table_expr.db else None
            return schema, name
        if isinstance(table_expr, exp.Identifier):
            return None, table_expr.this
        return None, str(table_expr) if table_expr else None

    @staticmethod
    def _determine_statement_type(tree: exp.Expression) -> str:
        """Determines statement type from root AST expression."""
        if isinstance(tree, exp.Select):
            if tree.args.get("into"):
                return "SELECT_INTO"
            return "SELECT"
        if isinstance(tree, exp.Insert):
            return "INSERT"
        if isinstance(tree, exp.Update):
            return "UPDATE"
        if isinstance(tree, exp.Delete):
            return "DELETE"
        if isinstance(tree, exp.Merge):
            return "MERGE"
        if isinstance(tree, exp.Union):
            return "UNION"
        if isinstance(tree, exp.Except):
            return "EXCEPT"
        if isinstance(tree, exp.Intersect):
            return "INTERSECT"
        return type(tree).__name__.upper()

    def _extract_ctes(self, tree: exp.Expression) -> tuple[list[CTEInfo], bool]:
        """Extracts common table expressions and checks for recursion."""
        ctes: list[CTEInfo] = []
        recursive_found = False
        with_clause = tree.find(exp.With)
        if with_clause:
            is_recursive_flag = bool(with_clause.args.get("recursive"))
            for cte_expr in with_clause.expressions:
                alias = cte_expr.alias
                columns: list[str] = []
                alias_obj = cte_expr.args.get("alias")
                if alias_obj and hasattr(alias_obj, "args"):
                    cols = alias_obj.args.get("columns") or []
                    columns = [c.name for c in cols if hasattr(c, "name")]

                cte_body = cte_expr.this
                is_recursive = is_recursive_flag
                if cte_body and alias:
                    for t in cte_body.find_all(exp.Table):
                        if t.name and t.name.upper() == alias.upper():
                            is_recursive = True
                            break
                if is_recursive:
                    recursive_found = True
                ctes.append(CTEInfo(name=alias, columns=columns, recursive=is_recursive))
        return ctes, recursive_found

    def _extract_tables(self, tree: exp.Expression, cte_names: set[str]) -> list[TableInfo]:
        """Extracts all table references with catalog and naming metadata."""
        tables_seen: dict[str, TableInfo] = {}
        for t in tree.find_all(exp.Table):
            raw_name = t.name
            if not raw_name:
                continue

            alias = t.alias if t.alias else None
            schema_name = t.db if t.db else None
            database = t.catalog if t.catalog else None

            # Detect temp table (#t or ##t)
            is_global = (
                bool(getattr(getattr(t, "this", None), "args", {}).get("global_"))
                or raw_name.startswith("##")
                or t.sql("tsql").startswith("##")
            )
            is_temp = (
                is_global
                or bool(getattr(getattr(t, "this", None), "args", {}).get("temporary"))
                or raw_name.startswith("#")
                or t.sql("tsql").startswith("#")
            )
            if is_temp and not raw_name.startswith("#"):
                raw_name = f"##{raw_name}" if is_global else f"#{raw_name}"

            is_cte = raw_name.upper() in cte_names

            # Naming engine evaluation
            naming_res = (
                self.naming_engine.parse_name(raw_name, is_view=False)
                if not (is_cte or is_temp)
                else None
            )

            # SAP catalog resolution
            sap_table, _, _ = self.sap_catalog.resolve_sap_table(
                raw_name=raw_name,
                naming_sap_table=naming_res.sap_table if naming_res else None,
                is_cte=is_cte,
                is_temp=is_temp,
            )

            role = self.sap_catalog.determine_role(
                raw_name=raw_name,
                naming_kind=naming_res.object_kind if naming_res else None,
                is_cte=is_cte,
                is_temp=is_temp,
                sap_table=sap_table,
            )

            key = f"{schema_name}.{raw_name}" if schema_name else raw_name
            if key not in tables_seen:
                tables_seen[key] = TableInfo(
                    raw_name=raw_name,
                    alias=alias,
                    schema=schema_name,
                    database=database,
                    role=role,  # type: ignore[arg-type]
                    sap_table=sap_table,
                    naming=naming_res,
                )

        return list(tables_seen.values())

    @staticmethod
    def _extract_joins(tree: exp.Expression) -> list[JoinInfo]:
        """Extracts join relationships and on-clause field conditions."""
        joins: list[JoinInfo] = []
        for j in tree.find_all(exp.Join):
            kind = (j.args.get("kind") or "INNER").upper()
            this_table = j.this.name if hasattr(j.this, "name") else str(j.this)

            # Left table heuristic from parent FROM or preceding join
            left_table = None
            parent_from = tree.find(exp.From)
            if parent_from and hasattr(parent_from.this, "name"):
                left_table = parent_from.this.name

            condition = j.args.get("on")
            cond_str = condition.sql("tsql") if condition else None

            left_field = None
            right_field = None
            if condition and isinstance(condition, exp.EQ):
                left_field = getattr(condition.left, "name", None) or str(condition.left)
                right_field = getattr(condition.right, "name", None) or str(condition.right)

            joins.append(
                JoinInfo(
                    type=kind,
                    left_table=left_table,
                    right_table=this_table,
                    condition=cond_str,
                    left_field=left_field,
                    right_field=right_field,
                )
            )
        return joins

    @staticmethod
    def _extract_columns(tree: exp.Expression) -> list[ColumnInfo]:
        """Extracts projected and referenced columns and flags custom fields."""
        columns_dict: dict[str, ColumnInfo] = {}

        # 1. Projected columns in SELECT
        select_node = tree.find(exp.Select)
        if select_node:
            for s in select_node.expressions:
                if isinstance(s, exp.Column):
                    col_name = s.name
                    tbl_name = s.table if s.table else None
                    is_custom = col_name.upper().startswith(("Z", "Y"))
                    columns_dict[f"{tbl_name}.{col_name}"] = ColumnInfo(
                        table=tbl_name,
                        column=col_name,
                        usage="select",
                        is_custom=is_custom,
                    )
                elif isinstance(s, exp.Alias):
                    col_name = s.alias
                    is_custom = col_name.upper().startswith(("Z", "Y"))
                    columns_dict[f"derived.{col_name}"] = ColumnInfo(
                        table=None,
                        column=col_name,
                        usage="derived",
                        is_custom=is_custom,
                    )
                    # Also collect any underlying columns referenced inside this expression
                    for inner_col in s.this.find_all(exp.Column):
                        in_name = inner_col.name
                        in_tbl = inner_col.table if inner_col.table else None
                        in_custom = in_name.upper().startswith(("Z", "Y"))
                        columns_dict[f"{in_tbl}.{in_name}"] = ColumnInfo(
                            table=in_tbl,
                            column=in_name,
                            usage="select",
                            is_custom=in_custom,
                        )

        # 2. Columns in WHERE
        where_node = tree.find(exp.Where)
        if where_node:
            for c in where_node.find_all(exp.Column):
                key = f"{c.table}.{c.name}"
                if key not in columns_dict:
                    is_custom = c.name.upper().startswith(("Z", "Y"))
                    columns_dict[key] = ColumnInfo(
                        table=c.table if c.table else None,
                        column=c.name,
                        usage="where",
                        is_custom=is_custom,
                    )

        # 3. Columns in JOINs
        for j in tree.find_all(exp.Join):
            on = j.args.get("on")
            if on:
                for c in on.find_all(exp.Column):
                    key = f"{c.table}.{c.name}"
                    if key not in columns_dict:
                        is_custom = c.name.upper().startswith(("Z", "Y"))
                        columns_dict[key] = ColumnInfo(
                            table=c.table if c.table else None,
                            column=c.name,
                            usage="join",
                            is_custom=is_custom,
                        )

        return list(columns_dict.values())

    @staticmethod
    def _extract_filters(tree: exp.Expression) -> tuple[list[str], list[str]]:
        """Extracts WHERE conditions and identifies scope filters (e.g. ZACTIVE = 1)."""
        filters: list[str] = []
        scope_filters: list[str] = []
        where_node = tree.find(exp.Where)
        if not where_node or not where_node.this:
            return filters, scope_filters

        # Split top-level ANDs
        def collect_conjuncts(expr: Any) -> list[str]:
            if isinstance(expr, exp.And):
                return collect_conjuncts(expr.left) + collect_conjuncts(expr.right)
            return [expr.sql("tsql") if hasattr(expr, "sql") else str(expr)]

        conjuncts = collect_conjuncts(where_node.this)
        for conj in conjuncts:
            filters.append(conj)
            upper_conj = conj.upper()
            if any(
                token in upper_conj
                for token in (
                    "ZACTIVE",
                    "MANUAL_TARGET_INSERT",
                    "LOEVM",
                    "BU_GROUP",
                    "ZWERKS IN",
                    "WERKS IN",
                )
            ):
                scope_filters.append(conj)

        return filters, scope_filters

    @staticmethod
    def _extract_aggregations(tree: exp.Expression) -> list[str]:
        """Extracts aggregation expressions."""
        aggs: list[str] = []
        for a in tree.find_all(exp.AggFunc):
            aggs.append(a.sql("tsql"))
        return aggs

    @staticmethod
    def _extract_window_functions(tree: exp.Expression) -> list[str]:
        """Extracts window function expressions."""
        wins: list[str] = []
        for w in tree.find_all(exp.Window):
            wins.append(w.sql("tsql"))
        return wins

    @staticmethod
    def _extract_set_operations(tree: exp.Expression) -> list[str]:
        """Extracts set operation types (UNION, EXCEPT, INTERSECT)."""
        set_ops: list[str] = []
        for node in tree.walk():
            if isinstance(node, (exp.Union, exp.Except, exp.Intersect)):
                set_ops.append(type(node).__name__.upper())
        return set_ops

    @staticmethod
    def _extract_subqueries(tree: exp.Expression) -> list[str]:
        """Extracts nested subqueries."""
        subqueries: list[str] = []
        for s in tree.find_all(exp.Select):
            if s != tree:
                subqueries.append(s.sql("tsql"))
        return subqueries

    @staticmethod
    def _extract_top_limit(tree: exp.Expression) -> str | None:
        """Extracts TOP N or LIMIT / FETCH clause."""
        limit_node = tree.find(exp.Limit)
        if limit_node:
            return limit_node.sql("tsql")
        fetch_node = tree.find(exp.Fetch)
        if fetch_node:
            return fetch_node.sql("tsql")
        return None

    @staticmethod
    def _extract_update_targets(tree: exp.Expression) -> list[UpdateTarget]:
        """Extracts derived rule update target columns ending in 'NEW'."""
        targets: list[UpdateTarget] = []
        for alias_node in tree.find_all(exp.Alias):
            alias_name = alias_node.alias
            if alias_name and alias_name.upper().endswith("NEW") and len(alias_name) > 3:
                base_field = alias_name[:-3]
                inner_expr = alias_node.this
                expr_sql = inner_expr.sql("tsql")

                # Determine expression kind
                if isinstance(inner_expr, exp.Case):
                    expr_kind = "case"
                elif isinstance(inner_expr, exp.Literal):
                    expr_kind = "constant"
                elif isinstance(inner_expr, (exp.Add, exp.Concat)):
                    expr_kind = "concat"
                elif isinstance(inner_expr, (exp.Func, exp.Anonymous, exp.Substring, exp.Upper)):
                    expr_kind = "function"
                else:
                    expr_kind = "lookup"

                # Extract source fields (deduplicated while preserving order)
                source_fields = list(dict.fromkeys(c.name for c in inner_expr.find_all(exp.Column)))

                # Rule kind: source updates a Z-field, target updates a standard target field
                rule_kind = "source" if base_field.upper().startswith(("Z", "Y")) else "target"

                targets.append(
                    UpdateTarget(
                        field=base_field,
                        new_alias=alias_name,
                        expression=expr_sql,
                        expression_kind=expr_kind,  # type: ignore[arg-type]
                        source_fields=source_fields,
                        rule_kind=rule_kind,  # type: ignore[arg-type]
                    )
                )
        return targets

    def _fallback_parse(
        self,
        primary_sql: str,
        cleaned_sql: str,
        comments: list[str],
        flags: list[str],
        warning_msg: str,
    ) -> ParsedQuery:
        """Degraded regex-based extraction when sqlglot cannot produce a full AST."""
        logger.warning("Degraded fallback parsing: %s", warning_msg)
        table_matches = re.findall(
            r"(?i)\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z0-9_.#]+)", cleaned_sql
        )
        tables: list[TableInfo] = []
        for t_name in set(table_matches):
            naming_res = self.naming_engine.parse_name(t_name)
            sap_table, _, _ = self.sap_catalog.resolve_sap_table(
                raw_name=t_name,
                naming_sap_table=naming_res.sap_table,
            )
            role = self.sap_catalog.determine_role(
                raw_name=t_name,
                naming_kind=naming_res.object_kind,
                sap_table=sap_table,
            )
            tables.append(
                TableInfo(
                    raw_name=t_name,
                    role=role,  # type: ignore[arg-type]
                    sap_table=sap_table,
                    naming=naming_res,
                )
            )

        query_hash = hashlib.sha256(cleaned_sql.encode("utf-8")).hexdigest()
        complexity = Complexity(score=5, level="medium", factors=["Degraded fallback parsing (+4)"])

        return ParsedQuery(
            statement_type="EXECUTE" if "EXEC" in cleaned_sql.upper() else "UNKNOWN",
            tables=tables,
            comments=comments,
            flags=flags,
            normalized_sql=cleaned_sql,
            query_hash=query_hash,
            parse_quality="fallback",
            warnings=[warning_msg],
            complexity=complexity,
            sql_part={
                "statement_type": "UNKNOWN",
                "tables": [t.raw_name for t in tables],
                "normalized_sql": cleaned_sql,
            },
            sap_part={
                "sap_tables": [t.sap_table for t in tables if t.sap_table],
            },
        )

    @staticmethod
    def _build_sql_part(
        statement_type: str,
        driving_table: str | None,
        tables: list[TableInfo],
        columns: list[ColumnInfo],
        joins: list[JoinInfo],
        filters: list[str],
        scope_filters: list[str],
        group_by: list[str],
        order_by: list[str],
        aggregations: list[str],
        window_functions: list[str],
        ctes: list[CTEInfo],
        set_operations: list[str],
        top_limit: str | None,
        update_targets: list[UpdateTarget],
        complexity: Complexity,
        risk_flags: list[RiskFlag],
        normalized_sql: str,
    ) -> dict[str, Any]:
        """Sterilized technical SQL slice for the SQL Agent (untrusted comments stripped)."""
        return {
            "statement_type": statement_type,
            "driving_table": driving_table,
            "tables": [t.raw_name for t in tables],
            "columns": [c.column for c in columns],
            "joins": [j.model_dump() for j in joins],
            "filters": filters,
            "scope_filters": scope_filters,
            "group_by": group_by,
            "order_by": order_by,
            "aggregations": aggregations,
            "window_functions": window_functions,
            "ctes": [c.model_dump() for c in ctes],
            "set_operations": set_operations,
            "top_limit": top_limit,
            "update_targets": [u.model_dump() for u in update_targets],
            "complexity": complexity.model_dump(),
            "risk_flags": [r.model_dump() for r in risk_flags],
            "normalized_sql": normalized_sql,
        }

    @staticmethod
    def _build_sap_part(
        tables: list[TableInfo],
        columns: list[ColumnInfo],
        filters: list[str],
        update_targets: list[UpdateTarget],
    ) -> dict[str, Any]:
        """SAP domain technical slice for the SAP Agent."""
        return {
            "sap_tables": [t.sap_table for t in tables if t.sap_table],
            "staging_tables": [t.raw_name for t in tables if t.role == "sap_staging"],
            "reference_tables": [
                t.raw_name for t in tables if t.role in ("mapping_reference", "external_source")
            ],
            "custom_fields": [c.column for c in columns if c.is_custom],
            "mandt_handled": any("MANDT" in (f or "").upper() for f in filters),
            "target_updates": [u.model_dump() for u in update_targets],
            "prefixes": list({t.naming.prefix for t in tables if t.naming and t.naming.prefix}),
            "zsources": list({t.naming.zsource for t in tables if t.naming and t.naming.zsource}),
        }
