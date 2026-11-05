"""Knowledge base: the project's docs, split into sections, with TF-IDF retrieval.

Each chunk is one Markdown section (## or ###), cited as `docs/model-card.md#threshold`.
TF-IDF keeps retrieval local, free and deterministic (no embedding API, nothing leaves
the machine); swapping in embeddings + pgvector later only changes `Retriever`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCES = ["README.md", "docs/*.md"]
MAX_CHARS = 2500  # long sections (e.g. the 72-month table) are cut so one hit can't flood the context

HEADING = re.compile(r"^(#{1,3})\s+(.*)$")


@dataclass
class Chunk:
    source: str   # e.g. docs/model-card.md#threshold
    title: str    # e.g. Model card: loan default model > Threshold
    text: str


def slug(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.lower()).strip()
    return re.sub(r"[\s]+", "-", s)


def split_markdown(path: str, text: str) -> list[Chunk]:
    chunks, doc_title, h2, buf, anchor, title = [], path, None, [], "", path

    def flush():
        body = "\n".join(buf).strip()
        if body:
            chunks.append(Chunk(f"{path}#{anchor}" if anchor else path, title, body[:MAX_CHARS]))

    in_code = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if m:
            flush()
            buf = []
            level, heading = len(m.group(1)), m.group(2).strip()
            if level == 1:
                doc_title, h2, anchor, title = heading, None, "", heading
            elif level == 2:
                h2, anchor, title = heading, slug(heading), f"{doc_title} > {heading}"
            else:
                anchor = slug(heading)
                title = f"{doc_title} > {h2} > {heading}" if h2 else f"{doc_title} > {heading}"
            continue
        buf.append(line)
    flush()
    return chunks


def load_chunks(root: Path = ROOT, patterns: list[str] = DEFAULT_SOURCES) -> list[Chunk]:
    chunks = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            chunks += split_markdown(rel, path.read_text(encoding="utf-8"))
    return chunks


class Retriever:
    def __init__(self, chunks: list[Chunk]):
        if not chunks:
            raise ValueError("no documents to index: run the pipeline first so docs/ exists")
        self.chunks = chunks
        # Title counted twice: a question about "the threshold" should land on the Threshold section.
        corpus = [f"{c.title} {c.title} {c.text}" for c in chunks]
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english", min_df=1)
        self.matrix = self.vectorizer.fit_transform(corpus)

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        scores = linear_kernel(self.vectorizer.transform([query]), self.matrix).ravel()
        top = np.argsort(-scores)[:k]
        return [(self.chunks[i], float(scores[i])) for i in top if scores[i] > 0]


_default: Retriever | None = None


def default_retriever() -> Retriever:
    global _default
    if _default is None:
        _default = Retriever(load_chunks())
    return _default
