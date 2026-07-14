import asyncio
import contextlib
import json
import logging
import time
import aiohttp
import secrets
import random
import urllib.parse
from typing import Dict, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")


class ReactionTaskInfo:
    def __init__(self, task_id: str, task_type: str, channel_id: int, message_id: int, emoji: str, target_count: int):
        self.task_id = task_id
        self.task_type = task_type
        self.channel_id = channel_id
        self.message_id = message_id
        self.emoji = emoji
        self.target_count = target_count
        self.success_count = 0
        self.total_attempts = 0
        self.status = "Running"
        self.error_message = None
        self.timestamp = time.time()

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "channel_id": str(self.channel_id),
            "message_id": str(self.message_id),
            "emoji": self.emoji,
            "target_count": self.target_count,
            "success_count": self.success_count,
            "total_attempts": self.total_attempts,
            "status": self.status,
            "error_message": self.error_message,
            "timestamp": self.timestamp
        }


def resolve_image(value: str) -> str:
    """
    Fallback helper (deprecated, use DiscordClient.resolve_asset)
    """
    if not value:
        return value
    v = value.strip()
    if v.startswith("http://") or v.startswith("https://"):
        return f"mp:external/{v}"
    return v


class DiscordClient:
    def __init__(self, token: str, config: dict):
        self.token = token
        self.config = config
        self.ws = None
        self.heartbeat_task = None
        self._start_task: Optional[asyncio.Task] = None
        self.running = False
        self.session = None
        self.bad_token = False  # set True when Discord sends close 4004
        self._user_id: Optional[str] = None  # stored after READY
        self._session_id: Optional[str] = None  # stored after READY
        self._guild_id_for_stream: Optional[str] = None  # voice guild when streaming
        self._channel_id_for_stream: Optional[str] = None  # voice channel when streaming
        # Track when this client first connected (for elapsed timestamps)
        self._internal_start_time = int(time.time())
        # Voice state tracking
        self.vc_state: dict = {}   # {"guild_id": ..., "channel_id": ..., "guild_name": ..., "channel_name": ...}
        self._guild_cache: Dict[str, dict] = {}
        # Stream state tracking
        self._stream_active = False

    async def get_app_assets(self, app_id: str) -> list:
        if not hasattr(self, "_app_assets_cache"):
            self._app_assets_cache = {}
        if app_id in self._app_assets_cache:
            return self._app_assets_cache[app_id]
        if not self.session or self.session.closed:
            return []
        headers = {"Authorization": self.token}
        url = f"https://discord.com/api/v10/oauth2/applications/{app_id}/assets"
        try:
            async with self.session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    assets = await resp.json()
                    self._app_assets_cache[app_id] = assets
                    return assets
        except Exception as e:
            logger.error(f"Error fetching app assets: {e}")
        return []

    def resolve_asset(self, value: str, assets_list: list) -> str:
        if not value:
            return value
        v = value.strip()
        if v.startswith("http://") or v.startswith("https://"):
            return f"mp:external/{v}"
        for asset in assets_list:
            if asset.get("name") == v:
                return asset.get("id")
        return v

    async def start(self):
        self.running = True
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
        while self.running and not self.bad_token:
            try:
                await self.connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self.running or self.bad_token:
                    break
                logger.error(f"[{self.token[:10]}...] Connection error: {e}")
                if self.running:
                    await asyncio.sleep(5)

    async def stop(self):
        self.running = False
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            self.heartbeat_task = None
        if self.ws and not self.ws.closed:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self.session and not self.session.closed:
            try:
                await self.session.close()
            except Exception:
                pass
        self.ws = None
        self.session = None

    async def connect(self):
        uri = "wss://gateway.discord.gg/?v=10&encoding=json"
        try:
            async with self.session.ws_connect(uri) as ws:
                self.ws = ws
                hello = await ws.receive_json()
                heartbeat_interval = hello["d"]["heartbeat_interval"]

                if self.heartbeat_task:
                    self.heartbeat_task.cancel()
                self.heartbeat_task = asyncio.create_task(self.heartbeat(heartbeat_interval))

                await self.identify()

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        op = data.get("op")
                        t = data.get("t")
                        if op == 9:  # Invalid session
                            logger.warning(f"[{self.token[:10]}...] Received Invalid Session")
                            break
                        if t == "READY":
                            ready_data = data.get("d", {})
                            self._user_id = ready_data.get("user", {}).get("id")
                            self._session_id = ready_data.get("session_id")
                            logger.info(f"[{self.token[:10]}...] Ready! user_id={self._user_id}")
                            await self.update_presence()
                            await self.update_voice()
                        elif t == "VOICE_STATE_UPDATE":
                            await self._handle_voice_state(data.get("d", {}))
                        elif t == "GUILD_CREATE":
                            self._cache_guild(data.get("d", {}))
                        elif t in ("STREAM_CREATE", "STREAM_SERVER_UPDATE"):
                            self._stream_active = True
                            logger.info(f"[{self.token[:10]}...] Stream is now active (event: {t})")
                        elif t == "STREAM_DELETE":
                            self._stream_active = False
                            logger.info(f"[{self.token[:10]}...] Stream ended")
                    elif msg.type == aiohttp.WSMsgType.CLOSE:
                        close_code = msg.data
                        logger.warning(f"[{self.token[:10]}...] WS closed with code {close_code}")
                        if close_code == 4004:
                            self.bad_token = True
                            self.running = False  # stop reconnecting
                            logger.error(f"[{self.token[:10]}...] BAD TOKEN - will not reconnect")
                        break
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.bad_token:
                return
            raise

    def _cache_guild(self, guild_data: dict):
        guild_id = guild_data.get("id")
        if not guild_id:
            return
        channels = {}
        for ch in guild_data.get("channels", []):
            channels[ch["id"]] = ch.get("name", ch["id"])
        icon_hash = guild_data.get("icon")
        icon_url = f"https://cdn.discordapp.com/icons/{guild_id}/{icon_hash}.png" if icon_hash else None
        self._guild_cache[guild_id] = {
            "name": guild_data.get("name", guild_id),
            "icon_url": icon_url,
            "channels": channels
        }

    async def _handle_voice_state(self, d: dict):
        # Only care about our own user
        guild_id = d.get("guild_id")
        channel_id = d.get("channel_id")
        if channel_id:
            guild_info = self._guild_cache.get(guild_id, {})
            self.vc_state = {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "guild_name": guild_info.get("name", guild_id),
                "guild_icon": guild_info.get("icon_url"),
                "channel_name": guild_info.get("channels", {}).get(channel_id, channel_id),
                "connected": True,
                "streaming": self._stream_active
            }
        else:
            self._stream_active = False
            self.vc_state = {"connected": False, "guild_id": guild_id}

    # ─── Heartbeat ─────────────────────────────────────────────────────────────
    async def heartbeat(self, interval):
        while self.running:
            await asyncio.sleep(interval / 1000)
            if self.ws and not self.ws.closed:
                try:
                    await self.ws.send_json({"op": 1, "d": None})
                except Exception as e:
                    logger.error(f"Heartbeat failed: {e}")
                    break

    # ─── Identify ──────────────────────────────────────────────────────────────
    async def identify(self):
        platform = self.config.get("platform", "pc")
        browser = "Discord iOS" if platform == "mobile" else "chrome"
        os_name = "ios" if platform == "mobile" else "windows"
        device = "iPhone" if platform == "mobile" else "pc"

        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "intents": 0,
                "properties": {
                    "$os": os_name,
                    "$browser": browser,
                    "$device": device
                }
            }
        }
        await self.ws.send_json(payload)

    # ─── Presence ──────────────────────────────────────────────────────────────
    async def update_presence(self):
        if not self.ws or self.ws.closed:
            return

        status = self.config.get("status", "online")
        status_text = self.config.get("status_text", "")
        rpc = self.config.get("rpc", {})

        activities = []

        # Custom Status Text (type 4)
        if status_text:
            activities.append({
                "type": 4,
                "name": "Custom Status",
                "state": status_text
            })

        # Rich Presence
        if rpc and rpc.get("name"):
            activity_type_map = {
                "playing": 0,
                "streaming": 1,
                "listening": 2,
                "watching": 3
            }
            act_type_str = rpc.get("activity_type", "playing").lower()
            act_type = activity_type_map.get(act_type_str, 0)

            activity = {
                "type": act_type,
                "name": rpc.get("name", "Playing"),
            }

            # Application ID only required for image assets
            if rpc.get("application_id"):
                activity["application_id"] = rpc.get("application_id")

            if rpc.get("details"):
                activity["details"] = rpc["details"]
            if rpc.get("state"):
                activity["state"] = rpc["state"]

            # Streaming URL
            if act_type == 1 and rpc.get("url"):
                activity["url"] = rpc.get("url")

            # ── Timestamps ──────────────────────────────────────────────
            timestamps = {}
            ts_start_raw = str(rpc.get("timestamp_start", "")).strip()
            ts_end_raw = str(rpc.get("timestamp_end", "")).strip()

            if ts_start_raw.lower() in ("auto", "true"):
                timestamps["start"] = self._internal_start_time
            elif ts_start_raw:
                try:
                    timestamps["start"] = int(float(ts_start_raw))
                except (ValueError, TypeError):
                    pass

            if ts_end_raw:
                try:
                    timestamps["end"] = int(float(ts_end_raw))
                except (ValueError, TypeError):
                    pass

            if timestamps:
                activity["timestamps"] = timestamps

            # Fetch assets if application_id is provided
            app_id = rpc.get("application_id", "").strip()
            assets_list = []
            if app_id:
                assets_list = await self.get_app_assets(app_id)

            # ── Assets (images) ─────────────────────────────────────────
            assets = {}
            large_img = self.resolve_asset(rpc.get("large_image", ""), assets_list)
            if large_img:
                assets["large_image"] = large_img
            if rpc.get("large_text"):
                assets["large_text"] = rpc["large_text"]
            small_img = self.resolve_asset(rpc.get("small_image", ""), assets_list)
            if small_img:
                assets["small_image"] = small_img
            if rpc.get("small_text"):
                assets["small_text"] = rpc["small_text"]

            if assets:
                activity["assets"] = assets

            # ── Buttons (metadata format for user gateway RPC) ──────────
            button_labels = []
            button_urls = []
            for i in [1, 2]:
                lbl = rpc.get(f"btn{i}_label", "").strip()
                url = rpc.get(f"btn{i}_url", "").strip()
                if lbl and url:
                    if not url.startswith(("http://", "https://")):
                        url = f"https://{url}"
                    button_labels.append(lbl[:32])
                    button_urls.append(url[:512])

            if button_labels and button_urls:
                if not app_id:
                    logger.warning(f"[{self.token[:10]}...] RPC buttons need application_id — skipping buttons")
                else:
                    activity["metadata"] = json.dumps({
                        "button_urls": button_urls,
                        "button_labels": button_labels
                    })

            activities.append(activity)

        payload = {
            "op": 3,
            "d": {
                "since": 0,
                "activities": activities,
                "status": status,
                "afk": status == "idle"
            }
        }
        await self.ws.send_json(payload)

    # ─── Voice ─────────────────────────────────────────────────────────────────
    async def update_voice(self):
        if not self.ws or self.ws.closed:
            return

        voice = self.config.get("voice", {})
        guild_id = voice.get("guild_id", "").strip()
        channel_id = voice.get("channel_id", "").strip()
        self_stream = voice.get("self_stream", False)

        if guild_id:
            # Step 1: Send Voice State Update (op 4)
            payload = {
                "op": 4,
                "d": {
                    "guild_id": guild_id,
                    "channel_id": channel_id if channel_id else None,
                    "self_mute": voice.get("self_mute", True),
                    "self_deaf": voice.get("self_deaf", False),
                    "self_video": voice.get("self_video", False),
                    "self_stream": self_stream
                }
            }
            await self.ws.send_json(payload)
            logger.info(f"[{self.token[:10]}...] Sent op 4 (voice state). stream={self_stream}, channel={channel_id}")

            # Step 2: If self_stream enabled, send STREAM_CREATE (op 22) after a short delay
            # op 22 is the correct opcode for initiating a stream on the user gateway
            if self_stream and channel_id:
                await asyncio.sleep(1.5)
                if self.ws and not self.ws.closed:
                    # Try op 22 first (documented as STREAM_CREATE in the user gateway)
                    stream_payload = {
                        "op": 22,
                        "d": {
                            "type": "guild",
                            "guild_id": guild_id,
                            "channel_id": channel_id,
                            "preferred_region": None
                        }
                    }
                    try:
                        await self.ws.send_json(stream_payload)
                        logger.info(f"[{self.token[:10]}...] Sent op 22 (STREAM_CREATE) for channel {channel_id}")
                    except Exception as e:
                        logger.error(f"[{self.token[:10]}...] STREAM_CREATE (op 22) failed: {e}")

    # ─── Reactions ─────────────────────────────────────────────────────────────
    def _normalize_emoji(self, emoji: str) -> str:
        """Convert a raw emoji string to the proper API format."""
        if emoji.startswith("<") and emoji.endswith(">"):
            # Custom emoji format: <:name:id> or <a:name:id>
            parts = emoji.strip("<>").split(":")
            if len(parts) >= 3:
                name = parts[-2]
                eid = parts[-1]
                return f"{name}:{eid}"
            elif len(parts) == 2:
                return f"{parts[0]}:{parts[1]}"
        return emoji

    async def react_to_message(self, channel_id: int, message_id: int, emoji: str) -> bool:
        if not self.session or self.session.closed:
            return False

        headers = {"Authorization": self.token}
        emoji_encoded = urllib.parse.quote(emoji)
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/reactions/{emoji_encoded}/@me"

        try:
            async with self.session.put(url, headers=headers) as resp:
                if resp.status == 204:
                    return True
                elif resp.status == 429:
                    # Rate limited — wait and try once more
                    try:
                        rj = await resp.json()
                        wait = float(rj.get("retry_after", 1.0))
                    except Exception:
                        wait = 1.0
                    logger.warning(f"[{self.token[:10]}...] Rate limited, retrying in {wait}s")
                    await asyncio.sleep(wait)
                    async with self.session.put(url, headers=headers) as resp2:
                        return resp2.status == 204
                else:
                    text = await resp.text()
                    logger.error(f"[{self.token[:10]}...] Reaction failed: {resp.status} - {text}")
                    return False
        except Exception as e:
            logger.error(f"[{self.token[:10]}...] Reaction error: {e}")
            return False

    async def remove_reaction(self, channel_id: int, message_id: int, emoji: str) -> bool:
        """Remove this token's reaction from a message."""
        if not self.session or self.session.closed:
            return False

        headers = {"Authorization": self.token}
        emoji_encoded = urllib.parse.quote(emoji)
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/reactions/{emoji_encoded}/@me"

        try:
            async with self.session.delete(url, headers=headers) as resp:
                if resp.status in (204, 404):
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"[{self.token[:10]}...] Remove reaction failed: {resp.status} - {text}")
                    return False
        except Exception as e:
            logger.error(f"[{self.token[:10]}...] Remove reaction error: {e}")
            return False

    async def get_message_reactions(self, channel_id: int, message_id: int) -> list:
        if not self.session or self.session.closed:
            return []

        headers = {"Authorization": self.token}
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages?around={message_id}&limit=1"

        try:
            async with self.session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data or len(data) == 0:
                        logger.error(f"[{self.token[:10]}...] Fetch message failed: Not found in channel")
                        return []
                    msg_data = data[0]
                    if str(msg_data.get("id")) != str(message_id):
                        logger.error(f"[{self.token[:10]}...] Fetch message failed: Message ID mismatch")
                        return []

                    reactions = msg_data.get("reactions", [])
                    emoji_strs = []
                    for r in reactions:
                        emoji_data = r.get("emoji", {})
                        emoji_id = emoji_data.get("id")
                        emoji_name = emoji_data.get("name")
                        if emoji_id:
                            emoji_strs.append(f"{emoji_name}:{emoji_id}")
                        elif emoji_name:
                            emoji_strs.append(emoji_name)
                    return emoji_strs
                else:
                    text = await resp.text()
                    logger.error(f"[{self.token[:10]}...] Fetch message failed: {resp.status} - {text}")
        except Exception as e:
            logger.error(f"[{self.token[:10]}...] Fetch message error: {e}")
        return []


