# 后端 Dockerfile
FROM python:3.11-slim

# 国内镜像源（可通过 --build-arg 覆盖）
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn

WORKDIR /app

# 配置 apt 国内镜像 + 安装系统依赖
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# 配置 pip 国内镜像并安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt

# 复制应用代码（包括 modules/ 更新脚本）
COPY . .

# 创建数据目录
RUN mkdir -p /app/data/qihuo/database/inventory \
    /app/data/qihuo/database/positioning \
    /app/data/qihuo/database/term_structure \
    /app/data/qihuo/database/technical_analysis \
    /app/data/qihuo/database/basis \
    /app/data/qihuo/database/receipt \
    /app/data/qihuo/cache/analysis \
    /app/data/qihuo/trading_agents_results \
    /app/data/logs

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/system/health')"

# 启动命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
