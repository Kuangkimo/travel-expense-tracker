#!/bin/bash
# 出游账本 - 启动脚本
echo "📱 启动出游账本..."
cd "$(dirname "$0")"
if [ ! -f database.db ]; then
    echo "  首次启动，自动创建数据库..."
fi
python3 app.py
