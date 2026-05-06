FROM python:3.11-slim

WORKDIR /app

# 安装 Tesseract OCR + 中文语言包
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 创建数据目录
RUN mkdir -p uploads

# 非root用户运行（安全）
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Render 会自动注入 PORT 环境变量
CMD gunicorn app:app --workers 2 --threads 2 --timeout 60 --bind 0.0.0.0:$PORT
