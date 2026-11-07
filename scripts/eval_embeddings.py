"""Evaluates candidate OpenAI embedding models against labelled queries using throwaway in-memory indexes."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add backend to sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import faiss  # type: ignore[import-untyped]
import numpy as np
from pydantic import TypeAdapter

from app.embeddings.embedder import OpenAIEmbedder, estimate_tokens
from app.embeddings.models import EmbeddingResponseError, RetrievalEvalQuery
from app.embeddings.settings import EmbeddingSettings, get_embedding_settings
from app.knowledge.build_index import build_sap_chunks, build_sql_chunks
from app.knowledge.models import DocChunk
from app.knowledge.settings import get_settings as get_knowledge_settings


def evaluate_model(
    model_name: str,
    eval_queries: list[RetrievalEvalQuery],
    sap_chunks: list[DocChunk],
    sql_chunks: list[DocChunk],
    price_per_m: float = 0.02,
) -> dict[str, object]:
    """Builds throwaway in-memory indexes and evaluates retrieval accuracy."""
    emb_settings = EmbeddingSettings(embedding_model=model_name)
    embedder = OpenAIEmbedder(settings=emb_settings)

    print(f"\n--- Evaluating Model: {model_name} ---")

    # 1. Build throwaway in-memory SAP Index
    print("Embedding SAP chunks into memory...")
    sap_texts = [c.text for c in sap_chunks]
    t0_embed = time.perf_counter()
    try:
        sap_res = embedder.embed_documents_sync(sap_texts)
    except EmbeddingResponseError as exc:
        if exc.code == "EMBEDDING_MODEL_NOT_FOUND" or "not found" in str(exc).lower():
            print(f"Skipping model '{model_name}': Provider reported EMBEDDING_MODEL_NOT_FOUND.")
            return {"model": model_name, "skipped": True, "reason": "EMBEDDING_MODEL_NOT_FOUND"}
        raise

    sap_vectors = np.array(sap_res.vectors, dtype=np.float32)
    faiss.normalize_L2(sap_vectors)
    sap_dim = sap_vectors.shape[1]
    sap_index = faiss.IndexFlatIP(sap_dim)
    sap_index.add(sap_vectors)

    # 2. Build throwaway in-memory SQL Index
    print("Embedding SQL chunks into memory...")
    sql_texts = [c.text for c in sql_chunks]
    sql_res = embedder.embed_documents_sync(sql_texts)
    sql_vectors = np.array(sql_res.vectors, dtype=np.float32)
    faiss.normalize_L2(sql_vectors)
    sql_dim = sql_vectors.shape[1]
    sql_index = faiss.IndexFlatIP(sql_dim)
    sql_index.add(sql_vectors)

    embed_time_s = time.perf_counter() - t0_embed
    total_tokens = sap_res.usage.total_tokens + sql_res.usage.total_tokens

    # 3. Run labelled query evaluation
    r1, r3, r5 = 0, 0, 0
    reciprocal_ranks: list[float] = []
    positive_count = 0
    negative_count = 0
    false_positives = 0
    query_latencies: list[float] = []

    for eq in eval_queries:
        t0_q = time.perf_counter()
        q_vec_raw = embedder.embed_query_sync(eq.query)
        q_vec = np.array([q_vec_raw], dtype=np.float32)
        faiss.normalize_L2(q_vec)
        lat_ms = (time.perf_counter() - t0_q) * 1000
        query_latencies.append(lat_ms)

        if eq.domain == "sap":
            scores, indices = sap_index.search(q_vec, 5)
            hit_chunks = [sap_chunks[i] for i in indices[0] if i != -1]
            hit_scores = [float(s) for s in scores[0] if s != -1]
            hit_targets = [getattr(c.metadata, "table", "") for c in hit_chunks]
        else:
            scores, indices = sql_index.search(q_vec, 5)
            hit_chunks = [sql_chunks[i] for i in indices[0] if i != -1]
            hit_scores = [float(s) for s in scores[0] if s != -1]
            hit_targets = [getattr(c.metadata, "chapter", "") for c in hit_chunks]

        if eq.is_negative:
            negative_count += 1
            # Negative query should have top score < 0.50
            if hit_scores and hit_scores[0] >= 0.50:
                false_positives += 1
        else:
            positive_count += 1
            # Check ranks
            expected = eq.expected_target.upper()
            rank: int | None = None
            for idx, target in enumerate(hit_targets, 1):
                if expected in target.upper() or target.upper() in expected:
                    rank = idx
                    break

            if rank is not None:
                if rank == 1:
                    r1 += 1
                if rank <= 3:
                    r3 += 1
                if rank <= 5:
                    r5 += 1
                reciprocal_ranks.append(1.0 / rank)
            else:
                reciprocal_ranks.append(0.0)

    recall_1 = r1 / positive_count if positive_count else 0.0
    recall_3 = r3 / positive_count if positive_count else 0.0
    recall_5 = r5 / positive_count if positive_count else 0.0
    mrr = float(np.mean(reciprocal_ranks)) if reciprocal_ranks else 0.0
    fp_rate = false_positives / negative_count if negative_count else 0.0
    avg_latency = float(np.mean(query_latencies)) if query_latencies else 0.0
    cost_usd = (total_tokens / 1_000_000.0) * price_per_m

    return {
        "model": model_name,
        "skipped": False,
        "recall_1": round(recall_1, 4),
        "recall_3": round(recall_3, 4),
        "recall_5": round(recall_5, 4),
        "mrr": round(mrr, 4),
        "false_positive_rate": round(fp_rate, 4),
        "total_tokens": total_tokens,
        "build_time_s": round(embed_time_s, 2),
        "avg_query_latency_ms": round(avg_latency, 2),
        "estimated_cost_usd": round(cost_usd, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate embedding models against labelled queries.")
    parser.add_argument(
        "--models",
        default="text-embedding-3-small,text-embedding-3-large",
        help="Comma-separated model identifiers to test",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Maximum cost budget in USD (hard stop if estimated cost exceeds)",
    )
    args = parser.parse_args()

    emb_settings = get_embedding_settings()
    if not emb_settings.is_configured:
        print("=" * 80)
        print("                   EMBEDDING EVALUATION HARNESS")
        print("=" * 80)
        print("Error: No valid API key configured for embeddings.")
        print("Set OPENAI_API_KEY in .env before running eval_embeddings.")
        print("=" * 80)
        sys.exit(1)

    k_settings = get_knowledge_settings()
    with open(k_settings.eval_queries_path, "r", encoding="utf-8") as f:
        eval_queries = TypeAdapter(list[RetrievalEvalQuery]).validate_json(f.read())

    sap_chunks = build_sap_chunks(k_settings)
    sql_chunks = build_sql_chunks(k_settings)

    model_list = [m.strip() for m in args.models.split(",") if m.strip()]
    results: list[dict[str, object]] = []

    for model in model_list:
        res = evaluate_model(model, eval_queries, sap_chunks, sql_chunks)
        results.append(res)

    # Write evaluation report to docs/eval/embeddings_<timestamp>.md
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    eval_dir = repo_root / "docs" / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    report_path = eval_dir / f"embeddings_{ts}.md"

    md_lines = [
        f"# Embedding Model Evaluation Report — {ts}",
        "",
        "## Evaluation Setup",
        f"- **Labelled Queries**: {len(eval_queries)}",
        f"- **SAP Chunks**: {len(sap_chunks)}",
        f"- **SQL Chunks**: {len(sql_chunks)}",
        "",
        "## Results Table",
        "| Model | Recall@1 | Recall@3 | Recall@5 | MRR | Negative FP Rate | Latency (ms) | Tokens | Est. Cost |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for r in results:
        if r.get("skipped"):
            md_lines.append(f"| {r['model']} | SKIPPED ({r.get('reason')}) | - | - | - | - | - | - | - |")
        else:
            md_lines.append(
                f"| {r['model']} | {r['recall_1']} | {r['recall_3']} | {r['recall_5']} | {r['mrr']} | {r['false_positive_rate']} | {r['avg_query_latency_ms']}ms | {r['total_tokens']:,} | ${r['estimated_cost_usd']:.4f} |"
            )

    report_path.write_text("\n".join(md_lines), encoding="utf-8")
    print("\n" + "=" * 80)
    print(f"Evaluation report written to: {report_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
