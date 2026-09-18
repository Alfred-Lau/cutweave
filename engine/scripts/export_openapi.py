"""导出 OpenAPI 3.0 schema 供扣子插件导入。

用法：cd engine && .venv/bin/python scripts/export_openapi.py
产出：coze_plugin/openapi.json
注意：导入扣子后需把 servers 修改为引擎的实际公网地址。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

spec = app.openapi()
out_dir = ROOT / "coze_plugin"
out_dir.mkdir(exist_ok=True)
out = out_dir / "openapi.json"
out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"已导出: {out}")
print(f"路径数: {len(spec.get('paths', {}))}")
print("提示：导入扣子插件前，把 spec.servers 改为引擎公网地址，例如")
print('  {"url": "https://你的域名", "description": "流光剪影引擎"}')
