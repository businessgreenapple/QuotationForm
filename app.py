from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy

# Flask app setup
app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY'] = 'dev-secret-key'  # 開発用
# 管理モード用の簡易パスワード（必要に応じて環境変数などに移行）
app.config.setdefault('VIEW_COST_PASSWORD', '393290')

# SQLite DB (instance/app.db)
instance_path = Path(app.instance_path)
instance_path.mkdir(parents=True, exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{instance_path / 'app.db'}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# --- Models ---
class Estimate(db.Model):
    __tablename__ = 'estimates'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    customer_id = db.Column(db.String(64), nullable=False)
    customer_name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    subtotal_price = db.Column(db.Float, default=0.0, nullable=False)
    subtotal_cost = db.Column(db.Float, default=0.0, nullable=False)
    # 値引額（税抜）
    discount = db.Column(db.Float, default=0.0, nullable=False)
    # 値引き後の合計金額（売上：合計税抜）
    total_price = db.Column(db.Float, default=0.0, nullable=False)
    # 粗利＝合計税抜 − 原価
    gross_profit = db.Column(db.Float, default=0.0, nullable=False)
    # 粗利率（合計税抜に対する％）
    gross_margin_rate = db.Column(db.Float, default=0.0, nullable=False)  # 0.0-1.0
    # 営業利益＝粗利 − 販管費（販管費＝小計×0.2）
    operating_profit = db.Column(db.Float, default=0.0, nullable=False)

    items = db.relationship('EstimateItem', backref='estimate', cascade='all, delete-orphan')


class EstimateItem(db.Model):
    __tablename__ = 'estimate_items'

    id = db.Column(db.Integer, primary_key=True)
    estimate_id = db.Column(db.Integer, db.ForeignKey('estimates.id', ondelete='CASCADE'), nullable=False)

    product_code = db.Column(db.String(64), nullable=False)
    product_name = db.Column(db.String(255), nullable=False)
    model_code = db.Column(db.String(64), nullable=True)
    model_name = db.Column(db.String(255), nullable=True)

    quantity = db.Column(db.Integer, default=1, nullable=False)
    unit_price = db.Column(db.Float, default=0.0, nullable=False)
    unit_cost = db.Column(db.Float, default=0.0, nullable=False)

    line_total_price = db.Column(db.Float, default=0.0, nullable=False)
    line_total_cost = db.Column(db.Float, default=0.0, nullable=False)


# 初回起動時にテーブル作成
with app.app_context():
    db.create_all()
    # 既存DBに列が無い場合は追加（SQLite）
    from sqlalchemy import text
    try:
        rows = db.session.execute(text("PRAGMA table_info(estimate_items)")).fetchall()
        existing_cols = {r[1] for r in rows}  # 1番目が列名
        if 'model_code' not in existing_cols:
            db.session.execute(text("ALTER TABLE estimate_items ADD COLUMN model_code VARCHAR(64)"))
        if 'model_name' not in existing_cols:
            db.session.execute(text("ALTER TABLE estimate_items ADD COLUMN model_name VARCHAR(255)"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    # estimates テーブルに値引・合計・営業利益列を追加（存在しない場合のみ）
    try:
        rows = db.session.execute(text("PRAGMA table_info(estimates)")).fetchall()
        existing_cols = {r[1] for r in rows}
        if 'discount' not in existing_cols:
            db.session.execute(
                text("ALTER TABLE estimates ADD COLUMN discount FLOAT NOT NULL DEFAULT 0.0")
            )
        if 'total_price' not in existing_cols:
            db.session.execute(
                text("ALTER TABLE estimates ADD COLUMN total_price FLOAT NOT NULL DEFAULT 0.0")
            )
            # 既存データについては、合計＝小計として初期化しておく
            db.session.execute(
                text("UPDATE estimates SET total_price = subtotal_price WHERE total_price = 0.0")
            )
        if 'operating_profit' not in existing_cols:
            db.session.execute(
                text("ALTER TABLE estimates ADD COLUMN operating_profit FLOAT NOT NULL DEFAULT 0.0")
            )
        db.session.commit()
    except Exception:
        db.session.rollback()


# サービス層
from services.masters import (
    get_customers,
    get_products,
    get_models,
    find_product_by_code,
    find_customer_by_id,
)
from services.calculator import (
    calculate_line_totals,
    calculate_estimate_totals,
)


# 太陽光用：モジュール型式ごとの容量(kW)
MODULE_CAPACITY_MAP = {
    # 例：JKM450N-54HL4R-V は 0.45kW/枚
    'JKM450N-54HL4R-V': 0.45,
}


# --- Routes ---
@app.get('/')
def estimate_list():
    q = request.args.get('q', '').strip()
    query = Estimate.query.order_by(Estimate.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter((Estimate.title.ilike(like)) | (Estimate.customer_name.ilike(like)))
    estimates = query.all()
    return render_template('estimate_list.html', estimates=estimates, q=q)


@app.get('/estimates/new')
def estimate_new():
    customers = get_customers()
    products = get_products()
    models = get_models()
    # 複数選択（type=... を複数指定）に対応。単一指定の後方互換も維持
    selected_types = [t.strip() for t in request.args.getlist('type') if t.strip()]
    if not selected_types:
        single = request.args.get('type', '').strip()
        selected_types = [single] if single else []
    return render_template(
        'estimate_form.html',
        customers=customers,
        products=products,
        models=models,
        selected_types=selected_types,
        selected_type=(selected_types[0] if selected_types else ''),
        is_admin_mode=session.get('is_admin_mode', False),
    )


@app.post('/estimates')
def estimate_create():
    form = request.form

    title = form.get('title', '').strip()
    customer_id = form.get('customer_id', '').strip()
    if not title or not customer_id:
        flash('件名と顧客は必須です。', 'error')
        return redirect(url_for('estimate_new'))

    customer = find_customer_by_id(customer_id)
    if not customer:
        flash('選択した顧客が見つかりません。', 'error')
        return redirect(url_for('estimate_new'))

    # アイテム行の復元
    codes: List[str] = form.getlist('item_product_code')
    model_codes: List[str] = form.getlist('item_model_code')
    model_names: List[str] = form.getlist('item_model_name')
    qtys: List[str] = form.getlist('item_quantity')
    unit_prices: List[str] = form.getlist('item_unit_price')
    unit_costs: List[str] = form.getlist('item_unit_cost')  # hidden

    items: List[EstimateItem] = []
    for code, mcode, mname, qty_str, up_str, uc_str in zip(codes, model_codes, model_names, qtys, unit_prices, unit_costs):
        code = code.strip()
        if not code:
            continue
        product = find_product_by_code(code)
        if not product:
            continue
        try:
            quantity = int(qty_str)
        except Exception:
            quantity = 1
        try:
            unit_price = float(up_str)
        except Exception:
            unit_price = float(product.get('unit_price', 0.0))
        try:
            unit_cost = float(uc_str)
        except Exception:
            unit_cost = float(product.get('unit_cost', 0.0))

        line_total_price, line_total_cost = calculate_line_totals(quantity, unit_price, unit_cost)
        items.append(
            EstimateItem(
                product_code=product['code'],
                product_name=product['name'],
                model_code=(mcode or None),
                model_name=(mname or None),
                quantity=quantity,
                unit_price=unit_price,
                unit_cost=unit_cost,
                line_total_price=line_total_price,
                line_total_cost=line_total_cost,
        )
        )

    # ---- 太陽光 電気工事費（SOL-009）の原価を「電材費」「電気工事費」に分けてバックエンドで計算 ----
    # システム容量(kW)は太陽電池モジュール（SOL-001）の枚数と型式から算出
    system_capacity_kw = 0.0
    for it in items:
        if it.product_code == 'SOL-001':
            per_kw = MODULE_CAPACITY_MAP.get(it.model_code or '', 0.0)
            system_capacity_kw += per_kw * float(it.quantity or 0)

    # パワコン台数はパワーコンディショナ（SOL-002）の数量合計
    powercon_count = 0
    for it in items:
        if it.product_code == 'SOL-002':
            powercon_count += int(it.quantity or 0)

    # 電気工事費 原価（単価）：システム容量(kW)×6,857 + 20,000
    if system_capacity_kw > 0:
        total_electric_cost_unit = float(system_capacity_kw) * 6857.0 + 20000.0

        for it in items:
            if it.product_code == 'SOL-009':
                it.unit_cost = total_electric_cost_unit
                # 数量は通常 1 だが、念のため数量を掛けて行原価を再計算
                it.line_total_cost = float(it.quantity or 0) * total_electric_cost_unit

    # ---- 蓄電池：その他部材（BAT-006）の原価を蓄電池ユニット型式からバックエンドで計算 ----
    battery_unit_model_code = None
    for it in items:
        if it.product_code == 'BAT-004':
            battery_unit_model_code = (it.model_code or '').strip()
            if battery_unit_model_code:
                break

    if battery_unit_model_code:
        other_material_cost = 0.0
        installation_cost = 0.0
        if battery_unit_model_code == 'ES-T3M1':
            other_material_cost = 152787.0
            installation_cost = 125000.0
        elif battery_unit_model_code in ('ESS-U4M1', 'ESS-U4X1'):
            other_material_cost = 189700.0
            if battery_unit_model_code == 'ESS-U4M1':
                installation_cost = 190885.0
            elif battery_unit_model_code == 'ESS-U4X1':
                installation_cost = 220082.0

        if other_material_cost > 0:
            for it in items:
                if it.product_code == 'BAT-006':
                    it.unit_cost = other_material_cost
                    # 数量は通常 1 だが、念のため数量を掛けて行原価を再計算
                    it.line_total_cost = float(it.quantity or 0) * other_material_cost

        if installation_cost > 0:
            for it in items:
                if it.product_code == 'BAT-007':
                    it.unit_cost = installation_cost
                    # 数量は通常 1 だが、念のため数量を掛けて行原価を再計算
                    it.line_total_cost = float(it.quantity or 0) * installation_cost

    if not items:
        flash('1件以上の商品を追加してください。', 'error')
        return redirect(url_for('estimate_new'))

    # アイテムから小計・原価小計を集計
    subtotal_price, subtotal_cost, _, _ = calculate_estimate_totals(items)
    # 「その他」原価（全見積タイプ共通）：① 小計(税抜) × 0.07 を原価に加算
    other_cost = subtotal_price * 0.07
    subtotal_cost = subtotal_cost + other_cost

    # フォームの値引額（税抜）
    discount_str = form.get('discount_amount', '0').strip()
    try:
        discount = float(discount_str)
    except Exception:
        discount = 0.0
    if discount < 0:
        discount = 0.0

    # 一般モードの場合、値引額の上限は小計の5%
    is_admin_mode = session.get('is_admin_mode', False)
    if not is_admin_mode:
        max_discount = int(subtotal_price * 0.05)
        if discount > max_discount:
            discount = float(max_discount)
            flash(f'一般モードでの値引上限（5%: ¥{max_discount:,}）を超えたため、上限値に補正しました。', 'warning')

    # 合計＝小計 − 値引（マイナスにはしない）
    total_price = max(0.0, subtotal_price - discount)

    # 粗利・粗利率・営業利益を計算
    # 粗利  = 合計税抜（total_price） − 原価（subtotal_cost）
    gross_profit = total_price - subtotal_cost
    gross_margin_rate = gross_profit / total_price if total_price > 0 else 0.0
    # 販管費 = 小計（subtotal_price）× 0.2
    selling_expense = subtotal_price * 0.2
    # 営業利益 = 粗利 − 販管費
    operating_profit = gross_profit - selling_expense

    est = Estimate(
        title=title,
        customer_id=customer['id'],
        customer_name=customer['name'],
        subtotal_price=subtotal_price,
        subtotal_cost=subtotal_cost,
        discount=discount,
        total_price=total_price,
        gross_profit=gross_profit,
        gross_margin_rate=gross_margin_rate,
        operating_profit=operating_profit,
    )
    for it in items:
        est.items.append(it)

    db.session.add(est)
    db.session.commit()

    flash('見積を保存しました。', 'success')
    return redirect(url_for('estimate_detail', estimate_id=est.id))


@app.get('/estimates/<int:estimate_id>')
def estimate_detail(estimate_id: int):
    est = Estimate.query.get_or_404(estimate_id)

    # --- 材料費（太陽光）の算出 ---
    # 対象：太陽電池モジュール、パワーコンディショナ、カラーモニター、漏電遮断器、
    #       配線用遮断器、接続ユニット、取付架台、電材費
    material_cost = 0.0
    # 太陽光の部材品目（取付架台は SOL-007 / SOL-007R の両方を対象とする）
    MATERIAL_PRODUCT_CODES = {
        'SOL-001',  # 太陽電池モジュール
        'SOL-002',  # パワーコンディショナ
        'SOL-003',  # カラーモニター
        'SOL-004',  # 漏電遮断器
        'SOL-005',  # 配線用遮断器
        'SOL-006',  # 接続ユニット
        'SOL-007',  # 取付架台
        'SOL-007R',  # 取付架台（陸屋根）
    }

    # 上記の部材行の原価（行合計）を集計
    for it in est.items:
        if it.product_code in MATERIAL_PRODUCT_CODES:
            material_cost += float(it.line_total_cost or 0.0)

    # 電材費（電気工事費 SOL-009 のうち、電材部分）を追加
    has_solar_electric = any(it.product_code == 'SOL-009' for it in est.items)
    if has_solar_electric:
        system_capacity_kw = 0.0
        powercon_count = 0
        electric_line_qty = 0
        for it in est.items:
            if it.product_code == 'SOL-001':
                per_kw = MODULE_CAPACITY_MAP.get(it.model_code or '', 0.0)
                system_capacity_kw += per_kw * float(it.quantity or 0)
            elif it.product_code == 'SOL-002':
                powercon_count += int(it.quantity or 0)
            elif it.product_code == 'SOL-009':
                electric_line_qty += int(it.quantity or 0)

        if electric_line_qty > 0 and (system_capacity_kw > 0 or powercon_count > 0):
            # 電材費：システム容量(kW)×6857 + 20000（見積単位）
            electric_material_cost_unit = float(system_capacity_kw) * 6857.0 + 20000.0
            # 行数（数量）を掛けて全体の電材費を算出
            material_cost += electric_material_cost_unit * float(electric_line_qty)

    # --- 材料費（蓄電池）の算出 ---
    # 対象：パワーコンディショナ、漏電遮断器、配線用遮断器、蓄電池ユニット、
    #       自動切替開閉器、その他部材
    BATTERY_MATERIAL_PRODUCT_CODES = {
        'BAT-001',  # パワーコンディショナ
        'BAT-002',  # 漏電遮断器
        'BAT-003',  # 配線用遮断器
        'BAT-004',  # 蓄電池ユニット
        'BAT-005',  # 自動切替開閉器
        'BAT-006',  # その他部材
    }
    for it in est.items:
        if it.product_code in BATTERY_MATERIAL_PRODUCT_CODES:
            material_cost += float(it.line_total_cost or 0.0)

    # --- 材料費（単機能V2H）の算出 ---
    # 対象：本体搬入費、電気工事労務費、取付工事費、現場雑費、現場管理費 以外
    #       （= V2H本体、設置部材セット、施工ケーブルセット、その他部材費、
    #          リモコンセット、ケーブルカバー、AC_CTケーブルセット、CTセンサ）
    V2H_SINGLE_MATERIAL_PRODUCT_CODES = {
        'V2H-001',  # V2H本体
        'V2H-002',  # 設置部材セット
        'V2H-003',  # 施工ケーブルセット
        'V2H-005',  # その他部材費
        'V2H-010',  # リモコンセット
        'V2H-011',  # ケーブルカバー
        'V2H-012',  # AC_CTケーブルセット
        'V2H-013',  # CTセンサ（内径θ24）
    }
    for it in est.items:
        if it.product_code in V2H_SINGLE_MATERIAL_PRODUCT_CODES:
            material_cost += float(it.line_total_cost or 0.0)

    # --- 材料費（トライブリッドV2H）の算出 ---
    # 対象：V2H本体、V2H通信ケーブル、その他部材、V2Hポッド用ポール
    V2H_HYBRID_MATERIAL_PRODUCT_CODES = {
        'TVH-001',  # V2H本体
        'TVH-002',  # V2H通信ケーブル
        'TVH-004',  # その他部材
        'TVH-007',  # V2Hポッド用ポール
    }
    for it in est.items:
        if it.product_code in V2H_HYBRID_MATERIAL_PRODUCT_CODES:
            material_cost += float(it.line_total_cost or 0.0)

    # --- 材料費（パワコン交換）の算出 ---
    # 対象：パワーコンディショナ、設置工事費の材料部分、電気工事費（材料費込）の材料部分
    # パワコン台数を集計
    powercon_exchange_count = 0
    for it in est.items:
        if it.product_code == 'PWR-001':
            powercon_exchange_count += int(it.quantity or 0)

    # パワーコンディショナ（PWR-001）の原価を材料費に追加
    for it in est.items:
        if it.product_code == 'PWR-001':
            material_cost += float(it.line_total_cost or 0.0)

    # 設置工事費（PWR-003）の材料部分：パワコン1台につき10600円
    if powercon_exchange_count > 0:
        installation_material_cost = powercon_exchange_count * 10600.0
        material_cost += installation_material_cost

    # 電気工事費（材料費込）（PWR-004）の材料部分：パワコン1台につき5000円
    if powercon_exchange_count > 0:
        electric_material_cost = powercon_exchange_count * 5000.0
        material_cost += electric_material_cost

    # 材料費対象の商品コードセットをテンプレートに渡す
    material_product_codes = (
        MATERIAL_PRODUCT_CODES
        | BATTERY_MATERIAL_PRODUCT_CODES
        | V2H_SINGLE_MATERIAL_PRODUCT_CODES
        | V2H_HYBRID_MATERIAL_PRODUCT_CODES
    )
    # SOL-009（電気工事費）は電材費部分が材料費に含まれるため、材料費対象として扱う
    material_product_codes.add('SOL-009')
    # PWR-001（パワーコンディショナ）は材料費に含まれるため、材料費対象として扱う
    material_product_codes.add('PWR-001')

    # 管理モード（セッション）フラグ
    is_admin_mode = session.get('is_admin_mode', False)

    return render_template(
        'estimate_detail.html',
        estimate=est,
        material_cost=material_cost,
        material_product_codes=material_product_codes,
        is_admin_mode=is_admin_mode,
    )


@app.route('/admin_mode_login', methods=['GET', 'POST'])
def admin_mode_login():
    """管理モード用の簡易ログイン画面"""
    next_url = request.args.get('next') or request.form.get('next') or url_for('estimate_list')
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        view_password = app.config.get('VIEW_COST_PASSWORD', '393290')
        if password == view_password:
            session['is_admin_mode'] = True
            flash('管理モードに切り替えました。', 'success')
            return redirect(next_url)
        flash('パスワードが正しくありません。', 'error')
        return redirect(url_for('admin_mode_login', next=next_url))

    return render_template('admin_mode_login.html', next=next_url)


@app.get('/admin_mode_logout')
def admin_mode_logout():
    """管理モードを解除"""
    session.pop('is_admin_mode', None)
    flash('管理モードを終了しました。', 'success')
    next_url = request.args.get('next') or url_for('estimate_list')
    return redirect(next_url)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
