"""Unit tests for L3 Semantic similarity cache (masking, structural fingerprint, and threshold actions)."""

from __future__ import annotations

from app.cache.similarity import (
    SimilarityCacheIndex,
    compute_structure_fingerprint,
    mask_literals_and_aliases,
)

from tests.support.fake_embedder import FakeEmbedder


class TestSimilarityCache:
    """Verifies literal masking, structural fingerprinting, and cosine threshold decisions."""

    def test_mask_literals_and_aliases(self) -> None:
        sql = "SELECT [MATNR] FROM [MARC] WHERE [WERKS] = '1000' AND [STOCK] > 50"
        masked = mask_literals_and_aliases(sql)
        assert "'1000'" not in masked
        assert "'?'" in masked
        assert " 50" not in masked
        assert " 0" in masked

    def test_compute_structure_fingerprint(self) -> None:
        sql1 = "SELECT [MATNR] FROM [MARC] WHERE [WERKS] = '1000'"
        sql2 = "SELECT [MATNR] FROM [MARC] WHERE [WERKS] = '2000'"
        # Same structure with different literal value produces identical structural fingerprint
        fp1 = compute_structure_fingerprint(sql1, ["MARC"])
        fp2 = compute_structure_fingerprint(sql2, ["MARC"])
        assert fp1 == fp2

        # Different tables produce different fingerprints
        fp3 = compute_structure_fingerprint(sql1, ["MARA"])
        assert fp1 != fp3

    def test_similarity_index_add_and_search(self) -> None:
        embedder = FakeEmbedder(dimensions=1536, model="text-embedding-3-small")
        idx = SimilarityCacheIndex(embedder=embedder)
        idx.clear()

        sql_base = "SELECT [MATNR], [WERKS] FROM [MARC] WHERE [WERKS] = '1000'"
        payload = {"agent": "sql", "result": "sample"}

        idx.add("k_sample", sql_base, ["MARC"], payload)

        # 1. Identical structure query with different literal -> reuse (>= 0.97)
        sql_variant = "SELECT [MATNR], [WERKS] FROM [MARC] WHERE [WERKS] = '2000'"
        action, res_payload, score = idx.find_similar(sql_variant, ["MARC"])
        assert action == "similar_reused"
        assert res_payload is not None
        assert score >= 0.97

        # 2. Mismatched tables -> never reuse, returns miss or hint if high score
        action_diff_table, _, _ = idx.find_similar(sql_variant, ["MARA"])
        assert action_diff_table != "similar_reused"

        # 3. Completely unrelated query -> miss
        unrelated = "SELECT COUNT(*) FROM [BSIS] GROUP BY [BUKRS]"
        action_unrel, _, _score_unrel = idx.find_similar(unrelated, ["BSIS"], hint_threshold=0.85)
        assert action_unrel == "miss"
