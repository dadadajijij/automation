# 服务器健康检查自动化服务方案

## 1. 目标

在当前服务器上建设一套轻量、可扩展、可定时执行的健康检查服务，用于日常巡检以下核心状态：

- CPU 使用率与负载
- 内存使用情况
- 磁盘容量与 inode 使用情况
- 网络连通性与基础吞吐状态
- 系统运行时信息
- 异常告警与巡检报告输出

这套服务优先满足以下要求：

- 单机可部署，依赖少
- 对业务侵入低
- 支持后续扩展更多巡检项
- 支持接入企业微信、钉钉、飞书或邮件告警
- 支持本地定时执行与留档

## 2. 适用场景

适合以下场景：

- 单台 Linux 服务器的日常健康巡检
- 小规模主机的统一巡检脚本模板
- 现有环境没有完整监控系统时的补充方案
- 作为 Prometheus / Zabbix / 云监控之前的过渡方案

不建议把它当作高频时序监控系统。它更适合“定时巡检 + 异常告警 + 巡检报告”。

## 3. 总体方案

建议采用分层设计：

1. 采集层
   负责从系统读取基础状态，比如 `/proc`、`/sys`、`os.statvfs`、`socket`、`ip`、`df` 等。

2. 规则层
   根据配置阈值判断状态是否正常，例如 CPU 超过 85%、内存超过 90%、根分区超过 80%。

3. 输出层
   输出 JSON 巡检报告、控制台结果、日志文件，并在异常时触发告警。

4. 调度层
   使用 `cron` 或 `systemd timer` 定时执行。

## 4. 建议巡检项

### 4.1 CPU

- 当前 CPU 总使用率
- 1 分钟 / 5 分钟 / 15 分钟负载
- CPU 核心数
- 是否存在负载持续高于核心数的情况

建议阈值：

- `warning`: CPU 使用率 >= 80%
- `critical`: CPU 使用率 >= 90%
- `warning`: 1 分钟负载 >= CPU 核数的 70%
- `critical`: 1 分钟负载 >= CPU 核数的 100%

### 4.2 内存

- 总内存
- 已使用内存
- 可用内存
- 内存使用率
- Swap 总量与使用率

建议阈值：

- `warning`: 内存使用率 >= 80%
- `critical`: 内存使用率 >= 90%
- `warning`: swap 使用率 >= 20%
- `critical`: swap 使用率 >= 50%

### 4.3 磁盘

- 各挂载点容量使用率
- inode 使用率
- 根目录 `/`
- 关键业务目录，例如 `/var`、`/data`、`/home`

建议阈值：

- `warning`: 磁盘使用率 >= 80%
- `critical`: 磁盘使用率 >= 90%
- `warning`: inode 使用率 >= 80%
- `critical`: inode 使用率 >= 90%

### 4.4 网络

- 默认网卡收发字节
- 默认网卡收发包数
- 网卡 error / drop 计数
- 默认网关连通性
- 外部目标连通性，例如 `8.8.8.8` 或公司内核心服务
- DNS 解析是否正常

建议把网络探测结果区分为三类：

- `ok`：探测成功
- `failed`：探测执行成功，但目标不可达
- `unavailable`：当前环境没有探测权限或缺少探测工具

这样可以避免在受限容器、低权限账户或禁用 ICMP 的环境中产生误报。

建议阈值：

- 网关不可达：`critical`
- 外部目标连续失败：`critical`
- 网卡错误计数突增：`warning` 或 `critical`

### 4.5 系统基础状态

- 主机名
- 内核版本
- 系统启动时长
- 当前时间与时区
- 登录用户数
- 关键进程存活状态

### 4.6 可选扩展项

- 关键端口监听状态
- 关键业务进程检查
- Docker / Podman 容器状态
- 磁盘读写延迟
- TCP 连接数、TIME_WAIT 数量
- NTP 时间同步状态
- 证书过期时间

## 5. 告警分级建议

建议定义 3 级状态：

- `ok`: 正常
- `warning`: 需要关注，但不一定立即处理
- `critical`: 需要立即处理

可采用“取最严重项作为本次巡检总状态”的策略。

示例：

