# -*- coding: utf-8 -*-
"""
QQ空间动态发送插件
参考 astrbot_plugin_qzone_ultra 架构，通过 HTTP 直连 QQ 空间 Web API 管理说说。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Image

_QZONE_PUBLISH_URL = (
    "https://user.qzone.qq.com/proxy/domain/"
    "taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
)
_QZONE_UPLOAD_PIC_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_pic_v2"
_COOKIE_DOMAIN = "user.qzone.qq.com"
_COOKIE_API_CANDIDATES = ["get_cookies", "get_credentials", "get_login_info"]
_ONEBOT_PLATFORM_MARKERS = (
    "aiocqhttp", "onebot", "cqhttp", "napcat",
    "llbot", "llonebot", "lagrange", "shamrock",
)
_PLUGIN_DATA_FILE = "plugin_state.json"


def _compute_gtk(p_skey: str) -> int:
    if not p_skey:
        return 0
    h = 5381
    for c in p_skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


def _cookie_str(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _detect_onebot_platform(obj: Any) -> bool:
    for attr in ("name", "type", "platform_type", "platform_name"):
        val = str(getattr(obj, attr, "")).lower()
        if any(marker in val for marker in _ONEBOT_PLATFORM_MARKERS):
            return True
    meta = getattr(obj, "meta", None)
    if callable(meta):
        with contextlib.suppress(Exception):
            meta = meta()
    if meta is not None:
        return _detect_onebot_platform(meta)
    return False


@register("astrbot_plugin_qq_zone_random", "AstrBot Community",
          "QQ空间动态发送插件，HTTP直连QQ空间Web API。", "1.0.0")
class QQZoneRandomPlugin(Star):
    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self._context = context
        self.config: dict[str, Any] = config if isinstance(config, dict) else {}

        admin_uins = self.config.get("admin_uins", [])
        if isinstance(admin_uins, str):
            admin_uins = [int(x.strip()) for x in admin_uins.split(",") if x.strip().isdigit()]
        self.admin_uins: list[int] = admin_uins if isinstance(admin_uins, list) else []

        self.root = Path(__file__).resolve().parent
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._onebot_client: Any = None
        self._cookies: dict[str, str] = {}
        self._cookie_bound: bool = False
        self._login_uin: int = 0
        self._http: aiohttp.ClientSession | None = None

        self._schedule_task: asyncio.Task | None = None
        self._posted_today: int = 0
        self._last_post_date: str = ""
        self._next_times: list[float] = []
        self._load_schedule_config()

        self._load_state()
        logger.info("QQ空间插件已加载 admin_uins=%s schedule=%s",
                     self.admin_uins, self._schedule_enabled)

    async def initialize(self):
        self._capture_onebot_client_from_context()
        self._http = aiohttp.ClientSession()
        if self._schedule_enabled and self._cookie_bound:
            self._start_scheduler()

    async def terminate(self):
        self._save_state()
        if self._schedule_task:
            self._schedule_task.cancel()
        if self._http:
            await self._http.close()
            self._http = None

    def _state_path(self) -> Path:
        return self.data_dir / _PLUGIN_DATA_FILE

    def _load_state(self) -> None:
        path = self._state_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._cookies = data.get("cookies", {})
            self._cookie_bound = bool(data.get("cookie_bound") and self._cookies)
            self._login_uin = data.get("login_uin", 0)
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            self._state_path().write_text(json.dumps({
                "cookies": self._cookies,
                "cookie_bound": self._cookie_bound,
                "login_uin": self._login_uin,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _capture_onebot_client_from_context(self) -> None:
        if self._onebot_client is not None:
            return
        context = getattr(self, "_context", None) or getattr(self, "context", None)
        if context is None:
            return
        for attr in ("platform_manager", "platform_mgr", "_platform_manager"):
            mgr = getattr(context, attr, None)
            if mgr is None:
                continue
            for inst_attr in ("platform_insts", "platforms", "adapters", "instances"):
                instances: Any = getattr(mgr, inst_attr, None)
                if instances is None:
                    continue
                if callable(instances):
                    instances = instances()
                if isinstance(instances, dict):
                    instances = list(instances.values())
                if hasattr(instances, "__iter__") and not isinstance(instances, (str, bytes)):
                    for inst in instances:
                        if _detect_onebot_platform(inst):
                            self._onebot_client = inst
                            return
        for attr in ("platform", "adapter", "bot"):
            inst = getattr(context, attr, None)
            if inst is not None and _detect_onebot_platform(inst):
                self._onebot_client = inst
                return

    def _sender_id(self, event: AstrMessageEvent) -> int:
        try:
            if hasattr(event, "get_sender_id"):
                val = event.get_sender_id()
                if val is not None:
                    return int(val)
        except Exception:
            pass
        msg_obj = getattr(event, "message_obj", None)
        sender = getattr(msg_obj, "sender", None)
        return int(getattr(sender, "user_id", 0) or 0)

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            if hasattr(event, "is_admin") and event.is_admin():
                return True
        except Exception:
            pass
        if not self.admin_uins:
            return True
        return self._sender_id(event) in set(self.admin_uins)

    def _stop_event(self, event: AstrMessageEvent) -> None:
        with contextlib.suppress(Exception):
            event.stop_event()

    def _command_result(self, event: AstrMessageEvent, text: str) -> MessageEventResult:
        self._stop_event(event)
        return event.plain_result(text)

    def _get_call_action(self, event: AstrMessageEvent) -> Any:
        self._capture_onebot_client_from_context()
        if self._onebot_client is not None:
            ca = getattr(self._onebot_client, "call_action", None)
            if callable(ca):
                return ca
        bot = getattr(event, "bot", None)
        if bot is not None:
            ca = getattr(bot, "call_action", None)
            if callable(ca):
                return ca
        return None

    @staticmethod
    def _get_images_from_event(event: AstrMessageEvent) -> list[str]:
        images: list[str] = []
        for msg in event.get_messages():
            if isinstance(msg, Image):
                url = getattr(msg, "url", "")
                file = getattr(msg, "file", "")
                if url:
                    images.append(str(url))
                elif file:
                    images.append(str(file))
        return images

    @staticmethod
    def _normalize_cookies(raw_cookies: Any) -> dict[str, str]:
        if isinstance(raw_cookies, list):
            result: dict[str, str] = {}
            for item in raw_cookies:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    value = item.get("value", "")
                    if name:
                        result[str(name)] = str(value)
            return result
        if isinstance(raw_cookies, dict):
            if "name" in raw_cookies and "value" in raw_cookies:
                return {str(raw_cookies["name"]): str(raw_cookies["value"])}
            for wrapper in ("data", "cookies"):
                inner = raw_cookies.get(wrapper)
                if inner and inner != raw_cookies:
                    return QQZoneRandomPlugin._normalize_cookies(inner)
            return {str(k): str(v) for k, v in raw_cookies.items()}
        if isinstance(raw_cookies, str):
            return QQZoneRandomPlugin._parse_cookie_string(raw_cookies)
        return {}

    @staticmethod
    def _parse_cookie_string(text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in text.replace(";", " ").split():
            if "=" in item:
                k, v = item.split("=", 1)
                result[k.strip()] = v.strip()
        return result

    async def _fetch_cookies_from_onebot(self, call_action: Any) -> dict[str, str] | None:
        for api_name in _COOKIE_API_CANDIDATES:
            for params in ({}, {"domain": _COOKIE_DOMAIN}):
                try:
                    result = await call_action(api_name, **params)
                except Exception:
                    continue
                if isinstance(result, dict):
                    for key in ("cookies", "data", "result"):
                        raw = result.get(key)
                        if raw:
                            cookies = self._normalize_cookies(raw)
                            if cookies:
                                return cookies
                    cookies = self._normalize_cookies(result)
                    if cookies:
                        return cookies
        return None

    async def _auto_bind_cookie(self, call_action: Any) -> bool:
        if self._cookie_bound:
            return True
        cookies = await self._fetch_cookies_from_onebot(call_action)
        if not cookies:
            return False
        self._cookies = cookies
        self._cookie_bound = True
        uin_str = cookies.get("uin", cookies.get("p_uin", "")).replace("o", "").replace("O", "")
        try:
            self._login_uin = int(uin_str)
        except (ValueError, TypeError):
            self._login_uin = 0
        self._save_state()
        logger.info("QQ空间插件: Cookie绑定成功, 账号=%s", self._login_uin or "?")
        return True

    async def _publish_to_qzone(self, content: str, images: list[str] | None = None) -> dict[str, Any]:
        if not self._http:
            raise RuntimeError("HTTP session not initialized")
        if not self._cookies:
            raise RuntimeError("Cookie not bound")

        p_skey = self._cookies.get("p_skey", "")
        g_tk = _compute_gtk(p_skey)
        cookie_header = _cookie_str(self._cookies)

        headers = {
            "Cookie": cookie_header,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            "Referer": f"https://user.qzone.qq.com/{self._login_uin}",
            "Origin": "https://user.qzone.qq.com",
        }

        data: dict[str, Any] = {
            "syn_tweet_verson": "1", "paramstr": "1", "who": "1",
            "con": content, "feedversion": "1", "ver": "1",
            "ugc_right": 1, "to_sign": 0, "hostuin": self._login_uin,
            "code_version": "1", "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{self._login_uin}",
        }

        if images and self._http:
            uploaded = await self._upload_pics(images, g_tk, cookie_header)
            if uploaded:
                data["richtype"] = "1"
                data["subrichtype"] = "1"
                data["richval"] = "\t".join(p["richval"] for p in uploaded)
                data["pic_bo"] = ",".join(p["pic_bo"] for p in uploaded if p.get("pic_bo"))

        url = f"{_QZONE_PUBLISH_URL}?g_tk={g_tk}"
        async with self._http.post(url, data=data, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
            text = await resp.text()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text, "status": resp.status}

    async def _upload_pics(self, image_urls: list[str], g_tk: int, cookie_header: str) -> list[dict[str, str]]:
        if not self._http:
            return []
        results: list[dict[str, str]] = []
        for img_url in image_urls[:9]:
            try:
                pic_data = await self._fetch_image_bytes(img_url)
                if not pic_data:
                    continue
                form = aiohttp.FormData()
                form.add_field("qzreferrer", f"https://user.qzone.qq.com/{self._login_uin}")
                form.add_field("up_goalfile", "0")
                form.add_field("charset", "utf-8")
                form.add_field("output_charset", "utf-8")
                form.add_field("output_type", "json")
                form.add_field("uin", str(self._login_uin))
                form.add_field("picture", pic_data, filename="upload.jpg", content_type="image/jpeg")
                upload_url = f"{_QZONE_UPLOAD_PIC_URL}?g_tk={g_tk}"
                async with self._http.post(upload_url, data=form, headers={
                    "Cookie": cookie_header,
                    "User-Agent": "Mozilla/5.0 ... Chrome/122",
                    "Origin": "https://user.qzone.qq.com",
                    "Referer": f"https://user.qzone.qq.com/{self._login_uin}",
                }, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    result = await resp.json()
                    if result.get("ret") == 0 or result.get("code") == 0:
                        pics = result.get("data", {}).get("pic", result.get("data", {}))
                        if isinstance(pics, list):
                            for pic in pics:
                                if isinstance(pic, dict):
                                    results.append({"richval": pic.get("richval", ""), "pic_bo": pic.get("pic_bo", "")})
                        elif isinstance(pics, dict):
                            results.append({"richval": pics.get("richval", ""), "pic_bo": pics.get("pic_bo", "")})
            except Exception:
                pass
        return results

    async def _fetch_image_bytes(self, url: str) -> bytes | None:
        if not self._http:
            return None
        try:
            if url.startswith(("http://", "https://")):
                async with self._http.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        return await resp.read()
            elif url.startswith("base64://"):
                import base64
                return base64.b64decode(url[9:])
            elif url.startswith("file://"):
                path = url[7:] if not url.startswith("file:///") else url[8:]
                return Path(path).read_bytes()
            else:
                p = Path(url)
                if p.is_file():
                    return p.read_bytes()
        except Exception:
            pass
        return None

    def _format_next_times(self) -> str:
        """Format next scheduled post times for display."""
        if not self._schedule_enabled:
            return "Next: scheduler disabled"
        if not self._next_times:
            # estimate: if scheduler is running, compute what it would pick
            remaining = max(0, self._schedule_max - self._posted_today)
            if remaining <= 0:
                tomorrow = datetime.now().date() + timedelta(days=1)
                return f"Next: tomorrow at {self._schedule_start}:00+ (daily limit reached)"
            count = min(remaining, self._schedule_max)
            estimated = self._random_times_today(max(count, self._schedule_min))
            if estimated:
                self._next_times = estimated
            else:
                return "Next: out of today's time window"

        now = datetime.now().timestamp()
        future = [t for t in self._next_times if t > now]
        if not future:
            tomorrow = datetime.now().date() + timedelta(days=1)
            return f"Next: tomorrow at {self._schedule_start}:00+"
        times_str = ", ".join(
            datetime.fromtimestamp(t).strftime("%H:%M") for t in future[:10]
        )
        countdown = int(future[0] - now)
        h, m = divmod(countdown, 3600)
        m //= 60
        return f"Next ({len(future)} posts, min 30min gap): {times_str}\nFirst in: {h}h {m}m"

    def _load_schedule_config(self) -> None:
        """Load schedule config from AstrBotConfig (auto-updated from _conf_schema)."""
        schedule_cfg = self.config.get("schedule", {})
        if isinstance(schedule_cfg, dict):
            self._schedule_enabled = bool(schedule_cfg.get("enabled", False))
            self._schedule_start = int(schedule_cfg.get("start_hour", 9))
            self._schedule_end = int(schedule_cfg.get("end_hour", 22))
            self._schedule_min = max(0, int(schedule_cfg.get("min_posts", 0)))
            self._schedule_max = max(self._schedule_min, int(schedule_cfg.get("max_posts", 3)))
        else:
            self._schedule_enabled = False
            self._schedule_start = 9
            self._schedule_end = 22
            self._schedule_min = 0
            self._schedule_max = 3

    # -- scheduler --

    def _start_scheduler(self) -> None:
        if self._schedule_task and not self._schedule_task.done():
            return
        if not self._schedule_enabled or self._schedule_max <= 0:
            return
        self._schedule_task = asyncio.create_task(self._schedule_loop())
        logger.info("QQ空间: scheduler started %d-%dh, %d-%d posts/day",
                     self._schedule_start, self._schedule_end,
                     self._schedule_min, self._schedule_max)

    def _random_times_today(self, count: int) -> list[float]:
        """Generate `count` random timestamps within schedule range, each >=30min apart.
        If window is too small, reduces count or returns empty list."""
        _MIN_GAP = 1800  # 30 minutes
        now = datetime.now()
        end_dt = now.replace(hour=self._schedule_end, minute=0, second=0)
        now_s = now.timestamp()
        end_s = end_dt.timestamp()
        window = max(end_s - now_s - 60, 0)
        if window <= 0:
            return []
        # check if we can fit the requested count with minimum gap
        needed = (count - 1) * _MIN_GAP + 60
        if window < needed:
            count = max(1, int(window // _MIN_GAP) + 1)
            if count <= 0:
                return []
        times: list[float] = []
        for i in range(count):
            min_t = now_s + 60 + i * _MIN_GAP
            max_t = end_s - (count - 1 - i) * _MIN_GAP
            if max_t <= min_t:
                break
            t = random.uniform(min_t, max_t)
            times.append(t)
        times.sort()
        return times

    async def _schedule_loop(self) -> None:
        """Main scheduler loop. Runs once per day, picks random times, then sleeps & posts."""
        while True:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self._last_post_date:
                self._posted_today = 0
                self._last_post_date = today

            remaining = max(0, self._schedule_max - self._posted_today)
            if remaining <= 0:
                # wait until next day
                tomorrow = datetime.now().date() + timedelta(days=1)
                next_start = datetime.combine(tomorrow, datetime.min.time()).replace(hour=self._schedule_start)
                delay = (next_start - datetime.now()).total_seconds()
                if delay > 0:
                    logger.info("QQ空间: scheduler sleeping %.0fs until next day", delay)
                    await asyncio.sleep(delay)
                continue

            count = random.randint(self._schedule_min, min(remaining, self._schedule_max))
            if count <= 0:
                await asyncio.sleep(600)  # 10 min retry
                continue

            times = self._random_times_today(count)
            self._next_times = times
            if not times:
                logger.info("QQ空间: scheduler skipped, time window too small for %d posts (min 30min gap)", count)
                # wait for next day
                tomorrow = datetime.now().date() + timedelta(days=1)
                next_start = datetime.combine(tomorrow, datetime.min.time()).replace(hour=self._schedule_start)
                delay = (next_start - datetime.now()).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            logger.info("QQ空间: scheduler will post %d times today at %s",
                         len(times), [datetime.fromtimestamp(t).strftime("%H:%M") for t in times])

            for target_ts in times:
                delay = target_ts - datetime.now().timestamp()
                if delay > 0:
                    await asyncio.sleep(delay)
                if not self._cookie_bound:
                    logger.warning("QQ空间: scheduler skipped, cookie not bound")
                    continue
                try:
                    await self._scheduled_post()
                except Exception as exc:
                    logger.warning("QQ空间: scheduled post failed: %s", exc)

            # wait for next day
            tomorrow = datetime.now().date() + timedelta(days=1)
            next_start = datetime.combine(tomorrow, datetime.min.time()).replace(hour=self._schedule_start)
            delay = (next_start - datetime.now()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)

    async def _scheduled_post(self) -> None:
        """Generate and publish one scheduled post."""
        # Get chat history from first available source
        history = ""
        try:
            context: Any = getattr(self, "_context", None)
            if context:
                conv_mgr: Any = getattr(context, "conversation_manager", None)
                if conv_mgr:
                    # get first conversation
                    convs = await conv_mgr.get_conversations()
                    if convs:
                        conv = convs[0]
                        history_raw: Any = getattr(conv, "history", None)
                        if history_raw and isinstance(history_raw, str):
                            messages: Any = json.loads(history_raw)
                            if isinstance(messages, list):
                                lines: list[str] = []
                                for msg in messages[-50:]:
                                    if isinstance(msg, dict):
                                        role = msg.get("role", "")
                                        content = str(msg.get("content", ""))[:200]
                                        d = "user" if role == "user" else ("AI" if role == "assistant" else role)
                                        lines.append(f"[{d}]: {content}")
                                history = "\n".join(lines)
        except Exception:
            pass

        if not history:
            logger.warning("QQ空间: scheduler no history, using fallback")
            text = "今天也是元气满满的一天！"
        else:
            prompt = (
                f"现在是 {datetime.now().strftime('%Y-%m-%d %H:%M')}。\n"
                "你是一个有自己性格的QQ空间用户。以下是最近聊天记录。\n"
                "根据当前时间和聊天内容，以你自己的口吻和性格写一段日常说说。\n"
                "内容要符合时间段语境（如: 早晨/午间/傍晚/深夜），简短有个性，只输出正文。\n\n"
                f"聊天记录:\n{history[:4000]}"
            )
            # reuse event-less LLM call
            event = self._scheduled_event()
            text = await self._llm_generate(event, prompt) or "今天也是元气满满的一天！"

        if not text.strip():
            text = "今天也是元气满满的一天！"

        logger.info("QQ空间: scheduled post text=%s...", text[:50])
        result = await self._publish_to_qzone(text)
        if isinstance(result, dict) and result.get("code", result.get("ret", -1)) == 0:
            self._posted_today += 1
            logger.info("QQ空间: scheduled post #%d published", self._posted_today)
        else:
            msg = result.get("message", result.get("msg", "")) if isinstance(result, dict) else str(result)
            logger.warning("QQ空间: scheduled post failed: %s", msg)

    def _scheduled_event(self) -> Any:
        """Create a minimal fake event for LLM calls without a real event."""
        class _FakeEvent:
            def __init__(self):
                self.message_str = ""
                self.unified_msg_origin = ""
            def get_sender_id(self) -> str: return "0"
            def get_sender_name(self) -> str: return "scheduler"
        return _FakeEvent()

    # -- commands --

    @filter.command_group("qzone")
    def qzone(self):
        pass

    @qzone.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        call_action = self._get_call_action(event)
        lines = ["QQ空间插件状态"]
        lines.append(f"Cookie: {'OK' if self._cookie_bound else 'NOT BOUND'}")
        if self._login_uin:
            lines.append(f"Account: {self._login_uin}")
        g_tk = _compute_gtk(self._cookies.get("p_skey", ""))
        lines.append(f"g_tk: {'OK' if g_tk else 'NO p_skey'}")
        lines.append(f"OneBot: {'OK' if call_action else 'NOT CONNECTED'}")
        if self._cookies:
            lines.append(f"Cookie keys: {', '.join(self._cookies.keys())}")
            p_skey = self._cookies.get("p_skey", "")
            lines.append(f"p_skey: {'OK ' + p_skey[:8] + '...' if p_skey else 'MISSING'}")
        yield self._command_result(event, "\n".join(lines))

    @qzone.command("autobind")
    async def cmd_autobind(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield self._command_result(event, "Admin only.")
            return
        call_action = self._get_call_action(event)
        if call_action is None:
            yield self._command_result(event, "OneBot not connected.")
            return
        ok = await self._auto_bind_cookie(call_action)
        if ok:
            yield self._command_result(event, f"Cookie bound! Account: {self._login_uin}")
        else:
            yield self._command_result(event,
                "Auto-bind failed. Ensure OneBot supports get_cookies.\n"
                "Or manual: /qzone bind p_skey=xxx; uin=xxx"
            )

    @qzone.command("bind")
    async def cmd_bind(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield self._command_result(event, "Admin only.")
            return
        message_str = event.message_str.strip()
        parts = message_str.split(None, 2)
        if len(parts) < 3:
            yield self._command_result(event, "Usage: /qzone bind p_skey=xxx; uin=xxx")
            return
        cookie_str = parts[-1].strip()
        cookies = self._parse_cookie_string(cookie_str)
        if not cookies:
            yield self._command_result(event, "Invalid cookie format")
            return
        self._cookies = cookies
        self._cookie_bound = True
        uin_str = cookies.get("uin", cookies.get("p_uin", "0")).replace("o", "").replace("O", "")
        try:
            self._login_uin = int(uin_str)
        except (ValueError, TypeError):
            self._login_uin = 0
        self._save_state()
        yield self._command_result(event, f"Cookie bound! Account: {self._login_uin}")

    @qzone.command("unbind")
    async def cmd_unbind(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield self._command_result(event, "Admin only.")
            return
        self._cookies = {}
        self._cookie_bound = False
        self._login_uin = 0
        self._save_state()
        yield self._command_result(event, "Cookie unbound.")

    @qzone.command("post")
    async def cmd_post(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield self._command_result(event, "Admin only.")
            return
        if not self._cookie_bound:
            yield self._command_result(event, "Cookie not bound. Run /qzone autobind first.")
            return
        message_str = event.message_str.strip()
        for prefix in ("/qzone post", "/qzone post "):
            if message_str.startswith(prefix):
                message_str = message_str[len(prefix):].strip()
                break
        if not message_str:
            yield self._command_result(event, "Usage: /qzone post <content>")
            return
        images = self._get_images_from_event(event)
        image_hint = f" (+{len(images)} images)" if images else ""
        try:
            result = await self._publish_to_qzone(message_str, images if images else None)
        except Exception as e:
            yield self._command_result(event, f"Publish failed: {e}")
            return
        if isinstance(result, dict):
            if result.get("code", result.get("ret", -1)) == 0:
                yield self._command_result(event, f"Published!{image_hint}\n{message_str[:100]}")
            else:
                msg = result.get("message", result.get("msg", str(result)))
                yield self._command_result(event, f"Publish error: {msg}")
        else:
            yield self._command_result(event, f"Unknown response: {str(result)[:300]}")

    @qzone.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        yield self._command_result(event,
            "/qzone status    - check status\n"
            "/qzone autobind  - auto bind cookie\n"
            "/qzone bind      - manual bind cookie\n"
            "/qzone unbind    - unbind cookie\n"
            "/qzone post      - publish post\n"
            "/qzone generate   - AI generate from chat\n"
            "/qzone autopost    - AI generate & publish\n"
            "/qzone schedule   - show/start/stop scheduler\n"
            "/qzone next       - show next post times\n"
            "/qzone help      - this help"
        )

    @qzone.command("schedule")
    async def cmd_schedule(self, event: AstrMessageEvent):
        """Manage scheduler. Usage: /qzone schedule [start|stop|status]"""
        if not self._is_admin(event):
            yield self._command_result(event, "Admin only.")
            return

        msg = event.message_str.strip()
        parts = msg.split()
        sub = parts[2] if len(parts) > 2 else "status"

        next_info = self._format_next_times()

        if sub == "start":
            self._load_schedule_config()
            self._start_scheduler()
            yield self._command_result(event,
                f"Scheduler started.\n"
                f"Time: {self._schedule_start}:00 - {self._schedule_end}:00\n"
                f"Posts: {self._schedule_min}-{self._schedule_max} per day\n"
                f"{next_info}"
            )
        elif sub == "stop":
            self._schedule_enabled = False
            if self._schedule_task:
                self._schedule_task.cancel()
                self._schedule_task = None
            self._next_times = []
            yield self._command_result(event, "Scheduler stopped.")
        else:
            self._load_schedule_config()
            status_text = "running" if (self._schedule_task and not self._schedule_task.done()) else "stopped"
            yield self._command_result(event,
                f"Scheduler: {status_text}\n"
                f"Time: {self._schedule_start}:00 - {self._schedule_end}:00\n"
                f"Posts: {self._schedule_min}-{self._schedule_max}/day\n"
                f"Today: {self._posted_today} posted\n"
                f"Enabled: {self._schedule_enabled}\n"
                f"{next_info}"
            )

    @qzone.command("next")
    async def cmd_next(self, event: AstrMessageEvent):
        """Show next scheduled post times."""
        self._load_schedule_config()
        yield self._command_result(event, self._format_next_times())

    # -- LLM generation --

    async def _chat_history_context(self, event: AstrMessageEvent, max_lines: int = 50) -> str:
        context = getattr(self, "_context", None)
        if not context:
            return ""
        conv_mgr: Any = getattr(context, "conversation_manager", None)
        if conv_mgr is None:
            return ""
        try:
            umo: Any = getattr(event, "unified_msg_origin", None)
            if umo is None:
                return ""
            cid: Any = await conv_mgr.get_curr_conversation_id(umo)  # type: ignore[reportGeneralTypeIssues]
            if not cid:
                return ""
            conv = await conv_mgr.get_conversation(umo, cid)  # type: ignore[reportGeneralTypeIssues]
            if not conv:
                return ""
            history_raw: Any = getattr(conv, "history", None)
            if not history_raw or not isinstance(history_raw, str):
                return ""
            messages: Any = json.loads(history_raw)
            if not isinstance(messages, list):
                return ""
            lines: list[str] = []
            for msg in messages[-max_lines:]:
                if isinstance(msg, dict):
                    role = msg.get("role", "")
                    content = str(msg.get("content", ""))[:200]
                    d = "user" if role == "user" else ("AI" if role == "assistant" else role)
                    lines.append(f"[{d}]: {content}")
                elif isinstance(msg, str):
                    lines.append(msg[:200])
            return "\n".join(lines)
        except Exception:
            return ""

    async def _llm_generate(self, event: AstrMessageEvent, prompt: str,
                            system_prompt: str = "") -> str:
        context: Any = getattr(self, "_context", None)
        if not context:
            return ""

        # Get current provider ID
        provider_id: str = ""
        try:
            get_cid: Any = getattr(context, "get_current_chat_provider_id", None)
            if callable(get_cid):
                umo = getattr(event, "unified_msg_origin", "")
                provider_id = str(get_cid(umo) or "")
        except Exception:
            pass

        # Try llm_generate
        try:
            gen: Any = getattr(context, "llm_generate", None)
            if callable(gen) and provider_id:
                logger.info("QQ空间: calling llm_generate (provider=%s)", provider_id)
                try:
                    resp = await gen(  # type: ignore[reportGeneralTypeIssues]
                        prompt=prompt, system_prompt=system_prompt,
                        chat_provider_id=provider_id,
                    )
                except TypeError:
                    resp = await gen(  # type: ignore[reportGeneralTypeIssues]
                        prompt=prompt, chat_provider_id=provider_id,
                    )
                txt = self._text_from_response(resp)
                if txt:
                    logger.info("QQ空间: llm_generate ok, %d chars", len(txt))
                    return txt
        except Exception as exc:
            logger.warning("QQ空间: llm_generate error %s", exc)

        # Fallback: get_using_provider (sync, not await)
        try:
            get_p: Any = getattr(context, "get_using_provider", None)
            if callable(get_p):
                logger.info("QQ空间: calling get_using_provider")
                p = get_p()  # sync call, NOT await
                if p and hasattr(p, "text_chat"):
                    resp = await p.text_chat(prompt=prompt)  # type: ignore[reportGeneralTypeIssues]
                    txt = self._text_from_response(resp)
                    if txt:
                        logger.info("QQ空间: provider ok, %d chars", len(txt))
                        return txt
        except Exception as exc:
            logger.warning("QQ空间: provider error %s", exc)

        return ""

    @staticmethod
    def _text_from_response(response: Any) -> str:
        if response is None:
            return ""
        if isinstance(response, str):
            return response.strip()
        for attr in ("completion_text", "text", "content", "message"):
            val = getattr(response, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip()
        if isinstance(response, dict):
            for key in ("completion_text", "text", "content", "message"):
                val = response.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return ""

    @qzone.command("generate")
    async def cmd_generate(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield self._command_result(event, "Admin only.")
            return
        history = await self._chat_history_context(event)
        if not history:
            yield self._command_result(event, "No chat history yet. Chat first, then retry.")
            return
        prompt = (
            f"现在是 {datetime.now().strftime('%Y-%m-%d %H:%M')}。\n"
            "你是一个有自己性格的QQ空间用户。以下是你最近和朋友的聊天记录。\n"
            "请根据当前时间和聊天内容，以你自己的口吻和性格，\n"
            "写一段适合发到QQ空间的日常说说。\n"
            "要求: 内容符合时间段语境，简短有个性，只输出正文。\n\n"
            f"最近聊天记录:\n{history[:4000]}"
        )
        sp = (
            "你是一个有性格的QQ空间用户。请延续你在聊天记录中展现的人设和口吻，"
            "写一段日常说说。只输出正文。"
        )
        yield event.plain_result("Generating...")
        text = await self._llm_generate(event, prompt, sp)
        if not text:
            yield self._command_result(event, "Generation failed.")
            return
        yield self._command_result(event,
            f"Generated:\n---\n{text}\n---\n"
            f"Use /qzone autopost to publish directly."
        )

    @qzone.command("autopost")
    async def cmd_autopost(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield self._command_result(event, "Admin only.")
            return
        if not self._cookie_bound:
            yield self._command_result(event, "Cookie not bound. /qzone autobind first.")
            return
        history = await self._chat_history_context(event)
        if not history:
            yield self._command_result(event, "No chat history yet.")
            return
        prompt = (
            f"现在是 {datetime.now().strftime('%Y-%m-%d %H:%M')}。\n"
            "你是一个有自己性格的QQ空间用户。以下是最近聊天记录。\n"
            "根据当前时间和聊天内容，以你自己的口吻写一段日常说说。\n"
            "内容符合时间段语境，简短有个性，只输出正文。\n\n"
            f"聊天记录:\n{history[:4000]}"
        )
        sp = (
            "你是一个有性格的QQ空间用户。延续聊天中人设写日常说说。只输出正文。"
        )
        yield event.plain_result("Generating & publishing...")
        text = await self._llm_generate(event, prompt, sp)
        if not text:
            yield self._command_result(event, "Generation failed.")
            return
        try:
            result = await self._publish_to_qzone(text)
        except Exception as e:
            yield self._command_result(event, f"Publish failed: {e}")
            return
        if isinstance(result, dict) and result.get("code", result.get("ret", -1)) == 0:
            yield self._command_result(event, f"Auto-published!\n{text[:200]}")
        else:
            msg = result.get("message", result.get("msg", "")) if isinstance(result, dict) else ""
            yield self._command_result(event, f"Publish error: {msg}\nGenerated: {text[:200]}")

    # -- auto capture --

    @filter.event_message_type(filter.EventMessageType.ALL, priority=9999)
    async def _on_first_message(self, event: AstrMessageEvent):
        if self._onebot_client is not None and self._cookie_bound:
            return
        self._capture_onebot_client_from_context()
        if self._onebot_client is None:
            bot = getattr(event, "bot", None)
            if bot is not None and _detect_onebot_platform(bot):
                self._onebot_client = bot
        if not self._cookie_bound and self._onebot_client is not None:
            call_action = getattr(self._onebot_client, "call_action", None)
            if callable(call_action):
                await self._auto_bind_cookie(call_action)
