# Athena Skills Manager

共享 Skills 管理工具。所有登录用户通过 Athena `/api/me` 认证，并共享同一套 Skills；每次修改都会记录操作人和版本。

## 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn skill_manager.app:app --reload
```

生产环境将 `/tools2/skill-manager/` 反向代理到容器的 8000 端口，并将 `/data` 挂载为持久化卷。`ATHENA_ME_URL` 可通过环境变量覆盖。
