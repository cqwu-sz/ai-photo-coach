# 申请并配置 Gemini API Key

Google AI Studio 对个人开发者**完全免费**，每天有比较慷慨的额度（足够你做完 MVP 调试）。

## 一、申请 API Key（5 分钟）

1. 浏览器打开 https://aistudio.google.com/apikey
2. 用任意 Google 账号登录（如果国内打不开，需要科学上网）
3. 页面右上角点 **`Create API key`** → 选择一个 Google Cloud Project（没有就让它新建一个）
4. 复制生成出来的形如 `AIzaSy...` 的字符串。**这一串就是 Key，妥善保管。**

> 如果点 Create 后报 "billing" 相关错误，说明你选的 Cloud Project 里已经绑定了付费账号；换一个项目或新建一个项目即可保留免费档。

## 二、把 Key 写到后端 .env

在仓库根目录的 `backend/.env` 文件里（如果没有就 `cp .env.example .env`）：

```env
APP_ENV=local
APP_PORT=8000
LOG_LEVEL=INFO

# 关键改这两行：
MOCK_MODE=false
GEMINI_API_KEY=AIzaSy...你的 key 粘贴到这里...

GEMINI_MODEL_FAST=gemini-2.5-flash
GEMINI_MODEL_HIGH=gemini-2.5-pro
```

保存后**重启** uvicorn：

```powershell
# 在 backend 目录、激活了 venv 之后
uvicorn app.main:app --reload --port 8000
```

## 三、验证连通

启动后端后另开一个终端：

```powershell
curl http://localhost:8000/healthz
# 期望返回 {"status":"ok","mock_mode":false}
```

`mock_mode` 字段必须是 `false`，否则就是 .env 没有被加载。

## 四、免费额度

截至 2026 年 5 月，AI Studio 个人免费档的实际限制（以官网最新数据为准）：

| 模型 | RPM (每分钟请求数) | RPD (每天请求数) | 输入 token/天 |
| --- | --- | --- | --- |
| gemini-2.5-flash | 10-15 | 1500 | 1M+ |
| gemini-2.5-pro | 5-10 | 50-100 | 较少 |

对一个一人开发、一天调试 50-200 次分析的项目来说**完全够用**。如果撞额度上限，错误码会是 `429`，把 `quality_mode` 切回 `fast`、或者过几小时再试。

## 五、控制开销的建议

`backend/app/services/gemini_video.py` 已经做了几处省钱设计：

- 默认 `gemini-2.5-flash`（最便宜），`quality_mode=high` 时才升 Pro
- 客户端预先抽 8-12 帧，不直接传整段视频
- `temperature=0.4`，避免长篇大论
- `response_mime_type=application/json` 强制结构化，token 用得少

如果未来想自己卡上限，可以在 `Settings` 里加一个 `daily_budget_usd` 字段，配合简单的本地 SQLite 计数器，超额自动降级到 mock 或拒绝。

## 六、Key 泄露了怎么办（重要）

只要你在**任何地方** "明文" 出现过这串 key，就要当成已经泄露处理：
聊天记录、Slack、Discord、issue、commit message、截图、截图 OCR
之后被搜索引擎收录……所有这些都是泄露面。免费 key 被薅羊毛会很快
打到额度上限，付费 key 直接是真金白银损失。

**第一时间做这三件事：**

1. **撤销 key**：打开 https://aistudio.google.com/apikey ，找到这个
   key，右侧三个点 → `Delete`。删除后立刻失效，所有用它的请求会
   `403 PERMISSION_DENIED`。
2. **生成新 key**：同一个页面 `Create API key`，新 key 会立即可用。
3. **更新 backend/.env**：把新 key 粘上去，重启 uvicorn。`.env`
   文件已经在 `.gitignore` 里，不会被推到 GitHub，但你最好也跑下面
   两个命令再确认一次：

   ```powershell
   git ls-files | Select-String -Pattern "\.env$"
   # 期望返回空。如果列出了 backend/.env，立刻 git rm --cached 并改
   # .gitignore，再 force-push 重写历史（git filter-repo / BFG）。
   ```

**预防措施：**

- 永远只在 `.env` 文件里放 key，文件在 `.gitignore`。
- 把 key 给别人调试时，用临时 key 或者直接给 mock 数据。
- 在公开仓库的 README、issue、截图里**绝对不要**带真 key——哪怕
  你只露了前缀（`AIzaSyC...`），结合服务名 Google 也能反查到一些
  历史使用模式，更别说工具会自动扫公开仓库。
- 如果你的 key 进过 Cursor / GPT 这类 AI 助手的对话窗口，对话
  历史可能保留较长时间，建议主动撤销重发一次更安心。

