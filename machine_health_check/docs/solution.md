# 服务器健康检查实现说明

本文描述 `machine_health_check` 当前已经落地的能力、配置结构和输出行为，以代码实现为准。

## 1. 目标与定位

这个子项目是一个轻量的单机巡检工具，适合：

- 定时巡检当前 Linux 主机
- 输出结构化 JSON 报告
- 对本机服务和 Podman 容器做基础健康检查
- 通过企业微信 webhook 发送文本告警

它不是时序监控系统，也不负责长期指标存储或高频采样。

## 2. 当前目录结构

```text
machine_health_check/
├── config/
│   └── default.json
├── deploy/
│   ├── machine-health-check.service
│   └── machine-health-check.timer
├── docs/
│   ├── deploy.md
│   └── solution.md
├── scripts/
│   ├── install_systemd.sh
│   └── run_check.sh
├── src/
│   └── health_check/
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── evaluators.py
│       ├── models.py
│       ├── notifiers.py
│       ├── runner.py
│       ├── service_checks.py
│       └── collectors/
│           ├── cpu.py
│           ├── disk.py
│           ├── memory.py
│           ├── network.py
│           ├── services.py
│           └── system.py
└── state/
    └── last_status.json
```

当前代码实际写报告的目录不是 `state/`，而是配置里的：

- `report_dir`
- `history_dir`

默认分别是：

- `reports`
- `reports/history`

## 3. 执行入口

命令行入口是：

```bash
PYTHONPATH=src python3 -m health_check --config config/default.json
```

也可以直接使用脚本：

```bash
bash scripts/run_check.sh
```

`run_check.sh` 会：

1. 切到 `machine_health_check/`
2. 如果存在 `.env`，先 `source` 进去
3. 执行 `python3 -m health_check --config config/default.json`

CLI 当前只支持两个参数：

- `--config`
  配置文件路径，默认 `config/default.json`
- `--no-write`
  只打印报告，不写 `reports/latest.json` 和历史归档

## 4. 当前巡检项

一次巡检固定生成 6 个检查项：

1. `system`
2. `cpu`
3. `memory`
4. `disk`
5. `network`
6. `services`

总体状态由 `worst_status()` 计算：

- 任一项 `critical`，总状态就是 `critical`
- 否则只要有 `warning`，总状态就是 `warning`
- 全部 `ok` 时，总状态是 `ok`

### 4.1 system

采集自 [`collectors/system.py`](../src/health_check/collectors/system.py)：

- 主机名
- 内核版本
- 平台字符串
- 当前 UTC 时间
- 启动时长
- CPU 核数

当前 `system` 项本身不做阈值判断，固定是：

- `status = "ok"`

### 4.2 cpu

采集自 [`collectors/cpu.py`](../src/health_check/collectors/cpu.py)：

- `/proc/stat` 两次采样间隔 0.2 秒，计算瞬时 CPU 使用率
- `os.getloadavg()` 的 1 / 5 / 15 分钟负载
- CPU 核数
- `load_ratio_1m = loadavg[0] / cpu_count`

阈值来自配置：

- `thresholds.cpu_usage_percent`
- `thresholds.load_ratio`

CPU 最终状态取这两个判断结果的最严重值。

### 4.3 memory

采集自 [`collectors/memory.py`](../src/health_check/collectors/memory.py)：

- `/proc/meminfo`
- 总内存、已用、可用
- swap 总量、已用
- 内存使用率
- swap 使用率

阈值来自配置：

- `thresholds.memory_usage_percent`
- `thresholds.swap_usage_percent`

### 4.4 disk

采集自 [`collectors/disk.py`](../src/health_check/collectors/disk.py)：

- 对配置里的每个挂载点执行 `os.statvfs()`
- 磁盘总量、已用、可用
- inode 总量、已用
- 容量使用率
- inode 使用率

阈值来自配置：

- `thresholds.disk_usage_percent`
- `thresholds.inode_usage_percent`

默认只检查：

- `/`

### 4.5 network

采集自 [`collectors/network.py`](../src/health_check/collectors/network.py)：

- `/proc/net/dev` 网卡统计
- `ping` 检查
- TCP 出站连通性检查

当前网络探测状态分三类：

- `ok`
- `failed`
- `unavailable`

其中：

- `ping` 命令不存在时会标记为 `unavailable`
- TCP 权限不足时会标记为 `unavailable`
- 探测失败时会标记为 `failed`

网络项的汇总规则是：

- 任一 ping 或 TCP 检查 `failed` -> `critical`
- 任一 ping 或 TCP 检查 `unavailable` -> `warning`
- 否则 `ok`

默认配置会检查：

- ping：`127.0.0.1`、`8.8.8.8`
- TCP：`1.1.1.1:53`

### 4.6 services

服务检查实现位于 [`service_checks.py`](../src/health_check/service_checks.py)。

当前支持两类目标：

- 宿主机服务
- Podman 容器服务

#### 宿主机服务

默认配置里当前只有：

