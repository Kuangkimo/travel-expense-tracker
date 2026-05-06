FROM python:3.11-slim

WORKDIR /app

# 复制依赖文件并安装Python包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

RUN mkdir -p uploads

# 配置百度OCR (推荐) 或 Tesseract (需在镜像内安装)
# 要启用Tesseract请取消下面注释:
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
#     && rm -rf /var/lib/apt/lists/*

CMD gunicorn app:app --workers 2 --threads 2 --timeout 120 --bind 0.0.0.0:$PORT
