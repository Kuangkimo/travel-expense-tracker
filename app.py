"""
Travel Expense Tracker - Flask Backend
一个无感的出游花费统计工具
"""
import os
import json
import uuid
import re
from datetime import datetime
from io import BytesIO

from flask import (
    Flask, render_template, request, jsonify, 
    redirect, url_for, session, send_from_directory
)
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract

# ── 配置 ──────────────────────────────────────
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
DB_PATH = os.path.join(BASE_DIR, 'database.db')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'travel-expense-dev-key')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── 数据库初始化 ────────────────────────────
import sqlite3

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            share_code TEXT NOT NULL UNIQUE,
            currency TEXT DEFAULT 'CNY',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_self INTEGER DEFAULT 0,
            join_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL DEFAULT '其他',
            note TEXT DEFAULT '',
            split_type TEXT DEFAULT 'equal',
            split_members TEXT DEFAULT '[]',
            receipt_image TEXT DEFAULT '',
            expense_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE,
            FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            data_json TEXT NOT NULL,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()

init_db()

# ── 工具函数 ────────────────────────────────

CATEGORIES = ['餐饮', '交通', '住宿', '门票', '购物', '酒水/娱乐', '其他']
CATEGORY_ICONS = {
    '餐饮': '🍽️', '交通': '🚗', '住宿': '🏠', 
    '门票': '🎫', '购物': '🛍️', '酒水/娱乐': '🍻', '其他': '📌'
}

def generate_share_code():
    """生成6位共享码"""
    return uuid.uuid4().hex[:6].upper()

def parse_ocr_text(text):
    """
    从OCR识别文本中提取金额、分类和备注
    支持格式: "吃饭 120"、"打车 35.5"、"门票 80 故宫"等
    """
    if not text:
        return {'amount': None, 'category': '', 'note': ''}
    
    # 提取金额 (支持整数和小数)
    amounts = re.findall(r'(\d+(?:\.\d{1,2})?)', text)
    amount = float(amounts[0]) if amounts else None
    
    # 提取金额后清理文本
    clean_text = re.sub(r'\d+(?:\.\d{1,2})?', '', text).strip()
    
    # 尝试匹配分类
    category = ''
    for cat in CATEGORIES:
        if cat in clean_text:
            category = cat
            clean_text = clean_text.replace(cat, '').strip()
            break
    
    # 剩余部分作为备注
    note = clean_text.strip()
    
    return {'amount': amount, 'category': category, 'note': note}

# ── 路由：页面 ──────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', categories=CATEGORIES, icons=CATEGORY_ICONS)

@app.route('/trip/<int:trip_id>')
def trip_dashboard(trip_id):
    return render_template('trip.html', trip_id=trip_id, categories=CATEGORIES, icons=CATEGORY_ICONS)

@app.route('/trip/<int:trip_id>/add')
def add_expense(trip_id):
    return render_template('add_expense.html', trip_id=trip_id, categories=CATEGORIES, icons=CATEGORY_ICONS)

@app.route('/trip/<int:trip_id>/settle')
def settlement(trip_id):
    return render_template('settlement.html', trip_id=trip_id, categories=CATEGORIES, icons=CATEGORY_ICONS)

@app.route('/trip/<int:trip_id>/sync')
def sync_page(trip_id):
    return render_template('sync.html', trip_id=trip_id, categories=CATEGORIES, icons=CATEGORY_ICONS)

# ── API：行程管理 ───────────────────────────

@app.route('/api/trip/create', methods=['POST'])
def create_trip():
    data = request.get_json()
    name = data.get('name', '我的旅行').strip()
    member_name = data.get('member_name', '我').strip()
    
    if not name:
        return jsonify({'ok': False, 'error': '行程名称不能为空'})
    
    share_code = generate_share_code()
    conn = get_db()
    
    try:
        cursor = conn.execute(
            'INSERT INTO trips (name, share_code) VALUES (?, ?)',
            (name, share_code)
        )
        trip_id = cursor.lastrowid
        
        # 添加默认成员（自己）
        cursor = conn.execute(
            'INSERT INTO members (trip_id, name, is_self) VALUES (?, ?, 1)',
            (trip_id, member_name)
        )
        member_id = cursor.lastrowid
        conn.commit()
        
        # 存入 session
        session['trip_id'] = trip_id
        session['member_id'] = member_id
        session['member_name'] = member_name
        
        return jsonify({
            'ok': True,
            'trip_id': trip_id,
            'share_code': share_code,
            'member_id': member_id,
            'member_name': member_name
        })
    except Exception as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/api/trip/join', methods=['POST'])