- `ka_tools`

宿主机服务会做：

- `ss -ltn` 端口监听检查
- TCP 连接检查
- 如果协议是 `http`，再做 HTTP 健康检查

#### 容器服务

容器服务从根仓库的 [`project-ports.json`](../../project-ports.json) 自动发现。

默认排除：

- `ka-tools`

每个容器服务会做：

- `podman ps --format json` 识别容器
- TCP 连接检查
- 协议是 `http` 或 `auto` 时的 HTTP 健康检查

容器匹配优先级：

1. `container_name`
2. 规范化后的名称
3. 宿主机映射端口

默认 HTTP 检查路径会合并三类来源：

1. 每个服务自己的 override
2. `base_path_templates`
3. 通用默认路径

当前默认 `base_path_templates` 包含：

- `/tools2/{service_name}`
- `/tools2/{service_name}/`

这意味着容器服务检查已经显式考虑了根仓库部署流水线的子路径发布方式。

如果当前环境无法访问 Podman，服务检查不会直接当成容器真实故障，而是表现为：

- `warning`
- `podman_error`

## 5. 配置结构

默认配置文件是 [`config/default.json`](../config/default.json)。

当前主要字段：

```json
{
  "report_dir": "reports",
  "history_dir": "reports/history",
  "network": {
    "ping_targets": ["127.0.0.1", "8.8.8.8"],
    "tcp_targets": [
      { "host": "1.1.1.1", "port": 53, "timeout_seconds": 2 }
    ]
  },
  "mount_points": ["/"],
  "service_checks": { "...": "..." },
  "notifications": {
    "enabled": true,
    "wecom_webhook": ""
  },
  "thresholds": { "...": "..." }
}
```

### 5.1 notifications

当前只接了企业微信 webhook。

读取顺序：

1. 先读配置文件中的 `notifications.wecom_webhook`
2. 如果为空，再读环境变量 `MACHINE_HEALTH_CHECK_WECOM_WEBHOOK`

因此推荐把 webhook 放在 `.env` 中，而不是写进版本控制里的 JSON。

### 5.2 service_checks

关键字段：

- `enabled`
- `project_ports_file`
- `host_services`
- `container_services.exclude_names`
- `container_services.default_host`
- `container_services.default_timeout_seconds`
- `container_services.default_protocol`
- `container_services.default_health_paths`
- `container_services.base_path_templates`
- `container_services.default_success_status_codes`
- `container_services.per_service_overrides`

默认配置里已经针对这些服务写了 override：

- `audioqas`
- `agora-token-generator`
- `api-examples-web`
- `agora-rest-api-debugger`
- `loga`
- `mos-video-compare`
- `decrypt-online`

其中 `decrypt-online` 默认按纯 TCP 服务检查，不做 HTTP 探测。

## 6. 输出格式

主流程始终会先把完整报告打印到 stdout，结构来自 [`CheckReport.to_dict()`](../src/health_check/models.py)：

```json
{
  "host": "hostname",
  "generated_at": "2026-06-23T00:00:00+00:00",
  "overall_status": "ok",
  "items": [
    {
      "name": "cpu",
      "status": "ok",
      "summary": "...",
      "details": {}
    }
  ]
}
```

如果没有带 `--no-write`，还会写：

- `reports/latest.json`
- `reports/history/report-<UTC 时间戳>.json`

当前不会写额外日志文件，systemd 场景下主要依赖 `journalctl` 看历史执行。

## 7. 企业微信通知

如果：

- `notifications.enabled = true`
- 且最终拿到了非空 `wecom_webhook`

则会发送一条文本消息。消息内容包含：

- 主机名
- 巡检时间
- 总状态
- 每一个巡检项的状态和摘要
- 所有非 `ok` 的服务项明细

Webhook 发送结果会额外打印一条 JSON 到 stdout：

```json
{
  "notification_sent": true,
  "notification_message": "..."
}
```

## 8. systemd 部署

当前仓库已经提供：

- [`deploy/machine-health-check.service`](../deploy/machine-health-check.service)
- [`deploy/machine-health-check.timer`](../deploy/machine-health-check.timer)

行为如下：

- service 以 `devops` 用户执行
- `WorkingDirectory` 固定为当前项目目录
- 会自动读取 `.env`
- timer 每 30 分钟触发一次
- `Persistent=true`

安装脚本是：

```bash
bash scripts/install_systemd.sh
```

它会：

1. 拷贝 service / timer 到 `/etc/systemd/system/`
2. `daemon-reload`
3. `enable --now` timer
4. 立即执行一次 service
5. 输出 timer 状态

## 9. 依赖与限制

- 依赖 Python 3 标准库，无第三方 Python 包
- 依赖 Linux `/proc`
- `ping` 缺失时，ICMP 检查会退化为 `unavailable`
- `podman ps` 不可用时，容器识别会退化为 warning
- 网络与服务检查都是单次探测，不具备连续观测或抖动平滑能力
- `state/last_status.json` 当前没有接进主流程
