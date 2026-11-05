"""Ingestion pipeline for multi-dialect SQL reference PDF into structured DocChunks."""

from __future__ import annotations

import re
from typing import Any

import pdfplumber

from app.knowledge.models import DocChunk, SqlChunkMetadata, SqlDialect
from app.knowledge.settings import KnowledgeSettings, get_settings


def estimate_tokens(text: str) -> int:
    """Rough token estimation based on word and punctuation tokens."""
    return len(re.findall(r"\w+|[^\w\s]", text))


def detect_dialects(text: str) -> list[SqlDialect]:
    """Detect mentioned SQL dialects in text."""
    t_lower = text.lower()
    dialects: list[SqlDialect] = []
    if re.search(r"\b(t-sql|tsql|sql server|transact-sql|mssql)\b", t_lower):
        dialects.append(SqlDialect.tsql)
    if re.search(r"\b(oracle|pl/sql|plsql)\b", t_lower):
        dialects.append(SqlDialect.oracle)
    if re.search(r"\b(mysql|mariadb)\b", t_lower):
        dialects.append(SqlDialect.mysql)
    if re.search(r"\b(postgres|postgresql|pg_catalog|pgsql)\b", t_lower):
        dialects.append(SqlDialect.postgres)
    if re.search(r"\b(ansi|standard sql|iso/ansi)\b", t_lower):
        dialects.append(SqlDialect.ansi)
    if not dialects:
        dialects.append(SqlDialect.unknown)
    return dialects


def detect_has_code(text: str) -> bool:
    """Detect if chunk contains SQL code."""
    patterns = [
        r"\b(SELECT|INSERT INTO|UPDATE|DELETE FROM|CREATE TABLE|ALTER TABLE|DROP TABLE)\b",
        r"\b(WITH\s+\w+\s+AS|CROSS APPLY|OUTER APPLY|PARTITION BY)\b",
        r";\s*$",
    ]
    for pat in patterns:
        if re.search(pat, text, re.MULTILINE | re.IGNORECASE):
            return True
    return False


def detect_keywords(text: str, candidate_keywords: set[str] | None = None) -> list[str]:
    """Detect SQL keywords present in the text."""
    if candidate_keywords is None:
        candidate_keywords = {
            "SELECT",
            "FROM",
            "WHERE",
            "GROUP BY",
            "HAVING",
            "ORDER BY",
            "JOIN",
            "INNER JOIN",
            "LEFT JOIN",
            "RIGHT JOIN",
            "FULL JOIN",
            "CROSS JOIN",
            "CROSS APPLY",
            "OUTER APPLY",
            "UNION",
            "UNION ALL",
            "EXCEPT",
            "INTERSECT",
            "CTE",
            "WITH",
            "RECURSIVE",
            "INSERT",
            "UPDATE",
            "DELETE",
            "MERGE",
            "CREATE",
            "ALTER",
            "DROP",
            "VIEW",
            "INDEX",
            "TRIGGER",
            "PROCEDURE",
            "FUNCTION",
            "CASE",
            "COALESCE",
            "NULLIF",
            "ISNULL",
            "ROW_NUMBER",
            "RANK",
            "DENSE_RANK",
            "LAG",
            "LEAD",
            "NTILE",
            "PARTITION BY",
            "OVER",
            "EXISTS",
            "NOT EXISTS",
            "IN",
            "NOT IN",
            "LIKE",
            "BETWEEN",
            "CAST",
            "CONVERT",
            "SUBSTRING",
            "TOP",
            "OFFSET",
            "FETCH",
            "DISTINCT",
            "NULL",
            "TRANSACTION",
            "COMMIT",
            "ROLLBACK",
        }
    t_upper = text.upper()
    found: list[str] = []
    for kw in candidate_keywords:
        pat = r"\b" + re.escape(kw) + r"\b"
        if re.search(pat, t_upper):
            found.append(kw)
    return sorted(found)


