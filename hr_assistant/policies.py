"""Load markdown policies and split them into section chunks with stable ids ("<doc>#<n>")."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SECTION = re.compile(r"^##\s+(\d+)\.\s+(.+)$")


@dataclass(frozen=True)
class Chunk:
    id: str
    doc_id: str
    doc_title: str
    section_no: int
    section_title: str
    text: str

    @property
    def label(self) -> str:
        return f"{self.doc_title}, §{self.section_no} {self.section_title}"


def parse_policy(doc_id: str, markdown: str) -> list[Chunk]:
    """Split one policy: '# Title', optional intro (chunk #0), then '## N. Section' blocks."""
    lines = markdown.strip().splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(f"{doc_id}: policy must start with '# Title'")
    title = lines[0][2:].strip()
    chunks: list[Chunk] = []
    current_no, current_title, buf = 0, "Introduction", []

    def flush() -> None:
        text = " ".join(l.strip() for l in buf if l.strip())
        if text:
            chunks.append(Chunk(f"{doc_id}#{current_no}", doc_id, title, current_no, current_title, text))

    for line in lines[1:]:
        m = _SECTION.match(line.strip())
        if m:
            flush()
            current_no, current_title, buf = int(m.group(1)), m.group(2).strip(), []
        else:
            buf.append(line)
    flush()
    return chunks


def load_chunks(policies_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(policies_dir.glob("*.md")):
        chunks.extend(parse_policy(path.stem, path.read_text(encoding="utf-8")))
    ids = [c.id for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate chunk ids in policies")
    return chunks
