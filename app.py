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

from werkzeug.security import generate_password_hash, check_password_hash

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
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            nickname TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS user_trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            trip_id INTEGER NOT NULL,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
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

# ── API：用户 ────────────────────────────────

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or len(username) < 2:
        return jsonify({'ok': False, 'error': '用户名至少2个字符'})
    if not password or len(password) < 4:
        return jsonify({'ok': False, 'error': '密码至少4个字符'})
    
    conn = get_db()
    try:
        existing = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if existing:
            return jsonify({'ok': False, 'error': '用户名已存在'})
        
        cursor = conn.execute(
            'INSERT INTO users (username, password_hash) VALUES (?, ?)',
            (username, generate_password_hash(password))
        )
        user_id = cursor.lastrowid
        conn.commit()
        
        session['user_id'] = user_id
        session['username'] = username
        
        return jsonify({'ok': True, 'user': {'id': user_id, 'username': username}})
    except Exception as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'ok': False, 'error': '用户名或密码错误'})
    
    session['user_id'] = user['id']
    session['username'] = user['username']
    
    return jsonify({
        'ok': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'nickname': user['nickname'],
            'avatar_url': user['avatar_url']
        }
    })

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    return jsonify({'ok': True})

@app.route('/api/me')
def get_me():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': '未登录'})
    
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'ok': False, 'error': '用户不存在'})
    
    return jsonify({
        'ok': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'nickname': user['nickname'],
            'avatar_url': user['avatar_url']
        }
    })

@app.route('/api/profile/update', methods=['POST'])
def update_profile():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': '请先登录'})
    
    data = request.get_json()
    nickname = data.get('nickname', '').strip()
    
    conn = get_db()
    try:
        conn.execute('UPDATE users SET nickname = ? WHERE id = ?', (nickname, user_id))
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/api/profile/avatar', methods=['POST'])
def upload_avatar():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': '请先登录'})
    
    if 'avatar' not in request.files:
        return jsonify({'ok': False, 'error': '请选择图片'})
    
    file = request.files['avatar']
    if not file.filename:
        return jsonify({'ok': False, 'error': '请选择图片'})
    
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.gif'):
        return jsonify({'ok': False, 'error': '仅支持jpg/png/gif'})
    
    filename = f'avatar_{user_id}{ext}'
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    
    avatar_url = f'/uploads/{filename}'
    
    conn = get_db()
    conn.execute('UPDATE users SET avatar_url = ? WHERE id = ?', (avatar_url, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'ok': True, 'avatar_url': avatar_url})

@app.route('/api/my/trips')
def get_my_trips():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': '未登录'})
    
    conn = get_db()
    trips = conn.execute("""
        SELECT t.*, ut.joined_at as joined_at
        FROM trips t
        JOIN user_trips ut ON ut.trip_id = t.id
        WHERE ut.user_id = ?
        ORDER BY ut.joined_at DESC
    """, (user_id,)).fetchall()
    conn.close()
    
    return jsonify({
        'ok': True,
        'trips': [{
            'id': t['id'],
            'name': t['name'],
            'share_code': t['share_code'],
            'created_at': t['created_at'],
            'joined_at': t['joined_at']
        } for t in trips]
    })

@app.route('/profile')
def profile_page():
    return render_template('profile.html')