# ─── Token Manager ─────────────────────────────────────────────────────────────

class TokenManager:
    def __init__(self):
        self.clients: Dict[str, DiscordClient] = {}
        self.task_logs: list = []

    async def start_all(self, tokens_data: dict):
        for token, config in tokens_data.items():
            await self.add_token(token, config)

    def _launch_client(self, client: DiscordClient):
        client._start_task = asyncio.create_task(client.start())

    async def add_token(self, token: str, config: dict):
        if token in self.clients:
            await self.update_token(token, config)
            return
        client = DiscordClient(token, config)
        self.clients[token] = client
        self._launch_client(client)

    async def update_token(self, token: str, config: dict):
        if token not in self.clients:
            return
        client = self.clients[token]
        old_platform = client.config.get("platform", "pc")
        new_platform = config.get("platform", "pc")
        client.config = config
        if old_platform != new_platform:
            await client.stop()
            if client._start_task and not client._start_task.done():
                client._start_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await client._start_task
            new_client = DiscordClient(token, config)
            self.clients[token] = new_client
            self._launch_client(new_client)
        else:
            await client.update_presence()
            await client.update_voice()

    async def restart_token(self, token: str):
        if token not in self.clients:
            return
        client = self.clients[token]
        config = client.config
        await client.stop()
        if client._start_task and not client._start_task.done():
            client._start_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._start_task
        new_client = DiscordClient(token, config)
        new_client._internal_start_time = int(time.time())
        self.clients[token] = new_client
        self._launch_client(new_client)

    async def remove_token(self, token: str):
        if token in self.clients:
            client = self.clients.pop(token)
            await client.stop()
            if client._start_task and not client._start_task.done():
                client._start_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await client._start_task

    def get_vc_state(self, token: str) -> dict:
        if token in self.clients:
            return self.clients[token].vc_state
        return {}

    def get_bad_tokens(self) -> list:
        """Return list of token strings that were rejected by Discord (close code 4004)."""
        return [t for t, c in self.clients.items() if c.bad_token]

    def get_active_count(self) -> int:
        """Return number of currently active (running + session open) clients."""
        return sum(1 for c in self.clients.values() if c.running and c.session and not c.session.closed)

    async def stop_all(self):
        for client in list(self.clients.values()):
            await client.stop()
        self.clients.clear()

    # ─── Task Logs ─────────────────────────────────────────────────────────────
    def add_task_log(self, task_info: ReactionTaskInfo):
        self.task_logs.insert(0, task_info)
        if len(self.task_logs) > 100:
            self.task_logs.pop()

    # ─── Reaction Tasks ────────────────────────────────────────────────────────
    async def run_single_reaction_task(self, task_id: str, channel_id: int, message_id: int, emoji: str, count: int, delay_min: float, delay_max: float):
        task_info = next((t for t in self.task_logs if t.task_id == task_id), None)

        active_clients = [c for c in self.clients.values() if c.running and c.session and not c.session.closed]
        if len(active_clients) < count:
            logger.warning(f"Requested {count} reactions, but only {len(active_clients)} clients are active.")
            count = len(active_clients)

        if count == 0:
            if task_info:
                task_info.status = "Failed"
                task_info.error_message = "No active tokens available."
            return

        selected_clients = random.sample(active_clients, count)

        success_count = 0
        total_attempts = 0
        for client in selected_clients:
            total_attempts += 1
            if task_info:
                task_info.total_attempts = total_attempts
            try:
                emoji_str = client._normalize_emoji(emoji)
                success = await client.react_to_message(channel_id, message_id, emoji_str)
                if success:
                    success_count += 1
                if task_info:
                    task_info.success_count = success_count

                delay = random.uniform(delay_min, delay_max)
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Error reacting with token {client.token[:10]}: {e}")

        if task_info:
            if success_count > 0:
                task_info.status = "Completed"
            else:
                task_info.status = "Failed"
                task_info.error_message = "All reaction attempts failed."

    async def run_copy_all_reactions_task(self, task_id: str, channel_id: int, message_id: int, count: int, delay_min: float, delay_max: float):
        task_info = next((t for t in self.task_logs if t.task_id == task_id), None)

        active_clients = [c for c in self.clients.values() if c.running and c.session and not c.session.closed]
        if not active_clients:
            if task_info:
                task_info.status = "Failed"
                task_info.error_message = "No active tokens available."
            return

        # Use the first active client to fetch emojis
        emojis = []
        for client in active_clients:
            emojis = await client.get_message_reactions(channel_id, message_id)
            if emojis:
                break

        if not emojis:
            if task_info:
                task_info.status = "Failed"
                task_info.error_message = "No reactions found on the message."
            return

        actual_count = min(count, len(active_clients))
        if actual_count == 0:
            if task_info:
                task_info.status = "Failed"
                task_info.error_message = "Reaction count must be at least 1."
            return

        if task_info:
            task_info.emoji = ", ".join(emojis[:5]) + ("..." if len(emojis) > 5 else "")
            task_info.target_count = actual_count * len(emojis)

        success_count = 0
        total_attempts = 0

        for emoji in emojis:
            selected_clients = random.sample(active_clients, actual_count)
            for client in selected_clients:
                total_attempts += 1
                if task_info:
                    task_info.total_attempts = total_attempts
                try:
                    success = await client.react_to_message(channel_id, message_id, emoji)
                    if success:
                        success_count += 1
                    if task_info:
                        task_info.success_count = success_count

                    delay = random.uniform(delay_min, delay_max)
                    await asyncio.sleep(delay)
                except Exception as e:
                    logger.error(f"Error reacting with token {client.token[:10]} for emoji {emoji}: {e}")

        if task_info:
            if success_count > 0:
                task_info.status = "Completed"
            else:
                task_info.status = "Failed"
                task_info.error_message = "All reaction attempts failed."

    async def run_emoji_bomb_task(self, task_id: str, channel_id: int, message_id: int, emojis: list, count: int, delay_min: float, delay_max: float):
        """Send multiple different emojis to the same message using `count` tokens per emoji."""
        task_info = next((t for t in self.task_logs if t.task_id == task_id), None)

        active_clients = [c for c in self.clients.values() if c.running and c.session and not c.session.closed]
        if not active_clients:
            if task_info:
                task_info.status = "Failed"
                task_info.error_message = "No active tokens available."
            return

        actual_count = min(count, len(active_clients))
        if actual_count == 0:
            if task_info:
                task_info.status = "Failed"
                task_info.error_message = "No active tokens available."
            return

        if task_info:
            task_info.emoji = " ".join(emojis[:8])
            task_info.target_count = actual_count * len(emojis)

        success_count = 0
        total_attempts = 0

        for emoji in emojis:
            selected_clients = random.sample(active_clients, actual_count)
            for client in selected_clients:
                total_attempts += 1
                if task_info:
                    task_info.total_attempts = total_attempts
                try:
                    emoji_str = client._normalize_emoji(emoji.strip())
                    success = await client.react_to_message(channel_id, message_id, emoji_str)
                    if success:
                        success_count += 1
                    if task_info:
                        task_info.success_count = success_count

                    delay = random.uniform(delay_min, delay_max)
                    await asyncio.sleep(delay)
                except Exception as e:
                    logger.error(f"Error in emoji bomb with token {client.token[:10]}: {e}")

        if task_info:
            if success_count > 0:
                task_info.status = "Completed"
            else:
                task_info.status = "Failed"
                task_info.error_message = "All emoji bomb attempts failed."

    async def run_remove_reactions_task(self, task_id: str, channel_id: int, message_id: int, emoji: str, count: int, delay_min: float, delay_max: float):
        """Remove a specific reaction from a message using `count` tokens."""
        task_info = next((t for t in self.task_logs if t.task_id == task_id), None)

        active_clients = [c for c in self.clients.values() if c.running and c.session and not c.session.closed]
        if not active_clients:
            if task_info:
                task_info.status = "Failed"
                task_info.error_message = "No active tokens available."
            return

        actual_count = min(count, len(active_clients))
        selected_clients = random.sample(active_clients, actual_count)

        success_count = 0
        total_attempts = 0

        for client in selected_clients:
            total_attempts += 1
            if task_info:
                task_info.total_attempts = total_attempts
            try:
                emoji_str = client._normalize_emoji(emoji)
                success = await client.remove_reaction(channel_id, message_id, emoji_str)
                if success:
                    success_count += 1
                if task_info:
                    task_info.success_count = success_count

                delay = random.uniform(delay_min, delay_max)
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Error removing reaction with token {client.token[:10]}: {e}")

        if task_info:
            if success_count > 0:
                task_info.status = "Completed"
            else:
                task_info.status = "Failed"
                task_info.error_message = "All remove reaction attempts failed."


manager = TokenManager()
