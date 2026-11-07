"""Live calibration script for calculating embedding similarity thresholds and L3 cache boundaries."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add backend to sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import numpy as np
from pydantic import TypeAdapter

from app.cache.similarity import mask_literals_and_aliases
from app.embeddings.embedder import get_embedder
from app.embeddings.models import (
    CalibrationFile,
    ModelCalibration,
    RetrievalEvalQuery,
)
from app.embeddings.settings import get_embedding_settings
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.settings import get_settings as get_knowledge_settings


def main() -> None:
    emb_settings = get_embedding_settings()
    k_settings = get_knowledge_settings()

    if not emb_settings.is_configured:
        print("=" * 80)
        print("                   EMBEDDING THRESHOLD CALIBRATOR")
        print("=" * 80)
        print("Error: No valid API key configured for embeddings.")
        print("Set OPENAI_API_KEY (or EMBEDDING_API_KEY) in .env before running calibrate_embeddings.")
        print("=" * 80)
        sys.exit(1)

    print("=" * 80)
    print("                   EMBEDDING THRESHOLD CALIBRATOR")
    print("=" * 80)
    print(f"Model:              {emb_settings.embedding_model}")
    print(f"Dimensions:         {emb_settings.embedding_dimensions or 'Model Default'}")
    print(f"Base Host:          {emb_settings.base_host}")
    print("=" * 80)

    # 1. Load eval set
    eval_path = k_settings.eval_queries_path
    if not eval_path.is_file():
        print(f"Error: Labelled queries file not found at {eval_path}")
        sys.exit(1)

    with open(eval_path, "r", encoding="utf-8") as f:
        eval_queries = TypeAdapter(list[RetrievalEvalQuery]).validate_json(f.read())

    sap_retriever = SapRetriever(settings=k_settings)
    sql_retriever = SqlDocRetriever(settings=k_settings)

    sap_pos_scores: list[float] = []
    sap_neg_scores: list[float] = []
    sql_pos_scores: list[float] = []
    sql_neg_scores: list[float] = []

    print("\nEvaluating Retrieval Queries against Indexes...")
    for eq in eval_queries:
        if eq.domain == "sap":
            res = sap_retriever.search(eq.query, k=5)
            scores = [h.score for h in res.hits if h.match_type.value == "semantic"]
            top_score = scores[0] if scores else 0.0
            if eq.is_negative:
                sap_neg_scores.append(top_score)
            else:
                sap_pos_scores.append(top_score)
        elif eq.domain == "sql":
            hits = sql_retriever.search(eq.query, k=5)
            top_score = hits[0].score if hits else 0.0
            if eq.is_negative:
                sql_neg_scores.append(top_score)
            else:
                sql_pos_scores.append(top_score)

    sap_min = round(float(np.min(sap_pos_scores)) * 0.9, 4) if sap_pos_scores else 0.45
    sql_min = round(float(np.min(sql_pos_scores)) * 0.9, 4) if sql_pos_scores else 0.40

    print(f"SAP Semantic Scores: pos_min={min(sap_pos_scores or [0]):.4f}, neg_max={max(sap_neg_scores or [0]):.4f} -> sap_min_score={sap_min}")
    print(f"SQL Semantic Scores: pos_min={min(sql_pos_scores or [0]):.4f}, neg_max={max(sql_neg_scores or [0]):.4f} -> sql_min_score={sql_min}")

    # 2. L3 Similarity Calibration using workbook examples
    examples_path = repo_root / "backend" / "tests" / "fixtures" / "examples.json"
    l3_reuse: float | None = 0.95
    l3_hint = 0.78
    positive_sims: list[float] = []
    negative_sims: list[float] = []

    if not examples_path.is_file():
        print(f"\nWorkbook examples not found at {examples_path}; skipping L3 pair scoring.")
    else:
        print("\nScoring SQL pairs for L3 similarity...")
        with open(examples_path, "r", encoding="utf-8") as f:
            examples = json.load(f)

        ex_by_sheet: dict[str, str] = {}
        for ex in examples:
            s_name = str(ex.get("source_sheet", "")).lower()
            sql = str(ex.get("input_sql", ""))
            ex_by_sheet[s_name] = mask_literals_and_aliases(sql)

        embedder = get_embedder(emb_settings)

        # Positive pairs: sheet2 vs sheet3, sheet9 vs sheet10
        pair_defs = [("sheet2", "sheet3"), ("sheet9", "sheet10")]
        for s1, s2 in pair_defs:
            if s1 in ex_by_sheet and s2 in ex_by_sheet:
                v1 = np.array(embedder.embed_query_sync(ex_by_sheet[s1]), dtype=np.float32)
                v2 = np.array(embedder.embed_query_sync(ex_by_sheet[s2]), dtype=np.float32)
                sim = float(np.dot(v1, v2))
                positive_sims.append(sim)

        # Negative pairs: different sheets
        neg_pairs = [("sheet1", "sheet5"), ("sheet2", "sheet8"), ("sheet4", "sheet11")]
        for s1, s2 in neg_pairs:
            if s1 in ex_by_sheet and s2 in ex_by_sheet:
                v1 = np.array(embedder.embed_query_sync(ex_by_sheet[s1]), dtype=np.float32)
                v2 = np.array(embedder.embed_query_sync(ex_by_sheet[s2]), dtype=np.float32)
                sim = float(np.dot(v1, v2))
                negative_sims.append(sim)

        if positive_sims and negative_sims:
            pos_min = min(positive_sims)
            neg_max = max(negative_sims)
            print(f"L3 Positive Pairs Min Sim: {pos_min:.4f}")
            print(f"L3 Negative Pairs Max Sim: {neg_max:.4f}")
            if pos_min > neg_max:
                l3_reuse = round(neg_max + 0.05, 4)
                l3_hint = round(neg_max - 0.05, 4)
            else:
                print("Warning: Positive and negative similarity distributions overlap; disabling L3 reuse.")
                l3_reuse = None
                l3_hint = round(neg_max, 4)

    calib_key = f"{emb_settings.embedding_model}_{emb_settings.embedding_dimensions}"
    model_calib = ModelCalibration(
        sap_min_score=sap_min,
        sql_min_score=sql_min,
        l3_hint_threshold=l3_hint,
        l3_reuse_threshold=l3_reuse,
        write_back_min_score=0.75,
        created_at=datetime.now(timezone.utc).isoformat(),
        metrics={
            "sap_positive_min": min(sap_pos_scores) if sap_pos_scores else None,
            "sap_negative_max": max(sap_neg_scores) if sap_neg_scores else None,
            "sql_positive_min": min(sql_pos_scores) if sql_pos_scores else None,
            "sql_negative_max": max(sql_neg_scores) if sql_neg_scores else None,
            "l3_positive_min": min(positive_sims) if positive_sims else None,
            "l3_negative_max": max(negative_sims) if negative_sims else None,
        },
    )

    # Save to calibration file
    calib_path = k_settings.calibration_path
    calib_path.parent.mkdir(parents=True, exist_ok=True)
    calib_file = CalibrationFile()
    if calib_path.is_file():
        try:
            with open(calib_path, "r", encoding="utf-8") as f:
                calib_file = CalibrationFile.model_validate_json(f.read())
        except Exception:
            calib_file = CalibrationFile()

    calib_file.models[calib_key] = model_calib
    # Also save fallback key
    calib_file.models[f"{emb_settings.embedding_model}_None"] = model_calib

    with open(calib_path, "w", encoding="utf-8") as f:
        f.write(calib_file.model_dump_json(indent=2))

    print(f"\nCalibration saved to {calib_path} for key '{calib_key}'.")
    print(f"  sap_min_score:       {model_calib.sap_min_score}")
    print(f"  sql_min_score:       {model_calib.sql_min_score}")
    print(f"  l3_hint_threshold:   {model_calib.l3_hint_threshold}")
    print(f"  l3_reuse_threshold:  {model_calib.l3_reuse_threshold}")
    print(f"  write_back_min_score:{model_calib.write_back_min_score}")
    print("=" * 80)


if __name__ == "__main__":
    main()
