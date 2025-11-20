from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"JSONファイルが見つかりません: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _parse_cost(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_cost_for_code(code: str, master_rows: List[Dict[str, Any]]) -> Optional[float]:
    """
    商品codeが商品名に含まれる行を探し、その移動平均単価を返す。
    最初にマッチした1件のみを使用する。
    """
    if not code:
        return None
    for row in master_rows:
        name = row.get("商品名") or ""
        if code in name:
            cost = _parse_cost(row.get("移動平均単価"))
            if cost is not None:
                return cost
    return None


def update_models_unit_cost() -> None:
    models_path = DATA_DIR / "models.json"
    master_path = DATA_DIR / "原価マスタ.json"

    models: Dict[str, List[Dict[str, Any]]] = _load_json(models_path)
    master_rows: List[Dict[str, Any]] = _load_json(master_path)

    updated_count = 0
    missing_codes = []

    for product_code, items in models.items():
        if not isinstance(items, list):
            continue
        for item in items:
            code = item.get("code")
            if not code:
                continue

            # すでに原価が入っている場合はスキップ（0 または未設定のみ更新）
            current_cost = item.get("unit_cost")
            if isinstance(current_cost, (int, float)) and current_cost not in (0, 0.0):
                continue

            cost = _find_cost_for_code(code, master_rows)
            if cost is None:
                missing_codes.append(code)
                continue

            item["unit_cost"] = cost
            updated_count += 1

    with models_path.open("w", encoding="utf-8") as f:
        json.dump(models, f, ensure_ascii=False, indent=2)

    print(f"更新完了: {updated_count} 件の unit_cost を更新しました。")
    if missing_codes:
        unique_missing = sorted(set(missing_codes))
        print("原価マスタ.json で移動平均単価が見つからなかった code:")
        for c in unique_missing:
            print(f"- {c}")


if __name__ == "__main__":
    update_models_unit_cost()


