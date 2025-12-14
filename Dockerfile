# 使用官方 Playwright 镜像 (包含 Python 和 浏览器环境)
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 单独安装 Chromium (虽然基础镜像包含环境，但最好显示安装一下以防万一)
RUN playwright install chromium

# 复制项目代码
COPY . .

# 创建输出目录并设置权限 (Hugging Face 需要非 root 用户权限写入)
RUN mkdir -p output && chmod 777 output

# 暴露端口 (Hugging Face Spaces 默认使用 7860)
EXPOSE 7860

# 启动命令
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