def join_trip():
    data = request.get_json()
    share_code = data.get('share_code', '').strip().upper()
    member_name = data.get('member_name', '我').strip()
    
    if not share_code:
        return jsonify({'ok': False, 'error': '请输入共享码'})
    
    conn = get_db()
    try:
        trip = conn.execute(
            'SELECT * FROM trips WHERE share_code = ?', (share_code,)
        ).fetchone()
        
        if not trip:
            return jsonify({'ok': False, 'error': '共享码无效，请检查后重试'})
        
        # 检查重名
        existing = conn.execute(
            'SELECT * FROM members WHERE trip_id = ? AND name = ?',
            (trip['id'], member_name)
        ).fetchone()
        if existing:
            # 沿用之前的成员记录
            member_id = existing['id']
        else:
            cursor = conn.execute(
                'INSERT INTO members (trip_id, name) VALUES (?, ?)',
                (trip['id'], member_name)
            )
            member_id = cursor.lastrowid
            conn.commit()
        
        session['trip_id'] = trip['id']
        session['member_id'] = member_id
        session['member_name'] = member_name
        
        return jsonify({
            'ok': True,
            'trip_id': trip['id'],
            'share_code': share_code,
            'member_id': member_id,
            'member_name': member_name,
            'trip_name': trip['name']
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/api/trip/<int:trip_id>')
def get_trip(trip_id):
    conn = get_db()
    trip = conn.execute('SELECT * FROM trips WHERE id = ?', (trip_id,)).fetchone()
    if not trip:
        conn.close()
        return jsonify({'ok': False, 'error': '行程不存在'})
    
    members = conn.execute(
        'SELECT * FROM members WHERE trip_id = ? ORDER BY is_self DESC, join_time ASC',
        (trip_id,)
    ).fetchall()
    
    expenses = conn.execute(
        'SELECT e.*, m.name as payer_name FROM expenses e '
        'JOIN members m ON e.member_id = m.id '
        'WHERE e.trip_id = ? ORDER BY e.expense_time DESC',
        (trip_id,)
    ).fetchall()
    
    conn.close()
    
    return jsonify({
        'ok': True,
        'trip': {
            'id': trip['id'],
            'name': trip['name'],
            'share_code': trip['share_code'],
            'created_at': trip['created_at']
        },
        'members': [{
            'id': m['id'],
            'name': m['name'],
            'is_self': bool(m['is_self'])
        } for m in members],
        'expenses': [{
            'id': e['id'],
            'member_id': e['member_id'],
            'payer_name': e['payer_name'],
            'amount': e['amount'],
            'category': e['category'],
            'note': e['note'],
            'split_type': e['split_type'],
            'split_members': json.loads(e['split_members']) if e['split_members'] else [],
            'receipt_image': e['receipt_image'],
            'expense_time': e['expense_time']
        } for e in expenses]
    })

@app.route('/api/trip/<int:trip_id>/members')
def get_members(trip_id):
    conn = get_db()
    members = conn.execute(
        'SELECT * FROM members WHERE trip_id = ? ORDER BY is_self DESC, join_time ASC',
        (trip_id,)
    ).fetchall()
    conn.close()
    
    return jsonify({
        'ok': True,
        'members': [{
            'id': m['id'],
            'name': m['name'],
            'is_self': bool(m['is_self'])
        } for m in members]
    })

@app.route('/api/trip/<int:trip_id>/member/add', methods=['POST'])
def add_member(trip_id):
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '姓名不能为空'})
    
    conn = get_db()
    try:
        cursor = conn.execute(
            'INSERT INTO members (trip_id, name) VALUES (?, ?)',
            (trip_id, name)
        )
        conn.commit()
        return jsonify({'ok': True, 'member_id': cursor.lastrowid, 'name': name})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/api/trip/<int:trip_id>/member/<int:member_id>/delete', methods=['POST'])