- 有任一项为 `critical`，总状态为 `critical`
- 没有 `critical` 但有 `warning`，总状态为 `warning`
- 全部正常则为 `ok`

## 6. 执行方式建议

### 方案 A：Cron 定时执行

优点：

- 简单
- 部署快
- 适合单机场景

示例：

```cron
*/30 * * * * cd /home/devops/ka/automation/machine_health_check && PYTHONPATH=src /usr/bin/python3 -m health_check --config config/default.json >> logs/cron.log 2>&1
```

### 方案 B：systemd service + timer

优点：

- 更规范
- 易于托管
- 日志更好管理

建议正式环境优先使用 `systemd timer`，当前项目已按 30 分钟一次生成对应部署文件。

## 7. 输出与留档建议

每次巡检建议输出：

- 一份标准 JSON 结果
- 一条简洁的控制台摘要
- 异常时的告警消息

建议目录：

- `reports/latest.json`：最近一次巡检结果
- `reports/history/`：按时间归档
- `logs/`：运行日志

## 8. 告警渠道建议

第一阶段：

- 控制台输出
- 本地日志

第二阶段：

- Webhook 告警
  - 企业微信机器人
  - 钉钉机器人
  - 飞书机器人
- 邮件告警

当前项目已优先接入企业微信机器人 Webhook。
建议使用 `.env` 环境变量注入，不直接写入受版本控制的配置文件。
当前行为为每次巡检都发送一次 webhook 消息，并带上全部巡检项状态。

建议告警内容包含：

- 主机名
- 巡检时间
- 总状态
- 异常项摘要
- 关键指标值

## 9. 推荐项目结构

```text
machine_health_check/
├── config/
│   └── default.json
├── docs/
│   └── solution.md
├── scripts/
│   └── run_check.sh
├── src/
│   └── health_check/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── models.py
│       ├── runner.py
│       ├── evaluators.py
│       └── collectors/
│           ├── __init__.py
│           ├── cpu.py
│           ├── memory.py
│           ├── disk.py
│           ├── network.py
│           └── system.py
└── reports/
```

## 10. 技术选型建议

推荐第一版使用 Python 3 标准库优先实现：

- 优点：
  - 当前机器已有 Python 3.10
  - 开发快
  - 维护成本低
  - 跨 Linux 发行版适配较容易

可选依赖：

- `psutil`
  用于更稳定地获取 CPU、内存、磁盘、网络指标。

如果希望第一版尽量少依赖，建议先用标准库 + `/proc` 实现，后续再替换或增强为 `psutil` 版本。

## 11. 推荐实施路径

### 第一阶段：基础巡检

实现以下能力：

- CPU、内存、磁盘、网络、系统信息采集
- 阈值判断
- JSON 报告输出
- 本地命令行执行

### 第二阶段：定时任务 + 告警

增加：

- cron 或 systemd timer
- Webhook 告警
- 历史报告归档

### 第三阶段：扩展检查

增加：

- 关键进程检查
- 端口检查
- 容器检查
- 业务接口健康检查

## 12. 阈值配置建议

阈值建议外置到配置文件，不写死在代码中。这样不同服务器可以复用一套程序，只修改配置即可。

示例：

```json
{
  "thresholds": {
    "cpu_usage_percent": { "warning": 80, "critical": 90 },
    "memory_usage_percent": { "warning": 80, "critical": 90 },
    "swap_usage_percent": { "warning": 20, "critical": 50 },
    "disk_usage_percent": { "warning": 80, "critical": 90 },
    "inode_usage_percent": { "warning": 80, "critical": 90 }
  }
}
```

## 13. 风险与注意事项

- 仅做单次巡检时，CPU 瞬时值可能不够稳定
- 网络质量判断不能只靠一次 ping
- 有些云环境可能限制 ICMP，需要支持 TCP 探测作为补充
- 不同挂载点的重要性不同，建议支持单独阈值
- 如果服务器后续纳入统一监控平台，这套方案应转为补充巡检工具，而不是重复建设完整监控平台

## 14. 结论

这套方案适合你当前“服务器日常状态巡检”的目标，建议按以下顺序推进：

1. 先完成基础指标巡检脚本
2. 再接入定时任务
3. 最后补齐告警与扩展检查

当前目录已经适合直接开始第一阶段开发。
