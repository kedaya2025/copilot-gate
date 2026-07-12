# Token 手动更新指南

> 当容器内 headless 自动刷新失败时（如 profile 过期、网络问题），按本文档手动更新 token。

---

## 何时需要手动更新

- 企业账号 JWT 过期（约 90 分钟），且容器内 headless 浏览器刷新失败
- `curl https://copilot.us.kg/v1/chat/completions` 返回 401 或 502
- 容器日志中出现 `BrowserType.launch_persistent_context` 或 `Not signed in` 错误

---
cd E:\Workspace\Projects\Windows-Copilot-API
.\venv\Scripts\python.exe -m copilot login

## 步骤

### 1. 在本地机器登录

在**有图形界面**的机器上（你的 Windows 开发机）：

```bash
cd Windows-Copilot-API

# 激活虚拟环境
venv\Scripts\Activate.ps1

# 企业账号登录
COPILOT_URL=https://m365.cloud.microsoft python -m copilot login
```

浏览器会弹出，登录你的 Microsoft 365 企业账号。登录成功后浏览器自动关闭，`session/token.json` 会更新。

### 2. 上传 token.json 到服务器

```powershell
# 方法 A：SCP 直传（推荐）
scp -P 1314 session/token.json root@43.162.93.34:/srv/workspace/services/copilot-gate/session/token.json

# 方法 B：base64 传输（SCP 不可用时）
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("session/token.json"))
# 然后通过 SSH 执行：
# echo '<base64>' | base64 -d > /srv/workspace/services/copilot-gate/session/token.json
```

### 3. 重启容器

```bash
ssh root@43.162.93.34 -p 1314
cd /srv/workspace/services/copilot-gate
docker compose restart copilot-api
```

### 4. 验证

```bash
# 无 API Key 时返回 401
curl https://copilot.us.kg/v1/models

# 带 API Key
curl -H "Authorization: Bearer sk-copilot-<your-key>" https://copilot.us.kg/v1/models

# 测试 chat
curl -X POST https://copilot.us.kg/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-copilot-<your-key>" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello"}]}'
```

---

## 同时更新浏览器 profile（可选，但推荐）

如果只更新 `token.json`，token 下次过期时仍需手动操作。同时上传 `session/profile/` 可以让容器 headless 自动刷新：

```powershell
# 打包 profile
tar -czf profile.tar.gz -C session profile

# 上传
scp -P 1314 profile.tar.gz root@43.162.93.34:/srv/workspace/services/copilot-gate/profile.tar.gz

# 在服务器上解压
ssh root@43.162.93.34 -p 1314 "cd /srv/workspace/services/copilot-gate && tar xzf profile.tar.gz && rm profile.tar.gz && docker compose restart copilot-api"
```

上传 profile 后，容器会在 token 过期时自动启动 headless Chromium 刷新 token，无需人工干预。