def delete_member(trip_id, member_id):
    conn = get_db()
    try:
        conn.execute('DELETE FROM members WHERE id = ? AND trip_id = ?', (member_id, trip_id))
        conn.execute('DELETE FROM expenses WHERE member_id = ? AND trip_id = ?', (member_id, trip_id))
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

# ── API：支出管理 ───────────────────────────

@app.route('/api/trip/<int:trip_id>/expense/add', methods=['POST'])
def add_expense_api(trip_id):
    data = request.form if request.files else request.get_json()
    
    # 支持form-data（带图片）和JSON
    if isinstance(data, dict) and request.content_type and 'multipart' in request.content_type:
        amount = float(data.get('amount', 0))
        category = data.get('category', '其他')
        note = data.get('note', '')
        member_id = int(data.get('member_id', session.get('member_id', 0)))
        split_type = data.get('split_type', 'equal')
        split_members = data.get('split_members', '[]')
        expense_time = data.get('expense_time', datetime.now().strftime('%Y-%m-%d %H:%M'))
        
        receipt_image = ''
        if 'receipt' in request.files:
            file = request.files['receipt']
            if file.filename:
                filename = f"{uuid.uuid4().hex}_{file.filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                receipt_image = filename
    else:
        amount = float(data.get('amount', 0))
        category = data.get('category', '其他')
        note = data.get('note', '')
        member_id = int(data.get('member_id', session.get('member_id', 0)))
        split_type = data.get('split_type', 'equal')
        split_members = data.get('split_members', '[]')
        expense_time = data.get('expense_time', datetime.now().strftime('%Y-%m-%d %H:%M'))
        receipt_image = ''
    
    if amount <= 0:
        return jsonify({'ok': False, 'error': '金额必须大于0'})
    
    if isinstance(split_members, str):
        split_members = json.loads(split_members)
    
    conn = get_db()
    try:
        # 验证member属于该trip
        member = conn.execute(
            'SELECT * FROM members WHERE id = ? AND trip_id = ?',
            (member_id, trip_id)
        ).fetchone()
        if not member:
            # 取第一个成员作为默认
            member = conn.execute(
                'SELECT * FROM members WHERE trip_id = ? ORDER BY is_self DESC LIMIT 1',
                (trip_id,)
            ).fetchone()
            member_id = member['id']
        
        cursor = conn.execute(
            'INSERT INTO expenses (trip_id, member_id, amount, category, note, split_type, split_members, receipt_image, expense_time) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (trip_id, member_id, amount, category, note, split_type, 
             json.dumps(split_members, ensure_ascii=False), receipt_image, expense_time)
        )
        conn.commit()
        return jsonify({
            'ok': True,
            'expense_id': cursor.lastrowid,
            'paid_by': member['name']
        })
    except Exception as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/api/trip/<int:trip_id>/expenses')
def get_expenses(trip_id):
    conn = get_db()
    expenses = conn.execute(
        'SELECT e.*, m.name as payer_name FROM expenses e '
        'JOIN members m ON e.member_id = m.id '
        'WHERE e.trip_id = ? ORDER BY e.expense_time DESC',
        (trip_id,)
    ).fetchall()
    conn.close()
    
    return jsonify({
        'ok': True,
        'expenses': [{
            'id': e['id'],
            'member_id': e['member_id'],
            'payer_name': e['payer_name'],
            'amount': e['amount'],
            'category': e['category'],
            'note': e['note'],
            'split_type': e['split_type'],
            'split_members': json.loads(e['split_members']) if e['split_members'] else [],
            'receipt_image': e['receipt_image'],
            'expense_time': e['expense_time']
        } for e in expenses]
    })

@app.route('/api/trip/<int:trip_id>/expense/<int:expense_id>/delete', methods=['POST'])
def delete_expense(trip_id, expense_id):
    conn = get_db()
    try:
        conn.execute(
            'DELETE FROM expenses WHERE id = ? AND trip_id = ?',
            (expense_id, trip_id)
        )
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

# ── OCR引擎 ────────────────────────────────
# 增强型预处理 + 多策略并发识别