class SqlDocsIngest:
    """Parses sql_reference.pdf into structured section-level DocChunks."""

    def __init__(self, settings: KnowledgeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.pdf_path = self.settings.sql_pdf_path

    def ingest(self) -> list[DocChunk]:
        """Ingest the SQL PDF and return a list of DocChunks."""
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"SQL Reference PDF not found at {self.pdf_path}")

        with pdfplumber.open(self.pdf_path) as pdf:
            chapters = self._parse_toc(pdf)
            chunks = self._extract_and_chunk(pdf, chapters)
        return chunks

    def _parse_toc(self, pdf: pdfplumber.PDF) -> list[dict[str, Any]]:
        """Extract TOC chapters and sections from pages 2-18 (indices 1-17)."""
        toc_lines: list[str] = []
        for p in range(1, 18):
            if p >= len(pdf.pages):
                break
            text = pdf.pages[p].extract_text() or ""
            for line in text.split("\n"):
                line = line.strip()
                if line and "https://riptutorial.com/" not in line:
                    toc_lines.append(line)

        chapters: list[dict[str, Any]] = []
        current_ch: dict[str, Any] | None = None
        ch_re = re.compile(r"^Chapter\s+(\d+):\s*(.+?)\s+(\d+)$")
        sec_re = re.compile(r"^(.+?)\s+(\d+)$")

        for line in toc_lines:
            if line == "Table of Contents" or line.startswith(("About ", "Credits ")):
                continue
            m_ch = ch_re.match(line)
            if m_ch:
                current_ch = {
                    "number": int(m_ch.group(1)),
                    "title": m_ch.group(2).strip(),
                    "page": int(m_ch.group(3)),
                    "sections": [],
                }
                chapters.append(current_ch)
            elif current_ch:
                m_sec = sec_re.match(line)
                if m_sec:
                    sec_title = m_sec.group(1).strip()
                    if sec_title.lower() != "examples":
                        current_ch["sections"].append(
                            {
                                "title": sec_title,
                                "page": int(m_sec.group(2)),
                            }
                        )

        if not chapters:
            raise ValueError("Failed to parse any chapters from SQL PDF Table of Contents.")

        # Validate that chapters are detected in body
        for ch in chapters:
            num = ch["number"]
            doc_p = ch["page"]
            pdf_idx = doc_p + 18 - 1
            if pdf_idx < len(pdf.pages):
                first_line = (pdf.pages[pdf_idx].extract_text() or "").split("\n")[0].strip()
                if not first_line.startswith(f"Chapter {num}:"):
                    raise ValueError(
                        f"TOC validation error: Chapter {num} expected on page {pdf_idx + 1}, found '{first_line}'"
                    )

        return chapters

    def _extract_and_chunk(
        self, pdf: pdfplumber.PDF, chapters: list[dict[str, Any]]
    ) -> list[DocChunk]:
        """Extract body text per chapter, split into sections, and construct DocChunks."""
        chunks: list[DocChunk] = []
        chunk_counter = 0

        for idx, ch in enumerate(chapters):
            ch_num = ch["number"]
            ch_title = ch["title"]

            # Skip non-content chapters: Chapter 17 is Example Databases and Tables
            if ch_num == 17:
                continue

            doc_start_page = ch["page"]
            doc_end_page = (chapters[idx + 1]["page"] - 1) if idx + 1 < len(chapters) else 198
            pdf_start_idx = doc_start_page + 18 - 1
            pdf_end_idx = min(doc_end_page + 18 - 1, len(pdf.pages) - 1)

            # Build list of pages
            pages: list[tuple[int, int, str]] = []
            for p_idx in range(pdf_start_idx, pdf_end_idx + 1):
                p_text = pdf.pages[p_idx].extract_text() or ""
                # Strip footers and online chapter links
                p_text = re.sub(
                    r"Read\s+.*?\s+online:\s*https?://\S+", "", p_text, flags=re.IGNORECASE
                )
                p_text = re.sub(r"https?://riptutorial\.com\S*", "", p_text, flags=re.IGNORECASE)
                p_text = p_text.strip()
                doc_p = p_idx - 18 + 1
                pages.append((doc_p, p_idx + 1, p_text))

            # Concatenate chapter text with page character offsets
            ch_text = ""
            page_offsets: list[tuple[int, int, int, int]] = []
            for doc_p, pdf_p, p_text in pages:
                start_c = len(ch_text)
                ch_text += p_text + "\n\n"
                end_c = len(ch_text)
                page_offsets.append((start_c, end_c, doc_p, pdf_p))

            # Strip initial chapter header line
            ch_text = re.sub(
                rf"^Chapter\s+{ch_num}:\s*.*?\n", "", ch_text, flags=re.IGNORECASE
            ).strip()

            sections_to_process = ch["sections"]
            if not sections_to_process:
                sections_to_process = [{"title": ch_title, "page": doc_start_page}]

            # Anchor each section to its page
            sec_spans: list[tuple[str, int, int, int]] = []
            last_pos = 0

            for sec in sections_to_process:
                sec_title = sec["title"]
                sec_doc_p = sec["page"]

                # Find character offset of section's TOC doc page
                page_char_start = 0
                for start_c, _, doc_p, _ in page_offsets:
                    if doc_p >= sec_doc_p:
                        page_char_start = start_c
                        break

                search_from = max(last_pos, page_char_start)
                words = [re.escape(w) for w in re.split(r"[\s\-–—]+", sec_title) if w]
                pattern = r"[\s\-–—]+".join(words)

                m = re.search(pattern, ch_text[search_from:], re.IGNORECASE)
                if m:
                    actual_pos = search_from + m.start()
                    sec_spans.append((sec_title, actual_pos, m.end() - m.start(), sec_doc_p))
                    last_pos = actual_pos + (m.end() - m.start())

            if not sec_spans:
                sec_spans = [(ch_title, 0, 0, doc_start_page)]

            for i, (s_title, s_pos, header_len, s_doc_p) in enumerate(sec_spans):
                body_start = s_pos + header_len
                body_end = sec_spans[i + 1][1] if i + 1 < len(sec_spans) else len(ch_text)
                sec_body = ch_text[body_start:body_end].strip()

                # Clean hanging Examples header
                sec_body = re.sub(r"\nExamples\s*$", "", sec_body, flags=re.IGNORECASE).strip()

                if len(sec_body) < 15 and estimate_tokens(sec_body) < 5:
                    continue

                p_start = s_doc_p + 18
                p_end = p_start
                for start_c, _, _, pdf_p in page_offsets:
                    if body_end >= start_c:
                        p_end = pdf_p

                tokens = estimate_tokens(sec_body)
                if tokens <= 900:
                    chunk_counter += 1
                    chunk_id = f"sql_chunk_{chunk_counter:04d}"
                    chunk_text = f"Chapter: {ch_title} > Section: {s_title}\n\n{sec_body}"
                    metadata = SqlChunkMetadata(
                        type="sql",
                        chapter=ch_title,
                        section=s_title,
                        page_start=p_start,
                        page_end=p_end,
                        dialects=detect_dialects(chunk_text),
                        has_code=detect_has_code(chunk_text),
                        keywords=detect_keywords(chunk_text),
                    )
                    chunks.append(
                        DocChunk(
                            chunk_id=chunk_id,
                            index_name="sql",
                            text=chunk_text,
                            metadata=metadata,
                        )
                    )
                else:
                    # Split oversized section at paragraph boundaries with ~80 token overlap
                    paras = re.split(r"\n\s*\n", sec_body)
                    cur_paras: list[str] = []
                    cur_tokens = 0
                    part_num = 1
                    for p in paras:
                        p_tok = estimate_tokens(p)
                        if cur_tokens + p_tok > 750 and cur_paras:
                            part_text = "\n\n".join(cur_paras)
                            chunk_counter += 1
                            chunk_id = f"sql_chunk_{chunk_counter:04d}"
                            chunk_text = f"Chapter: {ch_title} > Section: {s_title} (Part {part_num})\n\n{part_text}"
                            metadata = SqlChunkMetadata(
                                type="sql",
                                chapter=ch_title,
                                section=f"{s_title} (Part {part_num})",
                                page_start=p_start,
                                page_end=p_end,
                                dialects=detect_dialects(chunk_text),
                                has_code=detect_has_code(chunk_text),
                                keywords=detect_keywords(chunk_text),
                            )
                            chunks.append(
                                DocChunk(
                                    chunk_id=chunk_id,
                                    index_name="sql",
                                    text=chunk_text,
                                    metadata=metadata,
                                )
                            )
                            part_num += 1
                            overlap = cur_paras[-1:]
                            cur_paras = overlap + [p]
                            cur_tokens = sum(estimate_tokens(x) for x in cur_paras)
                        else:
                            cur_paras.append(p)
                            cur_tokens += p_tok

                    if cur_paras:
                        part_text = "\n\n".join(cur_paras)
                        chunk_counter += 1
                        chunk_id = f"sql_chunk_{chunk_counter:04d}"
                        chunk_text = f"Chapter: {ch_title} > Section: {s_title} (Part {part_num})\n\n{part_text}"
                        metadata = SqlChunkMetadata(
                            type="sql",
                            chapter=ch_title,
                            section=f"{s_title} (Part {part_num})" if part_num > 1 else s_title,
                            page_start=p_start,
                            page_end=p_end,
                            dialects=detect_dialects(chunk_text),
                            has_code=detect_has_code(chunk_text),
                            keywords=detect_keywords(chunk_text),
                        )
                        chunks.append(
                            DocChunk(
                                chunk_id=chunk_id,
                                index_name="sql",
                                text=chunk_text,
                                metadata=metadata,
                            )
                        )

        return chunks
