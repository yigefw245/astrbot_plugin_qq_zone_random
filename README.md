# astrbot_plugin_qq_zone_random

QQ 空间动态发送插件 — HTTP 直连 QQ 空间 Web API，支持 Cookie 绑定、手动发布、AI 生成说说、定时随机发送。架构参考 [astrbot_plugin_qzone_ultra](https://github.com/diaomin66/astrbot_plugin_qzone_ultra)。

## 功能

- 🔐 **Cookie 绑定** — 自动从 OneBot 获取或手动粘贴 Cookie
- 📝 **发布说说** — 支持文字 + 图片，HTTP 直连 taotao.qzone.qq.com
- 🧠 **AI 生成** — 基于最近聊天记录 + 当前时间，用 LLM 生成日常说说
- 🕐 **定时随机发送** — 设定时间范围和每天发送条数，自动随机时间发布
- ⏱️ **智能调度** — 最短间隔 30 分钟，窗口不足自动跳过
- 📊 **状态查询** — 查看 Cookie/g_tk/下次发送时间

## 安装

1. 将插件放入 `data/plugins/astrbot_plugin_qq_zone_random/`
2. 安装依赖: `pip install aiohttp`
3. 重启 AstrBot 或重新加载插件

## 命令

| 命令 | 说明 |
|------|------|
| `/qzone status` | 查看 Cookie / g_tk / OneBot 状态 |
| `/qzone autobind` | 自动从 OneBot 获取 Cookie |
| `/qzone bind <cookie>` | 手动绑定 Cookie |
| `/qzone unbind` | 解绑 Cookie |
| `/qzone post <内容>` | 发布说说（支持图片） |
| `/qzone generate` | AI 生成说说预览（从聊天记录+当前时间） |
| `/qzone autopost` | AI 生成并直接发布 |
| `/qzone schedule` | 查看/启动/停止定时发送 |
| `/qzone next` | 查看下次发送时间 |
| `/qzone help` | 帮助 |

## 快速开始

```
/qzone autobind              # 自动绑定 Cookie
/qzone status                # 确认 p_skey 正常
/qzone post 你好世界         # 发第一条说说
/qzone generate              # AI 生成一条试试
/qzone schedule start        # 启动定时发送
```

## 配置

在 AstrBot 管理面板配置（`_conf_schema.json`）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `admin_uins` | 空 | 管理员 QQ 号，逗号分隔 |
| `provider_id` | 空 | AI 生成的 LLM 提供商 ID |
| `schedule.enabled` | false | 启用定时发送 |
| `schedule.start_hour` | 9 | 每天开始时间（小时） |
| `schedule.end_hour` | 22 | 每天结束时间（小时） |
| `schedule.min_posts` | 0 | 每天最少发送条数 |
| `schedule.max_posts` | 3 | 每天最多发送条数 |

## 定时发送逻辑

- 每天在 `start_hour` ~ `end_hour` 内随机选取 `min_posts` ~ `max_posts` 个时间点
- 每条至少间隔 30 分钟，时间窗口不够则自动减少或跳过
- 到时间自动从聊天记录生成说说并发布
- 说说内容结合当前时间（早/午/晚）和聊天上下文

## 依赖

- **AstrBot** >= v4.0.0
- **aiohttp** (pip install aiohttp)
- **OneBot V11 协议端**（NapCat / LLOneBot 等）

## 排障

| 现象 | 解决 |
|------|------|
| Cookie 绑定失败 | 确认使用 NapCat 或 LLOneBot，执行 `/qzone autobind` |
| 发布失败 | Cookie 可能过期，`/qzone unbind` → `/qzone autobind` |
| g_tk 显示 NO p_skey | Cookie 缺少 p_skey 字段，重新自动绑定 |
| AI 生成空 | 聊天记录不足，先聊几句再试 |
| 定时不工作 | `/qzone schedule start` 启动，确认 Cookie 已绑定 |

## 致谢

参考 [diaomin66/astrbot_plugin_qzone_ultra](https://github.com/diaomin66/astrbot_plugin_qzone_ultra) 的架构设计。


## 支持

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