@app.route('/trip/<int:trip_id>')
def trip_dashboard(trip_id):
    member_id = session.get('member_id', 0)
    return render_template('trip.html', trip_id=trip_id, categories=CATEGORIES, icons=CATEGORY_ICONS, current_member_id=member_id)

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
        
        # 关联登录用户
        user_id = session.get('user_id')
        if user_id:
            conn.execute(
                'INSERT OR IGNORE INTO user_trips (user_id, trip_id) VALUES (?, ?)',
                (user_id, trip_id)
            )
            conn.commit()
        
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
        
        # 关联登录用户
        user_id = session.get('user_id')
        if user_id:
            conn.execute(
                'INSERT OR IGNORE INTO user_trips (user_id, trip_id) VALUES (?, ?)',
                (user_id, trip['id'])
            )
            conn.commit()
        
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
    current_member_id = session.get('member_id')
    if not current_member_id:
        return jsonify({'ok': False, 'error': '请先加入行程'})
    
    conn = get_db()
    try:
        expense = conn.execute(
            'SELECT * FROM expenses WHERE id = ? AND trip_id = ?',
            (expense_id, trip_id)
        ).fetchone()
        if not expense:
            return jsonify({'ok': False, 'error': '支出不存在'})
        if expense['member_id'] != current_member_id:
            return jsonify({'ok': False, 'error': '只能删除自己记录的支出'})
        
        conn.execute(
            'DELETE FROM expenses WHERE id = ? AND trip_id = ?',
            (expense_id, trip_id)
        )
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/api/trip/<int:trip_id>/expense/<int:expense_id>/edit', methods=['POST'])
def edit_expense(trip_id, expense_id):
    data = request.get_json()
    
    # 校验：只能修改自己创建的支出
    current_member_id = session.get('member_id')
    if not current_member_id:
        return jsonify({'ok': False, 'error': '请先加入行程'})
    
    conn = get_db()
    try:
        expense = conn.execute(
            'SELECT * FROM expenses WHERE id = ? AND trip_id = ?',
            (expense_id, trip_id)
        ).fetchone()
        if not expense:
            return jsonify({'ok': False, 'error': '支出不存在'})
        if expense['member_id'] != current_member_id:
            return jsonify({'ok': False, 'error': '只能修改自己记录的支出'})
        
        amount = float(data.get('amount', 0))
        category = data.get('category', expense['category'])
        note = data.get('note', expense['note'])
        split_type = data.get('split_type', expense['split_type'])
        split_members = data.get('split_members', '[]')
        expense_time = data.get('expense_time', expense['expense_time'])
        
        if amount <= 0:
            return jsonify({'ok': False, 'error': '金额必须大于0'})
        if isinstance(split_members, str):
            split_members = json.loads(split_members)
        
        conn.execute(
            'UPDATE expenses SET amount=?, category=?, note=?, split_type=?, split_members=?, expense_time=? WHERE id=? AND trip_id=?',
            (amount, category, note, split_type, json.dumps(split_members, ensure_ascii=False), 
             expense_time or datetime.now().strftime('%Y-%m-%d %H:%M'), expense_id, trip_id)
        )
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

# ── OCR引擎 ────────────────────────────────
# 百度OCR（优先）+ Tesseract（降级备用）

import base64

def baidu_ocr_access_token():
    """获取百度OCR access_token（缓存到全局变量）"""
    api_key = os.environ.get('BAIDU_OCR_API_KEY', '')
    secret_key = os.environ.get('BAIDU_OCR_SECRET_KEY', '')
    if not api_key or not secret_key:
        return None
    try:
        import requests as req
        r = req.get(
            f'https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials&client_id={api_key}&client_secret={secret_key}',
            timeout=5
        )
        return r.json().get('access_token')
    except:
        return None

def baidu_ocr(image_bytes):
    """调用百度OCR通用文字识别（免费500次/天）"""
    token = baidu_ocr_access_token()
    if not token:
        return None
    
    b64 = base64.b64encode(image_bytes).decode('utf-8')
    try:
        import requests as req
        r = req.post(
            f'https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic?access_token={token}',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={'image': b64},
            timeout=10
        )
        result = r.json()
        if 'words_result' in result:
            texts = [w['words'] for w in result['words_result']]
            return '\n'.join(texts)
        return None
    except:
        return None

def preprocess_image_for_ocr(img, scale=3):
    """使用PIL进行7步预处理，专为支付截图优化（Tesseract降级用）"""
    w, h = img.size
    img = img.resize((w * scale, h * scale), Image.LANCZOS)
    if img.mode != 'L':
        img = img.convert('L')
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    img = img.filter(ImageFilter.SHARPEN)
    img = img.filter(ImageFilter.SHARPEN)
    blurred = img.filter(ImageFilter.GaussianBlur(radius=15))
    pixels = img.load()
    blur_px = blurred.load()
    result = Image.new('L', img.size, 255)
    result_px = result.load()
    for y in range(img.height):
        for x in range(img.width):
            if pixels[x, y] < blur_px[x, y] - 15:
                result_px[x, y] = 0
            else:
                result_px[x, y] = 255
    result = result.filter(ImageFilter.MedianFilter(size=3))
    return result

