"""업무 문서(Word/PDF/엑셀/텍스트)를 Claude에 보낼 content block으로 변환."""

from __future__ import annotations

import base64
import csv
from pathlib import Path

SUPPORTED_SUFFIXES = {".docx", ".pdf", ".xlsx", ".xlsm", ".csv", ".txt", ".md"}


def find_documents(root: Path) -> list[Path]:
    """root 아래의 지원 문서를 정렬해서 반환 (임시 파일 ~$ 제외)."""
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_SUFFIXES
        and not p.name.startswith(("~$", "."))
    )


def _docx_text(path: Path) -> str:
    import docx  # python-docx

    d = docx.Document(str(path))
    parts: list[str] = [p.text for p in d.paragraphs if p.text.strip()]
    for ti, table in enumerate(d.tables, 1):
        parts.append(f"\n[표 {ti}]")
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _xlsx_text(path: Path) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        parts.append(f"\n[시트: {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                parts.append(" | ".join(cells))
    wb.close()
    return "\n".join(parts)


def _csv_text(path: Path) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            with path.open(encoding=enc, newline="") as f:
                return "\n".join(" | ".join(r) for r in csv.reader(f))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"CSV 인코딩을 읽을 수 없습니다: {path}")


def _plain_text(path: Path) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"텍스트 인코딩을 읽을 수 없습니다: {path}")


def to_content_blocks(path: Path) -> list[dict]:
    """문서 하나를 Claude 메시지 content block 목록으로 변환.

    PDF는 그대로 document block으로 보내 표·스캔본까지 Claude가 직접 읽게 하고,
    나머지는 텍스트로 추출한다.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        return [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                "title": path.name,
            }
        ]
    if suffix == ".docx":
        text = _docx_text(path)
    elif suffix in (".xlsx", ".xlsm"):
        text = _xlsx_text(path)
    elif suffix == ".csv":
        text = _csv_text(path)
    elif suffix in (".txt", ".md"):
        text = _plain_text(path)
    else:
        raise ValueError(f"지원하지 않는 형식: {path.suffix}")
    return [{"type": "text", "text": f"<document name=\"{path.name}\">\n{text}\n</document>"}]
