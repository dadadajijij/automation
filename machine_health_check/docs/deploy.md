# 部署说明

## 1. 本地执行

```bash
bash scripts/run_check.sh
```

## 2. 安装 systemd 定时任务

当前已经提供 30 分钟执行一次的 `systemd timer`：

- [service](/home/devops/ka/automation/machine_health_check/deploy/machine-health-check.service)
- [timer](/home/devops/ka/automation/machine_health_check/deploy/machine-health-check.timer)

安装命令：

```bash
bash scripts/install_systemd.sh
```

执行后会：

- 安装 `machine-health-check.service`
- 安装 `machine-health-check.timer`
- 启用 timer
- 立即触发一次巡检

## 3. 企业微信告警

企业微信 webhook 建议放在项目根目录 `.env` 中，程序和 systemd 都会自动读取。

示例文件可参考 [`.env.example`](/home/devops/ka/automation/machine_health_check/.env.example)。

告警策略：

- 每次巡检都会发送一次企业微信消息
- 消息中会包含总状态以及每一个巡检项的状态和摘要

## 4. 常用 systemd 命令

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

## 5. 安全建议

当前 webhook 直接写在配置文件中，适合先跑通。
当前项目已经改成 `.env` 注入，不会默认写入受版本控制的 JSON 配置。
