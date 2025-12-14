# ☁️ 部署指南 (Deploy to Cloud)

由于 Web Reader AI 使用了 Playwright (需要无头浏览器)，推荐使用支持 **Docker** 的平台进行部署。

我们推荐使用 **Hugging Face Spaces**，它提供免费的 Docker 容器托管（2 vCPU, 16GB RAM），非常适合本项目。

## 方案：部署到 Hugging Face Spaces (推荐)

### 1. 准备工作
确保你已经注册了 [Hugging Face](https://huggingface.co/) 账号。

### 2. 创建 Space
1. 登录 Hugging Face，点击右上角头像 -> **New Space**。
2. **Space name**: 输入 `web-reader-ai` (或任意名称)。
3. **License**: 选择 `MIT`。
4. **Select the Space SDK**: 选择 **Docker** (重要!)。
5. **Space Hardware**: 保持默认的 `Free` (2 vCPU · 16GB · CPU basic)。
6. 点击 **Create Space**。

### 3. 上传代码
创建成功后，你有两种方式上传代码：

#### 方式 A: 通过网页上传 (最简单)
1. 在 Space 页面，点击顶部 **Files** 标签。
2. 点击 **Add file** -> **Upload files**。
3. 将本地 `web_reader` 文件夹内的以下文件拖进去：
   - `Dockerfile`
   - `requirements.txt`
   - `server.py`
   - `crawler.py`
   - `config.py`
   - `utils.py`
   - `static/` (整个文件夹)
4. 点击底部 **Commit changes to main**。

#### 方式 B: 通过 Git 推送 (推荐)
1. 按照页面提示的指令：
   ```bash
   git clone https://huggingface.co/spaces/YOUR_USERNAME/web-reader-ai
   cd web-reader-ai
   # 将你本地的代码复制到这个文件夹
   git add .
   git commit -m "Deploy app"
   git push
   ```

### 4. 等待构建
上传后，点击 **App** 标签。你会看到状态显示 `Building`。
由于需要下载浏览器镜像，第一次构建可能需要几分钟。
当状态变为 `Running` 时，你的应用就上线了！

---

## 其他平台 (Render)

如果你更喜欢 Render：
1. 注册 [Render](https://render.com/)。
2. Create New -> **Web Service**。
3. 连接你的 GitHub 仓库。
4. Runtime 选择 **Docker**。
5. Plan 选择 **Free**。
6. 点击 Create Web Service。
   
*注意：Render 免费版在 15 分钟无操作后会休眠，唤醒需要几十秒。*
