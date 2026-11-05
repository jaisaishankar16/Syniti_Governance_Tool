"""Syniti Migrate naming convention engine (data-driven)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

from .models import NamingResult, NamingViolation

logger = logging.getLogger(__name__)


class SynitiNamingEngine:
    """Evaluates and parses table, view, column, and database identifiers according to Syniti Migrate conventions."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config = self._load_config(config_path)
        self.universal_rules = self.config.get("universal_rules", {})
        self.migration_prefixes = self.config.get("migration_prefixes", {})
        self.subject_area_codes = self.config.get("subject_area_codes", {})
        self.known_zsources = sorted(self.config.get("known_zsources", []), key=len, reverse=True)
        self.known_actions = set(self.config.get("known_actions", ["UPD"]))
        self.special_view_tokens = self.config.get(
            "special_view_tokens", {"report": "RPT", "export": "EXP"}
        )
        self.reference_keywords = self.config.get(
            "reference_keywords", ["XREF", "MAPPING", "SIEVO", "DATADUMP"]
        )
        self.db_prefixes = self.config.get(
            "db_prefixes", {"working": "WRK", "source_snapshot": "SRC", "target_snapshot": "TGT"}
        )
        self.environments = set(self.config.get("environments", ["D", "Q", "T", "P"]))

    @staticmethod
    def _resolve_config_path(config_path: Path | str | None) -> Path | None:
        if config_path:
            return Path(config_path)
        current_dir = Path(__file__).resolve().parent
        for candidate in [
            current_dir.parent.parent.parent / "knowledge" / "naming_conventions.json",
            current_dir.parent.parent / "knowledge" / "naming_conventions.json",
            Path("knowledge/naming_conventions.json"),
        ]:
            if candidate.exists():
                return candidate
        return None

    def _load_config(self, config_path: Path | str | None) -> dict[str, Any]:
        path = self._resolve_config_path(config_path)
        if path and path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Failed to read naming_conventions.json from %s: %s", path, e)
        return {
            "universal_rules": {
                "uppercase": True,
                "allowed_characters_regex": "^[A-Z0-9_]+$",
                "max_length": 256,
            },
            "migration_prefixes": {
                "MM": "Material Master",
                "CM": "Customer Master",
                "VM": "Vendor Master",
            },
            "subject_area_codes": {"PTP": "Procure to Pay"},
            "known_zsources": ["ECC_EU_R1", "NORTHWIND", "ADVENTUREWORKS"],
            "known_actions": ["UPD"],
            "special_view_tokens": {"report": "RPT", "export": "EXP"},
            "reference_keywords": ["XREF", "MAPPING", "SIEVO", "DATADUMP"],
            "db_prefixes": {"working": "WRK", "source_snapshot": "SRC", "target_snapshot": "TGT"},
            "environments": ["D", "Q", "T", "P"],
        }

    def check_universal_rules(self, name: str, is_column: bool = False) -> list[NamingViolation]:
        """Validate universal rules: uppercase, no spaces/special chars, max length 256."""
        violations: list[NamingViolation] = []
        raw_clean = name.strip("[]")

        # Check length
        max_len = self.universal_rules.get("max_length", 256)
        if len(raw_clean) > max_len:
            violations.append(
                NamingViolation(
                    code="TOO_LONG",
                    severity="warning",
                    message=f"Identifier length ({len(raw_clean)}) exceeds maximum allowed ({max_len})",
                )
            )

        # Check lowercase
        if any(c.islower() for c in raw_clean):
            violations.append(
                NamingViolation(
                    code="LOWERCASE",
                    severity="warning",
                    message=f"Identifier '{raw_clean}' contains lowercase characters; Syniti standard requires uppercase",
                )
            )

        # Check special characters and spaces
        if " " in raw_clean or not re.match(r"^[A-Za-z0-9_]+$", raw_clean):
            severity: Literal["error", "warning", "info"] = (
                "info" if is_column and " " in raw_clean else "warning"
            )
            violations.append(
                NamingViolation(
                    code="SPECIAL_CHAR_OR_SPACE",
                    severity=severity,
                    message=f"Identifier '{raw_clean}' contains spaces or special characters",
                )
            )

        return violations

    def parse_name(self, raw_name: str, is_view: bool = False) -> NamingResult:
        """Parses a table or view name according to Syniti naming rules."""
        clean_name = raw_name.strip("[]").split(".")[-1].strip()
        violations = self.check_universal_rules(clean_name, is_column=False)
        upper_name = clean_name.upper()

        # Check database naming if it starts with DB prefixes
        wrk_pfx = self.db_prefixes.get("working", "WRK")
        src_pfx = self.db_prefixes.get("source_snapshot", "SRC")
        tgt_pfx = self.db_prefixes.get("target_snapshot", "TGT")

        if clean_name.startswith((wrk_pfx, src_pfx, tgt_pfx)):
            return self._parse_database_name(clean_name, violations)

        # Check reference keywords first: XREF, MAPPING, SIEVO, DATADUMP
        # Names that match no pattern but contain a reference keyword are mapping_reference or external_source, not violations.
        for ref_kw in self.reference_keywords:
            if ref_kw in upper_name:
                tokens = upper_name.split("_")
                prefix = tokens[0] if tokens[0] in self.migration_prefixes else None
                kind: Literal["external_source", "mapping_reference"] = (
                    "external_source" if "DATADUMP" in upper_name else "mapping_reference"
                )
                return NamingResult(
                    object_kind=kind,
                    prefix=prefix,
                    dataset=f"{prefix}_{tokens[1]}" if prefix and len(tokens) > 1 else None,
                    compliant=len([v for v in violations if v.severity in ("error", "warning")])
                    == 0,
                    violations=violations,
                    confidence=1.0,
                )

        tokens = clean_name.split("_")
        upper_tokens = upper_name.split("_")

        # 1. Rule View, Report View, or Export View
        # Standard: Find end of working table as first token equal to 'S' or 'T' at index >= 2
        st_index = -1
        for idx in range(2, len(upper_tokens)):
            if upper_tokens[idx] in ("S", "T"):
                st_index = idx
                break

        # If it's a view, or st_index >= 2 and there are tokens after st_index
        if st_index != -1 and st_index < len(upper_tokens) - 1:
            return self._parse_view_name(
                clean_name=clean_name,
                upper_name=upper_name,
                upper_tokens=upper_tokens,
                tokens=tokens,
                st_index=st_index,
                violations=violations,
            )

        # 2. Target Staging Table: <PREFIX>_<SAP_TABLE>_T
        if len(upper_tokens) >= 3 and upper_tokens[-1] == "T":
            prefix = upper_tokens[0]
            sap_table = upper_tokens[1]
            if prefix not in self.migration_prefixes:
                violations.append(
                    NamingViolation(
                        code="UNKNOWN_PREFIX",
                        severity="warning",
                        message=f"Prefix '{prefix}' is not in known migration prefixes {list(self.migration_prefixes.keys())}",
                    )
                )
            return NamingResult(
                object_kind="target_staging",
                prefix=prefix,
                dataset=f"{prefix}_{sap_table}",
                sap_table=sap_table,
                compliant=len([v for v in violations if v.severity in ("error", "warning")]) == 0,
                violations=violations,
                confidence=1.0,
            )

        # 3. Source Staging Table: <PREFIX>_<SAP_TABLE>_<ZSOURCE>_<SOURCE_TABLE>_S
        if len(upper_tokens) >= 4 and upper_tokens[-1] == "S":
            prefix = upper_tokens[0]
            sap_table = upper_tokens[1]
            if prefix not in self.migration_prefixes:
                violations.append(
                    NamingViolation(
                        code="UNKNOWN_PREFIX",
                        severity="warning",
                        message=f"Prefix '{prefix}' is not in known migration prefixes {list(self.migration_prefixes.keys())}",
                    )
                )

            # Middle tokens between sap_table and trailing '_S'
            # e.g. for MM_MARC_ECC_EU_R1_MARC_S -> tokens[2:-1] = ['ECC', 'EU', 'R1', 'MARC']
            middle_tokens = upper_tokens[2:-1]
            middle_str = "_".join(middle_tokens)

            zsource = None
            source_table = None
            confidence = 1.0

            # Match against known_zsources (longest match first)
            for known_z in self.known_zsources:
                if middle_str.startswith(known_z + "_"):
                    zsource = known_z
                    source_table = middle_str[len(known_z) + 1 :]
                    break
                elif middle_str == known_z:
                    zsource = known_z
                    source_table = known_z
                    break

            # Else fallback heuristic: last token before _S is source_table, the rest is zsource
            if not zsource and middle_tokens:
                source_table = middle_tokens[-1]
                zsource = "_".join(middle_tokens[:-1]) if len(middle_tokens) > 1 else source_table
                confidence = 0.7  # Heuristic lower confidence

            return NamingResult(
                object_kind="source_staging",
                prefix=prefix,
                dataset=f"{prefix}_{sap_table}",
                sap_table=sap_table,
                zsource=zsource,
                source_table=source_table,
                compliant=len([v for v in violations if v.severity in ("error", "warning")]) == 0,
                violations=violations,
                confidence=confidence,
            )

        # If it looks like a staging table candidate (starts with a known prefix) but lacks _S or _T suffix
        if len(upper_tokens) >= 2 and upper_tokens[0] in self.migration_prefixes:
            if not is_view and upper_tokens[-1] not in ("S", "T"):
                violations.append(
                    NamingViolation(
                        code="MISSING_SUFFIX",
                        severity="warning",
                        message=f"Staging table '{clean_name}' is missing standard suffix '_S' or '_T'",
                    )
                )
            return NamingResult(
                object_kind="unknown",
                prefix=upper_tokens[0],
                dataset=f"{upper_tokens[0]}_{upper_tokens[1]}",
                sap_table=upper_tokens[1],
                compliant=False,
                violations=violations,
                confidence=0.5,
            )

        # Unknown / non-standard
        return NamingResult(
            object_kind="unknown",
            compliant=len([v for v in violations if v.severity in ("error", "warning")]) == 0,
            violations=violations,
            confidence=0.2,
        )

    def _parse_view_name(
        self,
        clean_name: str,
        upper_name: str,
        upper_tokens: list[str],
        tokens: list[str],
        st_index: int,
        violations: list[NamingViolation],
    ) -> NamingResult:
        """Parses rule views, report views, and export views."""
        working_table_tokens = tokens[: st_index + 1]
        working_table = "_".join(working_table_tokens)
        view_suffix_tokens = tokens[st_index + 1 :]
        view_suffix_upper = upper_tokens[st_index + 1 :]

        # Parse the underlying working table
        working_table_res = self.parse_name(working_table, is_view=False)
        prefix = working_table_res.prefix
        sap_table = working_table_res.sap_table
        zsource = working_table_res.zsource
        source_table = working_table_res.source_table

        # Check special tokens
        rpt_token = self.special_view_tokens.get("report", "RPT")
        exp_token = self.special_view_tokens.get("export", "EXP")

        # 1. Report View: <WORKING_TABLE>_RPT(_<FIELD>)(_<DESCRIPTION>)
        if view_suffix_upper[0] == rpt_token:
            field = view_suffix_tokens[1] if len(view_suffix_tokens) > 1 else None
            description = "_".join(view_suffix_tokens[2:]) if len(view_suffix_tokens) > 2 else None
            return NamingResult(
                object_kind="report_view",
                prefix=prefix,
                dataset=working_table_res.dataset,
                sap_table=sap_table,
                zsource=zsource,
                source_table=source_table,
                field=field,
                description=description,
                working_table=working_table,
                compliant=len([v for v in violations if v.severity in ("error", "warning")]) == 0,
                violations=violations,
                confidence=1.0,
            )

        # 2. Export View: <TARGET_TABLE>_EXP(_<DESCRIPTION>)
        if view_suffix_upper[0] == exp_token:
            description = "_".join(view_suffix_tokens[1:]) if len(view_suffix_tokens) > 1 else None
            return NamingResult(
                object_kind="export_view",
                prefix=prefix,
                dataset=working_table_res.dataset,
                sap_table=sap_table,
                description=description,
                working_table=working_table,
                compliant=len([v for v in violations if v.severity in ("error", "warning")]) == 0,
                violations=violations,
                confidence=1.0,
            )

        # 3. Rule View: <WORKING_TABLE>_<ACTION>(_<FIELD>)(_<DESCRIPTION>)
        # Check if an action appears in view_suffix_upper
        action_idx = -1
        action = None
        for idx, tok in enumerate(view_suffix_upper):
            if tok in self.known_actions:
                action_idx = idx
                action = tok
                break

        if action_idx == 0:
            # Standard order: ACTION is first suffix token
            field = view_suffix_tokens[1] if len(view_suffix_tokens) > 1 else None
            description = "_".join(view_suffix_tokens[2:]) if len(view_suffix_tokens) > 2 else None
        elif action_idx > 0:
            # ACTION_ORDER violation: token(s) appear before action (e.g. FLVN00_UPD_ZACTIVE)
            before_tokens = view_suffix_tokens[:action_idx]
            after_tokens = view_suffix_tokens[action_idx + 1 :]
            description = "_".join(before_tokens)
            field = after_tokens[0] if after_tokens else None
            if len(after_tokens) > 1:
                description = f"{description}_{'_'.join(after_tokens[1:])}"

            violations.append(
                NamingViolation(
                    code="ACTION_ORDER",
                    severity="warning",
                    message=(
                        f"Action '{action}' is preceded by descriptive token(s) '{'_'.join(before_tokens)}'; "
                        f"Syniti standard requires <WORKING_TABLE>_<ACTION>(_<FIELD>)(_<DESCRIPTION>)"
                    ),
                )
            )
        else:
            # Action not in known_actions
            action = view_suffix_upper[0]
            field = view_suffix_tokens[1] if len(view_suffix_tokens) > 1 else None
            description = "_".join(view_suffix_tokens[2:]) if len(view_suffix_tokens) > 2 else None
            violations.append(
                NamingViolation(
                    code="UNKNOWN_ACTION",
                    severity="warning",
                    message=f"Action '{action}' is not in known actions {list(self.known_actions)}",
                )
            )

        return NamingResult(
            object_kind="rule_view",
            prefix=prefix,
            dataset=working_table_res.dataset,
            sap_table=sap_table,
            zsource=zsource,
            source_table=source_table,
            action=action,
            field=field,
            description=description,
            working_table=working_table,
            compliant=len([v for v in violations if v.severity in ("error", "warning")]) == 0,
            violations=violations,
            confidence=working_table_res.confidence,
        )

    def _parse_database_name(
        self, clean_name: str, violations: list[NamingViolation]
    ) -> NamingResult:
        """Parses 3-part database prefix names like WRKPTP, WRKPTP01, WRKPTP01D, SRC..., TGT..."""
        upper = clean_name.upper()
        wrk_pfx = self.db_prefixes.get("working", "WRK")

        if upper.startswith(wrk_pfx):
            remainder = upper[len(wrk_pfx) :]
            # Match Subject Area (e.g. PTP), optional Release (digits), optional Env (D, Q, T, P)
            match = re.match(r"^([A-Z]{3,4})(\d+)?([DQTP])?$", remainder)
            if match:
                subject_area, _release, _env = match.groups()
                return NamingResult(
                    object_kind="working_db",
                    prefix=wrk_pfx,
                    dataset=subject_area,
                    compliant=len(violations) == 0,
                    violations=violations,
                    confidence=1.0,
                )

        return NamingResult(
            object_kind="unknown",
            compliant=len(violations) == 0,
            violations=violations,
            confidence=0.5,
        )
