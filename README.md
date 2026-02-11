# FRPC Launcher (Py + WebApp)

一个从 0 开始构建的 **frpc 启动器**，具备：

- frpc 可执行文件管理：**选择 / 导入 / 上传 / 下载**
- 链接创建：支持模板、手动输入 **INI / TOML**
- 安全登录机制：密码哈希、限流、登录失败锁定
- 配置中心：从 `cfg/` 目录读取配置与模板
- 现代 UI：玻璃拟态风格、主题切换、动效与流畅交互

## 快速启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

打开浏览器访问：`http://localhost:5000`

## 目录说明

- `app.py`: 主程序（认证、API、启动逻辑）
- `app/templates/`: 页面模板
- `app/static/`: CSS + JS
- `cfg/`: 应用配置、安全配置、模板配置
- `frpc_bin/`: frpc 可执行文件目录
- `runtime/`: 运行时渲染出的 ini/toml

## cfg 配置

应用首次启动会自动生成：

- `cfg/app.json`
- `cfg/security.json`
- `cfg/templates.json`

可按需调整安全策略、模板、下载地址。

## 安全说明

- 密码使用 `PBKDF2-HMAC (SHA-256)` 哈希存储（含随机盐）
- `/api/auth/login` 与 `/api/auth/register` 具备频率限制
- 多次失败触发临时锁定，防止恶意爆破

## 注意

`下载 frpc` 默认拉取 GitHub release 资产。若网络受限，可使用 **导入/上传** 本地 frpc 可执行文件。