def preprocess_image_for_ocr(img, scale=3):
    """使用PIL进行7步预处理，专为支付截图优化"""
    # 1. 大幅放大（支付截图文字小，放大后识别率显著提升）
    w, h = img.size
    img = img.resize((w * scale, h * scale), Image.LANCZOS)
    
    # 2. 转灰度
    if img.mode != 'L':
        img = img.convert('L')
    
    # 3. 增强对比度
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    
    # 4. 锐化两次
    img = img.filter(ImageFilter.SHARPEN)
    img = img.filter(ImageFilter.SHARPEN)
    
    # 5. 自适应二值化
    blurred = img.filter(ImageFilter.GaussianBlur(radius=15))
    pixels = img.load()
    blur_px = blurred.load()
    result = Image.new('L', img.size, 255)
    result_px = result.load()
    
    for y in range(img.height):
        for x in range(img.width):
            if pixels[x, y] < blur_px[x, y] - 15:
                result_px[x, y] = 0  # 文字（黑色）
            else:
                result_px[x, y] = 255  # 背景（白色）
    
    # 6. 去噪
    result = result.filter(ImageFilter.MedianFilter(size=3))
    return result


def try_ocr_pass(image, config_str, lang='chi_sim+eng'):
    """尝试一次OCR识别"""
    try:
        text = pytesseract.image_to_string(image, lang=lang, config=config_str)
        return text.strip()
    except:
        return ''


def smart_ocr(image):
    """智能OCR：多策略并发，自动选最优结果"""
    from collections import Counter
    
    all_results = []
    
    # 策略1：放大3倍+仅数字（专抓金额）
    img1 = preprocess_image_for_ocr(image, scale=3)
    t1 = try_ocr_pass(img1, 
        '--psm 6 --oem 3 -c tessedit_char_whitelist=0123456789.')
    if t1: all_results.append(('digits', t1))
    
    # 策略2：放大3倍+完整中文（抓备注和分类）
    t2 = try_ocr_pass(img1, '--psm 6 --oem 3')
    if t2: all_results.append(('full', t2))
    
    # 策略3：2倍放大+快速模式
    img3 = image.copy()
    if img3.mode != 'L':
        img3 = img3.convert('L')
    enhancer = ImageEnhance.Contrast(img3)
    img3 = enhancer.enhance(1.5)
    w, h = img3.size
    img3 = img3.resize((w * 2, h * 2), Image.LANCZOS)
    t3 = try_ocr_pass(img3, '--psm 4 --oem 3')
    if t3: all_results.append(('fast', t3))
    
    # 策略4：仅英文数字
    t4 = try_ocr_pass(img1, '--psm 6 --oem 3', lang='eng')
    if t4: all_results.append(('eng', t4))
    
    # 从所有结果中提取金额
    amounts = []
    for name, text in all_results:
        found = re.findall(r'(?:¥|￥)?(\d+(?:\.\d{1,2})?)', text)
        for a in found:
            try:
                val = float(a)
                if 1 < val < 100000:
                    amounts.append(val)
            except:
                pass
    
    # 取出现频率最高的金额
    best_amount = None
    if amounts:
        val_counts = Counter(amounts)
        best_amount = val_counts.most_common(1)[0][0]
    
    # 合并所有文本
    combined = '\n'.join(r[1] for r in all_results)
    return combined, best_amount


@app.route('/api/ocr', methods=['POST'])
def ocr_receipt():
    if 'image' not in request.files:
        return jsonify({'ok': False, 'error': '未上传图片'})
    
    file = request.files['image']
    if not file.filename:
        return jsonify({'ok': False, 'error': '无效文件'})
    
    try:
        import io
        image = Image.open(io.BytesIO(file.read()))
        
        # 执行智能OCR
        ocr_text, best_amount = smart_ocr(image)
        
        result = {'amount': best_amount, 'category': '', 'note': ''}
        
        if best_amount:
            # 从完整文本中提取备注和分类
            clean = re.sub(r'[¥￥]?\d+(?:\.\d{1,2})?', '', ocr_text).strip()
            for cat in CATEGORIES:
                if cat in clean:
                    result['category'] = cat
                    clean = clean.replace(cat, '').strip()
                    break
            # 清理干扰文本
            clean = re.sub(r'[=+\-*/<>(){}【】\[\]：:：、，,。.！!？?]', ' ', clean).strip()
            result['note'] = ' '.join(clean.split())[:100]
        
        return jsonify({
            'ok': best_amount is not None,
            'ocr_text': ocr_text[:500],
            'parsed': result
        })
        
    except Exception as e:
        import traceback
        return jsonify({'ok': False, 'error': f'OCR识别失败: {str(e)}'})

