from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

_BASE = Path(__file__).resolve().parent.parent
_DATA = _BASE / 'data'


def _read_json(path: Path):
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)


def get_customers() -> List[Dict]:
    return _read_json(_DATA / 'customers.json')


def get_products() -> List[Dict]:
    return _read_json(_DATA / 'products.json')


def find_customer_by_id(customer_id: str) -> Optional[Dict]:
    for c in get_customers():
        if c.get('id') == customer_id:
            return c
    return None


def find_product_by_code(code: str) -> Optional[Dict]:
    for p in get_products():
        if p.get('code') == code:
            return p
    return None


def get_models() -> Dict[str, List[Dict]]:
    """
    商品コードごとの型式配列を返す。
    戻り値は {product_code: [{code,name,unit_price,unit_cost}, ...], ...}
    """
    path = _DATA / 'models.json'
    data = _read_json(path)
    # models.jsonはdictを期待。存在しない/空の場合は空dictを返す
    return data if isinstance(data, dict) else {}