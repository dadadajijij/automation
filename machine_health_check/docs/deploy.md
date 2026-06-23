# 部署说明

本文说明 `machine_health_check` 当前的本地执行、systemd 安装方式，以及与根仓库 `project-ports.json` 的联动关系。

## 1. 本地执行

在项目目录下直接运行：

```bash
bash scripts/run_check.sh
```

这个脚本会：

1. 切到 `machine_health_check/`
2. 如果存在 `.env`，先导入环境变量
3. 执行 `PYTHONPATH=src python3 -m health_check --config config/default.json`

如果只想看 stdout、不写 `reports/`，可以直接运行：

```bash
PYTHONPATH=src python3 -m health_check --config config/default.json --no-write
```

## 2. 配置文件与环境变量

默认配置文件：

- [`config/default.json`](../config/default.json)

当前支持通过 `.env` 注入企业微信 webhook。推荐写法：

```bash
MACHINE_HEALTH_CHECK_WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
```

读取顺序是：

1. 先看 `config/default.json` 里的 `notifications.wecom_webhook`
2. 如果为空，再读 `.env` / 环境变量中的 `MACHINE_HEALTH_CHECK_WECOM_WEBHOOK`

仓库里当前没有 `.env.example`，因此文档不再引用示例文件。

## 3. 输出目录

默认输出：

- `reports/latest.json`
- `reports/history/report-<UTC 时间戳>.json`

目录由配置控制：

- `report_dir`
- `history_dir`

当前不会单独写日志文件；systemd 场景请直接看 `journalctl`。

## 4. 安装 systemd 定时任务

当前已提供：

- [machine-health-check.service](/home/devops/ka/automation/machine_health_check/deploy/machine-health-check.service)
- [machine-health-check.timer](/home/devops/ka/automation/machine_health_check/deploy/machine-health-check.timer)

安装命令：

```bash
bash scripts/install_systemd.sh
```

脚本会执行：

1. 复制 service / timer 到 `/etc/systemd/system/`
2. `sudo systemctl daemon-reload`
3. `sudo systemctl enable --now machine-health-check.timer`
4. `sudo systemctl start machine-health-check.service`
5. `sudo systemctl status machine-health-check.timer --no-pager`

当前 timer 配置为：

- 每 30 分钟执行一次
- `Persistent=true`

当前 service 配置为：

- `WorkingDirectory=/home/devops/ka/automation/machine_health_check`
- `EnvironmentFile=-/home/devops/ka/automation/machine_health_check/.env`
- 执行用户 `devops`
- 入口命令 `bash scripts/run_check.sh`

## 5. 企业微信告警

当满足以下条件时，每次巡检都会发送一条企业微信文本消息：

- `notifications.enabled = true`
- 最终拿到了非空 webhook

消息内容包括：

- 总状态
- 各巡检项状态与摘要
- 所有非 `ok` 的服务项明细

发送结果会打印到 stdout，systemd 场景下也能在 `journalctl` 里看到。

## 6. 服务健康检查与根仓库联动

服务检查默认是开启的，配置位于：

- `config/default.json -> service_checks`

当前会检查两类服务：

### 6.1 宿主机服务

默认只配置了：

- `ka_tools`

检查内容：

- `ss -ltn` 端口监听
- TCP 探测
- HTTP 健康检查

### 6.2 Podman 容器服务

容器服务来源于根仓库：

- [`project-ports.json`](../../project-ports.json)

默认排除：

- `ka-tools`

因此这个巡检工具和部署流水线是联动的：

- 部署流水线给项目分配或维护端口映射
- 巡检工具根据这些映射自动发现容器服务

容器服务检查依赖：

- `project-ports.json` 可读
- 当前执行用户能访问本地监听端口
- 当前执行用户能执行 `podman ps --format json`

如果 `podman ps` 失败，服务项不会直接当成容器真实故障，而是记为 warning，并附带 `podman_error`。

## 7. 常用 systemd 命令

查看 timer：

```bash
systemctl status machine-health-check.timer
```

查看最近执行记录：

```bash
journalctl -u machine-health-check.service -n 100 --no-pager
```

手动触发一次：

```bash
sudo systemctl start machine-health-check.service
```

查看本次生成的最新报告：

```bash
sed -n '1,200p' reports/latest.json
```

## 8. 注意事项

- 当前依赖 `ping`、`ss`、`podman` 等系统命令；缺失时会退化为 warning 或 `unavailable`
- systemd 环境通常比交互式受限沙箱更接近真实线上运行权限
- `.env` 只用于注入环境变量，不会自动创建