# ── API：结算 ────────────────────────────────

@app.route('/api/trip/<int:trip_id>/settlement')
def get_settlement(trip_id):
    conn = get_db()
    
    members = conn.execute(
        'SELECT * FROM members WHERE trip_id = ?', (trip_id,)
    ).fetchall()
    
    expenses = conn.execute(
        'SELECT e.*, m.name as payer_name FROM expenses e '
        'JOIN members m ON e.member_id = m.id '
        'WHERE e.trip_id = ?', (trip_id,)
    ).fetchall()
    
    conn.close()
    
    if not members:
        return jsonify({'ok': False, 'error': '没有成员'})
    
    # 计算每人应付/应收
    member_ids = [m['id'] for m in members]
    paid = {m['id']: 0.0 for m in members}
    share = {m['id']: 0.0 for m in members}
    
    for e in expenses:
        payer_id = e['member_id']
        paid[payer_id] = paid.get(payer_id, 0) + e['amount']
        
        # 计算分摊
        split_ids = json.loads(e['split_members']) if e['split_members'] else member_ids
        if not split_ids:
            split_ids = member_ids
        
        per_person = e['amount'] / len(split_ids)
        for sid in split_ids:
            if sid in share:
                share[sid] += per_person
    
    # 计算结算
    balances = {}
    for m in members:
        net = paid[m['id']] - share[m['id']]
        balances[m['id']] = {
            'name': m['name'],
            'paid': round(paid[m['id']], 2),
            'should_pay': round(share[m['id']], 2),
            'balance': round(net, 2)  # 正数：别人欠他；负数：他欠别人
        }
    
    # 生成结算方案（贪心算法）
    debtor = [(mid, -b['balance']) for mid, b in balances.items() if b['balance'] < 0]
    creditor = [(mid, b['balance']) for mid, b in balances.items() if b['balance'] > 0]
    debtor.sort(key=lambda x: -x[1])
    creditor.sort(key=lambda x: -x[1])
    
    settlements = []
    i = j = 0
    while i < len(debtor) and j < len(creditor):
        d_id, d_amount = debtor[i]
        c_id, c_amount = creditor[j]
        
        settle = min(d_amount, c_amount)
        if settle > 0.01:
            settlements.append({
                'from_id': d_id,
                'from_name': balances[d_id]['name'],
                'to_id': c_id,
                'to_name': balances[c_id]['name'],
                'amount': round(settle, 2)
            })
        
        debtor[i] = (d_id, round(d_amount - settle, 2))
        creditor[j] = (c_id, round(c_amount - settle, 2))
        
        if debtor[i][1] < 0.01:
            i += 1
        if creditor[j][1] < 0.01:
            j += 1
    
    # 分类统计
    category_totals = {}
    total = 0
    for e in expenses:
        cat = e['category']
        category_totals[cat] = category_totals.get(cat, 0) + e['amount']
        total += e['amount']
    
    # 成员支出明细
    member_expenses = {}
    for m in members:
        member_expenses[m['id']] = {
            'name': m['name'],
            'expenses': []
        }
    for e in expenses:
        member_expenses[e['member_id']]['expenses'].append({
            'amount': e['amount'],
            'category': e['category'],
            'note': e['note']
        })
    
    return jsonify({
        'ok': True,
        'total': round(total, 2),
        'per_person': round(total / len(members), 2) if members else 0,
        'balances': balances,
        'settlements': settlements,
        'category_totals': category_totals,
        'member_expenses': member_expenses,
        'member_count': len(members)
    })

# ── API：多人同步 ───────────────────────────

@app.route('/api/trip/<int:trip_id>/sync/export')
def sync_export(trip_id):
    """导出自己的数据用于同步"""
    member_id = request.args.get('member_id', session.get('member_id'))
    if not member_id:
        return jsonify({'ok': False, 'error': '请先选择成员'})
    
    conn = get_db()
    expenses = conn.execute(
        'SELECT e.*, m.name as payer_name FROM expenses e '
        'JOIN members m ON e.member_id = m.id '
        'WHERE e.trip_id = ? AND e.member_id = ?',
        (trip_id, int(member_id))
    ).fetchall()
    
    member = conn.execute(
        'SELECT * FROM members WHERE id = ?', (int(member_id),)
    ).fetchone()
    conn.close()
    
    export_data = {
        'version': '1.0',
        'exported_at': datetime.now().isoformat(),
        'member': {
            'id': member['id'],
            'name': member['name']
        },
        'expenses': [{
            'amount': e['amount'],
            'category': e['category'],
            'note': e['note'],
            'split_type': e['split_type'],
            'split_members': json.loads(e['split_members']) if e['split_members'] else [],
            'expense_time': e['expense_time']
        } for e in expenses]
    }
    
    return jsonify({'ok': True, 'data': export_data})

