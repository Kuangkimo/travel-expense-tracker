// ══════════════════════════════════════
// Travel Expense Tracker - Shared JS
// 全局工具函数
// ══════════════════════════════════════

// ── Toast ──
function showToast(msg) {
    let t = document.getElementById('toast');
    if (!t) {
        t = document.createElement('div');
        t.id = 'toast';
        t.className = 'toast';
        document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.remove('show'), 2500);
}

// ── 返回 ──
function goBack() {
    if (document.referrer) {
        window.history.back();
    } else {
        window.location = '/';
    }
}

// ── 格式化金额 ──
function formatMoney(amount) {
    return '¥' + parseFloat(amount).toFixed(2);
}

// ── 格式化时间 ──
function formatTime(t) {
    if (!t) return '';
    const d = new Date(t);
    if (isNaN(d.getTime())) return t;
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const hour = String(d.getHours()).padStart(2, '0');
    const min = String(d.getMinutes()).padStart(2, '0');
    return `${month}-${day} ${hour}:${min}`;
}

// ── 分类图标 ──
function getCategoryIcon(cat) {
    const icons = {
        '餐饮': '🍽️', '交通': '🚗', '住宿': '🏠',
        '门票': '🎫', '购物': '🛍️', '酒水/娱乐': '🍻', '其他': '📌'
    };
    return icons[cat] || '📌';
}

// ── 获取URL参数 ──
function getUrlParam(name) {
    const params = new URLSearchParams(window.location.search);
    return params.get(name);
}

// ── 防抖 ──
function debounce(fn, delay = 300) {
    let timer;
    return function(...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

// ══════════════════════════════════════
// PWA Service Worker 注册（如果存在）
// ══════════════════════════════════════
if ('serviceWorker' in navigator) {
    // 不做自动注册，保留扩展能力
}
