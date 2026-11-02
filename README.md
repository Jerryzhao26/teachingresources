# 比伯斯教学资源中心

教师端为 GitHub Pages 静态网站；管理员端在 Mac 本机运行。公开仓库中的 `docs/data` 只包含 AES-256-GCM 加密后的目录与 PDF。密码通过 PBKDF2-SHA256（600,000 次）派生，不写入仓库。

## 本机启动

需要 Python 3 和 `cryptography`：`python3 -m pip install cryptography`。双击 `run-admin.command`，或在项目目录运行 `python3 -m admin.server`。浏览器访问 `http://127.0.0.1:8765`。

首次输入至少 12 字符的教师统一密码。选择 PDF 或 DOCX 批量导入；DOCX 由本机 Microsoft Word 导出为 PDF。设置分类、是否显示下载按钮、更新说明，然后保存公告与更新。若替换旧资源，先在资源列表选择替换目标，再导入一个新文件。点击“发布”将 `docs` 推送到 GitHub 仓库。

GitHub Pages 设为从 `main` 分支的 `/docs` 目录发布。教师页面需通过 HTTPS 访问，浏览器 Web Crypto API 在安全上下文中运行。

## 安全边界

加密保护公开仓库中的 PDF 正文和资源目录。统一密码一旦共享或泄露，持有人就能解密所有资源。浏览器为了阅读、打印，必须拿到解密后的 PDF；即使没有下载按钮，用户也可能借助浏览器工具、打印或截图保存内容。下载许可是界面与使用约定，不是技术上的防复制控制。更强的个人权限或撤销权限需要服务端登录与授权。旧版本密文保留在 Git 历史中；更换密码需要重新加密所有文件并清理历史才可抵御已知旧密码。

## 测试

`python3 -m unittest discover -s tests -v`
