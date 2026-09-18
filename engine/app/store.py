"""草稿文件持久化（P0）。

以文件系统为存储：data/drafts/{draft_id}/draft.json。
Store 层接口化设计，P1 可平替为 PostgreSQL 实现。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import DRAFTS_DIR, ensure_dirs
from .models import Draft


class DraftNotFound(Exception):
    pass


def draft_dir(draft_id: str) -> Path:
    return DRAFTS_DIR / draft_id


def draft_path(draft_id: str) -> Path:
    return draft_dir(draft_id) / "draft.json"


def save_draft(draft: Draft) -> Path:
    ensure_dirs()
    p = draft_path(draft.draft_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    return p


def load_draft(draft_id: str) -> Draft:
    p = draft_path(draft_id)
    if not p.exists():
        raise DraftNotFound(draft_id)
    return Draft.model_validate_json(p.read_text(encoding="utf-8"))


def list_drafts() -> list[dict]:
    ensure_dirs()
    out: list[dict] = []
    if not DRAFTS_DIR.exists():
        return out
    for d in sorted(DRAFTS_DIR.iterdir()):
        p = d / "draft.json"
        if p.exists():
            try:
                out.append(Draft.model_validate_json(p.read_text(encoding="utf-8")).summary())
            except Exception:  # 损坏的草稿不阻塞列表
                continue
    return out


def delete_draft(draft_id: str) -> None:
    d = draft_dir(draft_id)
    if not d.exists():
        raise DraftNotFound(draft_id)
    shutil.rmtree(d)
