FROM python:3.11-slim

WORKDIR /app

# 安装 Tesseract OCR + 中文语言包（失败不影响部署）
RUN apt-get update -qq 2>/dev/null; \
    apt-get install -y -qq --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-chi-tra \
        tesseract-ocr-eng \
        2>/dev/null || true; \
    rm -rf /var/lib/apt/lists/*

# 复制依赖并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建必要目录
RUN mkdir -p uploads

# Zeabur 会自动注入 PORT 环境变量
CMD gunicorn app:app --workers 2 --threads 2 --timeout 60 --bind 0.0.0.0:$PORT