@app.route('/api/trip/<int:trip_id>/sync/import', methods=['POST'])
def sync_import(trip_id):
    """导入其他成员的数据"""
    data = request.get_json()
    import_data = data.get('data', {})
    member_info = import_data.get('member', {})
    expenses = import_data.get('expenses', [])
    
    if not member_info or not expenses:
        return jsonify({'ok': False, 'error': '导入数据格式无效'})
    
    conn = get_db()
    try:
        # 查找或创建成员
        member = conn.execute(
            'SELECT * FROM members WHERE trip_id = ? AND name = ?',
            (trip_id, member_info['name'])
        ).fetchone()
        
        if not member:
            cursor = conn.execute(
                'INSERT INTO members (trip_id, name) VALUES (?, ?)',
                (trip_id, member_info['name'])
            )
            member_id = cursor.lastrowid
        else:
            member_id = member['id']
        
        imported_count = 0
        for exp in expenses:
            conn.execute(
                'INSERT INTO expenses (trip_id, member_id, amount, category, note, split_type, split_members, expense_time) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (trip_id, member_id, exp['amount'], exp['category'], exp['note'],
                 exp.get('split_type', 'equal'),
                 json.dumps(exp.get('split_members', []), ensure_ascii=False),
                 exp.get('expense_time', datetime.now().strftime('%Y-%m-%d %H:%M')))
            )
            imported_count += 1
        
        conn.commit()
        return jsonify({
            'ok': True,
            'member_name': member_info['name'],
            'imported': imported_count
        })
    except Exception as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/api/trip/<int:trip_id>/sync/reconcile')
def sync_reconcile(trip_id):
    """获取所有成员的汇总对账数据"""
    conn = get_db()
    
    expenses = conn.execute(
        'SELECT e.*, m.name as payer_name FROM expenses e '
        'JOIN members m ON e.member_id = m.id '
        'WHERE e.trip_id = ? ORDER BY m.name, e.expense_time',
        (trip_id,)
    ).fetchall()
    
    members = conn.execute(
        'SELECT * FROM members WHERE trip_id = ?', (trip_id,)
    ).fetchall()
    
    conn.close()
    
    # 按成员分组
    by_member = {}
    for m in members:
        by_member[m['id']] = {
            'name': m['name'],
            'expenses': [],
            'total': 0.0
        }
    
    for e in expenses:
        mid = e['member_id']
        if mid in by_member:
            by_member[mid]['expenses'].append({
                'amount': e['amount'],
                'category': e['category'],
                'note': e['note'],
                'expense_time': e['expense_time']
            })
            by_member[mid]['total'] += e['amount']
    
    overall_total = sum(m['total'] for m in by_member.values())
    
    return jsonify({
        'ok': True,
        'members': by_member,
        'overall_total': round(overall_total, 2)
    })

# ── 静态文件 ────────────────────────────────

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ── 网络信息API ──────────────────────────────

@app.route('/api/network')
def get_network_info():
    """返回局域网IP和连接方式"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        local_ip = '127.0.0.1'
    
    return jsonify({
        'ok': True,
        'lan_url': f'http://{local_ip}:5000',
        'local_url': 'http://127.0.0.1:5000',
        'note': '同WiFi下用LAN地址访问，不同网络用导出导入同步'
    })

# ── 启动 ──────────────────────────────────────

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('RENDER', '') == ''  # Render上关闭debug
    
    print(f"""
╔══════════════════════════════════════╗
║      Travel Expense Tracker v1.0     ║
║        出游花费统计工具                ║
╠══════════════════════════════════════╣
║  服务已启动，端口: {port}                ║
║  部署到 Render 后即可全球访问           ║
╚══════════════════════════════════════╝
    """)
    app.run(host='0.0.0.0', port=port, debug=debug)