def try_ocr_pass(image, config_str, lang='chi_sim+eng'):
    try:
        text = pytesseract.image_to_string(image, lang=lang, config=config_str)
        return text.strip()
    except:
        return ''

def tesseract_fallback(image):
    """Tesseract降级识别（多策略）"""
    from collections import Counter
    all_results = []
    img1 = preprocess_image_for_ocr(image, scale=3)
    t1 = try_ocr_pass(img1, '--psm 6 --oem 3 -c tessedit_char_whitelist=0123456789.')
    if t1: all_results.append(('digits', t1))
    t2 = try_ocr_pass(img1, '--psm 6 --oem 3')
    if t2: all_results.append(('full', t2))
    img3 = image.copy()
    if img3.mode != 'L': img3 = img3.convert('L')
    enhancer = ImageEnhance.Contrast(img3)
    img3 = enhancer.enhance(1.5)
    w, h = img3.size
    img3 = img3.resize((w * 2, h * 2), Image.LANCZOS)
    t3 = try_ocr_pass(img3, '--psm 4 --oem 3')
    if t3: all_results.append(('fast', t3))
    t4 = try_ocr_pass(img1, '--psm 6 --oem 3', lang='eng')
    if t4: all_results.append(('eng', t4))
    
    amounts = []
    for name, text in all_results:
        found = re.findall(r'(?:¥|￥)?(\d+(?:\.\d{1,2})?)', text)
        for a in found:
            try:
                val = float(a)
                if 1 < val < 100000: amounts.append(val)
            except: pass
    best_amount = None
    if amounts:
        val_counts = Counter(amounts)
        best_amount = val_counts.most_common(1)[0][0]
    combined = '\n'.join(r[1] for r in all_results)
    return combined, best_amount


def parse_ocr_full_text(text):
    """从OCR文本中提取金额、分类、备注"""
    if not text:
        return {'amount': None, 'category': '', 'note': ''}
    
    amounts = re.findall(r'(?:¥|￥)?(\d+(?:\.\d{1,2})?)', text)
    best_amount = None
    for a in amounts:
        try:
            val = float(a)
            if 1 < val < 100000:
                best_amount = val
                break
        except: pass
    
    clean = re.sub(r'[¥￥]?\d+(?:\.\d{1,2})?', '', text).strip()
    category = ''
    for cat in CATEGORIES:
        if cat in clean:
            category = cat
            clean = clean.replace(cat, '').strip()
            break
    clean = re.sub(r'[=+\-*/<>(){}【】\[\]：:：、，,。.！!？?\s]+', ' ', clean).strip()
    
    return {'amount': best_amount, 'category': category, 'note': clean[:100]}


@app.route('/api/ocr', methods=['POST'])
def ocr_receipt():
    if 'image' not in request.files:
        return jsonify({'ok': False, 'error': '未上传图片'})
    
    file = request.files['image']
    if not file.filename:
        return jsonify({'ok': False, 'error': '无效文件'})
    
    try:
        image_data = file.read()
        image = Image.open(io.BytesIO(image_data))
        
        ocr_source = 'tesseract'
        ocr_text, best_amount = tesseract_fallback(image)
        
        # 尝试百度OCR（若有API Key）
        baidu_text = baidu_ocr(image_data)
        if baidu_text:
            ocr_source = 'baidu'
            ocr_text = baidu_text
            result = parse_ocr_full_text(baidu_text)
            best_amount = result['amount']
        else:
            result = {'amount': best_amount, 'category': '', 'note': ''}
            if best_amount:
                clean = re.sub(r'[¥￥]?\d+(?:\.\d{1,2})?', '', ocr_text).strip()
                for cat in CATEGORIES:
                    if cat in clean:
                        result['category'] = cat
                        clean = clean.replace(cat, '').strip()
                        break
                clean = re.sub(r'[=+\-*/<>(){}【】\[\]：:：、，,。.！!？?]', ' ', clean).strip()
                result['note'] = ' '.join(clean.split())[:100]
        
        result['ocr_source'] = ocr_source
        
        return jsonify({
            'ok': best_amount is not None,
            'ocr_text': ocr_text[:500],
            'parsed': result
        })
        
    except Exception as e:
        return jsonify({'ok': False, 'error': f'OCR识别失败: {str(e)}'})

