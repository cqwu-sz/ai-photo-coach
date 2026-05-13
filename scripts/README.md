# 本地开发脚本

## 同 WiFi 下用 iPhone 调试笔记本上的后端

### 1. 笔记本（Windows）

双击 `scripts/start-local-backend.cmd`：
- 自动找局域网 IP（`192.168.x.x` / `10.x.x.x` / `172.x.x.x`）
- 自动加 Windows 防火墙规则放行 8000（首次会弹 UAC）
- 用 `backend/.venv` 启 uvicorn，监听 `0.0.0.0:8000`
- 日志直接打在窗口里，关窗口就停

> 第一次跑前先建 venv：
> ```cmd
> cd backend
> python -m venv .venv
> .venv\Scripts\pip install -r requirements.txt
> ```

兜底停止：双击 `scripts/stop-local-backend.cmd`（kill 占用 8000 的进程）。

### 2. iPhone（同一 WiFi）

不用 rebuild IPA。两种方式：

**方式 A — App 内动态切换（推荐）**

1. 用管理员账号登录
2. 「管理员审计」→「服务器端点」→「本地覆盖」
3. 填脚本窗口里打印的那行：`http://192.168.x.x:8000`
4. 立即生效，所有后续请求改走笔记本

**方式 B — 重新打 IPA 固定 baseURL（给别人测才需要）**

把 `API_BASE_URL` 写进 `ios/project.yml`（仅 Debug），
再 `xcodegen generate && xcodebuild ...` 出 IPA。
日常开发不需要这条路。

### 3. 验证连通性

iPhone Safari 打开 `http://192.168.x.x:8000/healthz` 应返回 JSON。
打不开 → 检查：
- 笔记本和手机是否同一 WiFi（一些公司 WiFi 客户端隔离）
- Windows 防火墙是否真放行（`netsh advfirewall firewall show rule name="AIPhotoCoach Backend"`）
- 路由器是否 AP 隔离（`Client Isolation` / `AP Isolation`）

### 4. ATS（iOS 14+ 安全策略）

`project.yml` 里 `NSAllowsLocalNetworking: true` 已开，
RFC 1918 段（10/8、172.16/12、192.168/16）的 HTTP 不会被 ATS 拦。
如果你给后端套了不在 RFC 1918 的 IP，需要把 `NSAllowsArbitraryLoads: true` 临时开起来再 build。
