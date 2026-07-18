# astrbot_plugin_qq_zone_random

QQ 空间动态发送插件 — 通过 OneBot 协议管理 QQ 空间说说。架构参考 [astrbot_plugin_qzone_ultra](https://github.com/diaomin66/astrbot_plugin_qzone_ultra)。

## 功能

- 🔐 **Cookie 自动绑定** — 首次使用时自动从 OneBot 获取 Cookie
- 📝 **发布说说** — 支持文字 + 图片
- 📋 **查看动态** — 查看好友最新动态列表
- 📊 **状态检测** — 查看插件和 QQ 空间连接状态
- 🔌 **自动探测 API** — 自动适配 NapCat/LLOneBot 等不同实现

## 命令

| 命令 | 权限 | 说明 |
|------|------|------|
| `/qzone status` | 所有人 | 查看插件和连接状态 |
| `/qzone autobind` | 管理员 | 自动绑定 Cookie |
| `/qzone bind <cookie>` | 管理员 | 手动绑定 Cookie |
| `/qzone unbind` | 管理员 | 解绑 Cookie |
| `/qzone post <内容>` | 管理员 | 发布说说（支持图片） |
| `/qzone feed [数量]` | 管理员 | 查看好友动态 |
| `/qzone help` | 所有人 | 显示帮助 |

## 示例

```
/qzone status                          # 查看状态
/qzone autobind                        # 自动绑定
/qzone post 今天天气真好！              # 发文字说说
/qzone post 美图分享 [附带图片]         # 发图文说说
/qzone feed 10                         # 看最近 10 条动态
```

## 依赖

- **AstrBot** >= v4.0.0
- **OneBot V11 协议端**（NapCat / LLOneBot / Shamrock 等）

## 配置

在 AstrBot 插件配置面板中可设置：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `admin_uins` | 空 | 管理员 QQ 号，逗号分隔；为空时允许所有人 |

## 架构

```
main.py
├── QQZoneRandomPlugin(Star)
│   ├── 生命周期: initialize() / terminate()
│   ├── OneBot 客户端: _capture_onebot_client_from_context()
│   ├── Cookie 管理: _auto_bind_cookie() / bind / unbind
│   ├── 命令组 /qzone:
│   │   ├── status   → 状态检测
│   │   ├── autobind → 自动绑定
│   │   ├── bind     → 手动绑定
│   │   ├── unbind   → 解绑
│   │   ├── post     → 发布说说
│   │   ├── feed     → 查看动态
│   │   └── help     → 帮助
│   └── 自动触发: 首次消息时自动捕获客户端并绑定
```

## 致谢

参考 [diaomin66/astrbot_plugin_qzone_ultra](https://github.com/diaomin66/astrbot_plugin_qzone_ultra) 的架构设计。
3. 协议端版本过旧，请更新到最新版本

## 支持

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