# ── API：结算 ────────────────────────────────

def calc_settlement(trip_id):
    """计算结算数据，返回 dict 或 {'ok': False, 'error': ...}"""
    conn = get_db()
    try:
        members = conn.execute(
            'SELECT * FROM members WHERE trip_id = ?', (trip_id,)
        ).fetchall()
        
        expenses = conn.execute(
            'SELECT e.*, m.name as payer_name FROM expenses e '
            'JOIN members m ON e.member_id = m.id '
            'WHERE e.trip_id = ?', (trip_id,)
        ).fetchall()
        
        if not members:
            return {'ok': False, 'error': '没有成员'}
        
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
                'balance': round(net, 2)
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
        
        # 自付 vs 分摊统计
        self_paid_total = 0.0
        split_total = 0.0
        self_paid_expenses = []
        split_expenses = []
        for e in expenses:
            is_self = e['split_type'] == 'self'
            amt = e['amount']
            if is_self:
                self_paid_total += amt
                self_paid_expenses.append(e)
            else:
                split_total += amt
                split_expenses.append(e)
        
        # 成员支出明细（带 split_type 标识）
        member_expenses = {}
        for m in members:
            member_expenses[m['id']] = {
                'name': m['name'],
                'self_paid': 0.0,
                'split_amount': 0.0,
                'expenses': []
            }
        for e in expenses:
            is_self = e['split_type'] == 'self'
            member_expenses[e['member_id']]['expenses'].append({
                'amount': e['amount'],
                'category': e['category'],
                'note': e['note'],
                'split_type': e['split_type']
            })
            if is_self:
                member_expenses[e['member_id']]['self_paid'] += e['amount']
            else:
                member_expenses[e['member_id']]['split_amount'] += e['amount']
        
        return {
            'ok': True,
            'total': round(total, 2),
            'per_person': round(total / len(members), 2) if members else 0,
            'balances': balances,
            'settlements': settlements,
            'category_totals': category_totals,
            'member_expenses': member_expenses,
            'member_count': len(members),
            'self_paid_total': round(self_paid_total, 2),
            'split_total': round(split_total, 2)
        }
    finally:
        conn.close()


@app.route('/api/trip/<int:trip_id>/settlement')
def get_settlement(trip_id):
    return jsonify(calc_settlement(trip_id))

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

# ── 结算记录 ────────────────────────────────

@app.route('/api/trip/<int:trip_id>/settlement/save', methods=['POST'])
def save_settlement(trip_id):
    """保存当前结算快照"""
    data = request.get_json()
    note = data.get('note', '')
    
    snap = calc_settlement(trip_id)
    
    if not snap.get('ok'):
        return jsonify({'ok': False, 'error': '无法获取结算数据'})
    
    conn = get_db()
    try:
        conn.execute(
            'INSERT INTO settlements (trip_id, snapshot_json, note) VALUES (?, ?, ?)',
            (trip_id, json.dumps(snap, ensure_ascii=False), note)
        )
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/api/trip/<int:trip_id>/settlement/history')
def settlement_history(trip_id):
    """获取结算历史"""
    conn = get_db()
    records = conn.execute(
        'SELECT * FROM settlements WHERE trip_id = ? ORDER BY created_at DESC LIMIT 20',
        (trip_id,)
    ).fetchall()
    conn.close()
    
    return jsonify({
        'ok': True,
        'records': [{
            'id': r['id'],
            'snapshot': json.loads(r['snapshot_json']),
            'note': r['note'],
            'created_at': r['created_at']
        } for r in records]
    })

@app.route('/api/trip/<int:trip_id>/settlement/<int:record_id>/delete', methods=['POST'])
def delete_settlement_record(trip_id, record_id):
    """删除结算记录"""
    conn = get_db()
    try:
        conn.execute(
            'DELETE FROM settlements WHERE id = ? AND trip_id = ?',
            (record_id, trip_id)
        )
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        conn.close()

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
