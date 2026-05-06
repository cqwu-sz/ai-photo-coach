# Web Demo 使用指南

PWA 演示版：Windows / Ubuntu 浏览器或 iPhone Safari 都能跑，体验和原生 iOS App 90% 接近。后端跑通了，前端就立刻能看到效果。

## 一、最快上手（仅 Windows + Chrome）

```powershell
# 1. 启动后端（默认 mock 模式，看 UI 不需要 Gemini key）
cd d:\project\ai-ios-photo\backend
.\.venv\Scripts\activate
uvicorn app.main:app --reload --port 8000

# 2. 浏览器打开 http://localhost:8000/web/
```

`localhost` 是 secure context，Chrome 会授权摄像头和陀螺仪。Windows 笔电没有陀螺仪 → 自动 fallback 成"鼠标移动模拟方向"，依旧能跑通整个流程。

## 二、看到的东西

```
首页 (index.html)
  人数 1-6+ / 质量档 fast/high / 风格关键词
  ↓ 点"开始环视拍摄"
环境扫描 (capture.html)
  实时摄像头预览 + 12 段覆盖环 + 录制按钮
  ↓ 录制 -> 抽 8-12 帧 -> POST /analyze
  ↓ Loading -> 推荐结果
拍摄方案 (result.html)
  环境分析卡片 + 1-3 个机位卡片
  每个机位：方向/俯仰/距离/构图/相机参数/AI 解释 + 姿势缩略图 + 每个人的细分动作
```

Mock 模式下两个机位都是同一个高低错位双人姿势——这是模拟数据的局限。**接上真 Gemini 后才有真实多样的推荐**，参考 [GEMINI_SETUP.md](GEMINI_SETUP.md)。

## 三、用真 Gemini 验证 AI 输出质量

```powershell
# backend/.env
MOCK_MODE=false
GEMINI_API_KEY=AIzaSy...
```

重启 uvicorn，再录一次 → 这次是真 AI 在分析你环境的实际画面。

**省钱建议**：Web 端默认用 `gemini-2.5-flash`，单次成本约 $0.01-0.03。如果 AI Studio 免费额度被打穿，错误码 `429` 就过几小时再来。

## 四、想用 iPhone Safari 测试（同局域网）

`getUserMedia` 和 `DeviceOrientationEvent` 在非 localhost 上**强制要 HTTPS**，所以 iPhone 直接访问 `http://192.168.x.x:8000/web/` 会被拒绝相机权限。解决：用 `mkcert` 给本机签自签名证书。

### 4.1 装 mkcert + 签证书（一次性，5 分钟）

```powershell
# 用 chocolatey
choco install mkcert
# 或 scoop
scoop install mkcert

mkcert -install                              # 把本地 CA 加到 Windows 信任库
mkdir d:\project\ai-ios-photo\backend\certs
cd d:\project\ai-ios-photo\backend\certs
mkcert -cert-file cert.pem -key-file key.pem 192.168.x.x localhost 127.0.0.1
```

记得把 `192.168.x.x` 改成你 Windows 在路由器内的实际 IP（PowerShell 里 `ipconfig` 查 IPv4）。

### 4.2 启动 HTTPS 后端

```powershell
cd d:\project\ai-ios-photo\backend
.\.venv\Scripts\activate
uvicorn app.main:app --port 8000 --host 0.0.0.0 `
  --ssl-keyfile=certs/key.pem --ssl-certfile=certs/cert.pem
```

### 4.3 让 iPhone 信任这个证书

mkcert 生成的根 CA 在 Windows 上的路径：

```powershell
mkcert -CAROOT
# 例如：C:\Users\xxx\AppData\Local\mkcert
```

把 `rootCA.pem` 通过 AirDrop / 邮件发到 iPhone，点开后**安装描述文件**：
- 设置 → 通用 → VPN 与设备管理 → 安装下载的"mkcert..."
- 设置 → 通用 → 关于本机 → 证书信任设置 → **打开 mkcert 这一行的开关**

### 4.4 iPhone Safari 打开

```
https://192.168.x.x:8000/web/
```

应该看到绿锁。允许相机和"运动与方向"权限，就能用 iPhone 真实陀螺仪 + 后置摄像头跑全流程了。

## 五、常见问题

**问题** | **原因 / 解决**
--- | ---
Chrome 提示"摄像头被禁用" | 第一次进允许；如果误拒了，地址栏左边小锁→网站权限→重置
点录制后没反应 | 先看浏览器控制台。如果 `getUserMedia` 报错，多半是其它 App 占着摄像头（如 Zoom、企业微信）。
环视一圈但覆盖环只亮 50% | 笔电没陀螺仪是正常的，移动鼠标会触发 fake heading。手机端则要授权"运动与方向"。
首页徽章一直显示 "MOCK 模式" | 后端 `.env` 里 `MOCK_MODE` 没有 `false`，或者改完没重启 uvicorn。
推荐结果两个机位长得一样 | mock 数据本来就这样。换成 Gemini 后会有真实多样性。
姿势缩略图加载失败 | 检查 `backend/app/knowledge/poses/` 下面有没有 PNG，没有就跑一次 `python scripts/generate_pose_thumbnails.py`。

## 六、目录速查

```
web/
├─ index.html         首页：选人数/质量/风格
├─ capture.html       环视拍摄页
├─ result.html        推荐结果页
├─ css/style.css
└─ js/
   ├─ store.js        sessionStorage 包装
   ├─ heading.js      DeviceOrientationEvent + 12 段覆盖环
   ├─ keyframe.js     getUserMedia 录像 + Canvas 抽帧 + 关键帧选择算法
   ├─ api.js          /analyze 和 /pose-library 客户端
   ├─ render.js       结果卡片渲染（与 iOS RecommendationView 同步）
   ├─ index.js        首页交互
   ├─ capture.js      拍摄页交互
   └─ result.js       结果页交互
```
