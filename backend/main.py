from fastapi import FastAPI, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
import requests as req_lib
import contextlib
import asyncio
import secrets
import uvicorn
from .config import load_tokens, save_tokens, load_config, save_config, BASE_DIR
from .bot import manager, ReactionTaskInfo

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    tokens_data = load_tokens()
    await manager.start_all(tokens_data)
    yield
    await manager.stop_all()

app = FastAPI(
    title="Voicecord Dashboard",
    description="Multi-account Discord presence & voice manager",
    version="2.0.0",
    lifespan=lifespan
)

# ─── CORS (needed for Railway + any external clients) ─────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = BASE_DIR / "frontend"
frontend_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def get_current_user(token: str = Depends(oauth2_scheme)):
    config = load_config()
    if token != config["admin_pass"]:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token

# ─── Health Check (Railway uses this) ────────────────────────────────────────

@app.get("/health")
async def health_check():
    return JSONResponse({"status": "ok", "active_tokens": manager.get_active_count()})

# ─── Pages ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    f = frontend_dir / "index.html"
    return f.read_text(encoding="utf-8") if f.exists() else "Not Found"

@app.get("/login_page", response_class=HTMLResponse)
async def get_login_page():
    f = frontend_dir / "login.html"
    return f.read_text(encoding="utf-8") if f.exists() else "Not Found"

# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    config = load_config()
    if username == config["admin_user"] and password == config["admin_pass"]:
        return {"access_token": password, "token_type": "bearer"}
    raise HTTPException(status_code=400, detail="Incorrect username or password")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def discord_get(path: str, token: str):
    try:
        r = req_lib.get(f"https://discord.com/api/v10{path}", headers={"Authorization": token}, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def fetch_discord_profile(token: str):
    return discord_get("/users/@me", token)

def fetch_guild_info(bot_token: str, guild_id: str):
    """Fetch guild name via Discord API using the user token."""
    return discord_get(f"/guilds/{guild_id}?with_counts=false", bot_token)

def fetch_channel_info(bot_token: str, channel_id: str):
    return discord_get(f"/channels/{channel_id}", bot_token)

# ─── Token API ────────────────────────────────────────────────────────────────

@app.get("/api/tokens")
async def api_get_tokens(token: str = Depends(get_current_user)):
    return load_tokens()

@app.post("/api/tokens")
async def api_add_token(data: dict, token: str = Depends(get_current_user)):
    token_str = data.get("token", "").strip()
    if not token_str:
        raise HTTPException(status_code=400, detail="Token is required")

    # ── Validate token against Discord BEFORE saving ──────────────────────────
    try:
        r_validate = req_lib.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": token_str},
            timeout=8
        )
        if r_validate.status_code == 401:
            raise HTTPException(status_code=400, detail="Invalid or unauthorized Discord token. Please check the token and try again.")
        if r_validate.status_code == 403:
            raise HTTPException(status_code=400, detail="Token is valid but access was forbidden. The account may be locked or phone-verified.")
        if r_validate.status_code not in (200, 429):
            raise HTTPException(status_code=400, detail=f"Discord rejected the token (HTTP {r_validate.status_code}). Please verify it is correct.")
    except req_lib.exceptions.Timeout:
        raise HTTPException(status_code=408, detail="Could not verify token: Discord API request timed out.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token validation failed: {e}")

    config = data.get("config", {})

    profile = fetch_discord_profile(token_str)
    if profile:
        config["profile"] = {
            "id": profile.get("id"),
            "username": profile.get("username"),
            "global_name": profile.get("global_name"),
            "discriminator": profile.get("discriminator"),
            "avatar": profile.get("avatar")
        }
    else:
        config["profile"] = None

    tokens_data = load_tokens()
    tokens_data[token_str] = config
    save_tokens(tokens_data)
    await manager.add_token(token_str, config)
    return {"message": "Token added successfully", "profile": config["profile"]}

@app.get("/api/tokens/bad")
async def api_get_bad_tokens(token: str = Depends(get_current_user)):
    tokens_data = load_tokens()
    bad = manager.get_bad_tokens()
    result = []
    for t in bad:
        cfg = tokens_data.get(t, {})
        profile = cfg.get("profile") or {}
        result.append({
            "token_id": t,
            "username": profile.get("username", "Unknown"),
            "global_name": profile.get("global_name"),
            "avatar": profile.get("avatar"),
            "avatar_id": profile.get("id")
        })
    return result

@app.put("/api/tokens/{token_id:path}")
async def api_update_token(token_id: str, data: dict, token: str = Depends(get_current_user)):
    tokens_data = load_tokens()
    if token_id not in tokens_data:
        raise HTTPException(status_code=404, detail="Token not found")
    # Preserve profile
    if "profile" in tokens_data[token_id] and "profile" not in data:
        data["profile"] = tokens_data[token_id]["profile"]
    tokens_data[token_id] = data
    save_tokens(tokens_data)
    await manager.update_token(token_id, data)
    return {"message": "Token updated"}

@app.post("/api/tokens/{token_id:path}/restart")
async def api_restart_token(token_id: str, token: str = Depends(get_current_user)):
    tokens_data = load_tokens()
    if token_id not in tokens_data:
        raise HTTPException(status_code=404, detail="Token not found")
    await manager.restart_token(token_id)
    return {"message": "Token restarted"}

@app.delete("/api/tokens/{token_id:path}")
async def api_delete_token(token_id: str, token: str = Depends(get_current_user)):
    tokens_data = load_tokens()
    if token_id not in tokens_data:
        raise HTTPException(status_code=404, detail="Token not found")
    del tokens_data[token_id]
    save_tokens(tokens_data)
    await manager.remove_token(token_id)
    return {"message": "Token deleted"}

@app.post("/api/tokens/bulk/status")
async def api_bulk_status(data: dict, token: str = Depends(get_current_user)):
    status = data.get("status", "online")
    tokens_data = load_tokens()
    for t in tokens_data:
        tokens_data[t]["status"] = status
    save_tokens(tokens_data)
    for t in tokens_data:
        await manager.update_token(t, tokens_data[t])
    return {"message": f"All tokens set to {status}"}

@app.post("/api/tokens/bulk/restart")
async def api_bulk_restart(token: str = Depends(get_current_user)):
    tokens_data = load_tokens()
    for t in tokens_data:
        await manager.restart_token(t)
    return {"message": "All tokens restarted"}

@app.post("/api/tokens/bulk/disconnect-vc")
async def api_bulk_disconnect_vc(token: str = Depends(get_current_user)):
    tokens_data = load_tokens()
    for t in tokens_data:
        if "voice" in tokens_data[t]:
            tokens_data[t]["voice"]["channel_id"] = ""
    save_tokens(tokens_data)
    for t in tokens_data:
        await manager.update_token(t, tokens_data[t])
    return {"message": "All tokens disconnected from voice channels"}

@app.post("/api/tokens/{token_id:path}/offline")
async def api_offline_token(token_id: str, token: str = Depends(get_current_user)):
    tokens_data = load_tokens()
    if token_id not in tokens_data:
        raise HTTPException(status_code=404, detail="Token not found")

    tokens_data[token_id]["status"] = "invisible"
    save_tokens(tokens_data)
    await manager.update_token(token_id, tokens_data[token_id])
    return {"message": "Token is now offline (invisible)"}

@app.patch("/api/tokens/{token_id:path}/profile")
async def api_update_profile(token_id: str, data: dict, token: str = Depends(get_current_user)):
    tokens_data = load_tokens()
    if token_id not in tokens_data:
        raise HTTPException(status_code=404, detail="Token not found")

    headers = {
        "Authorization": token_id,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "X-Super-Properties": "eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiQ2hyb21lIiwiZGV2aWNlIjoiIiwic3lzdGVtX2xvY2FsZSI6ImVuLVVTIiwiYnJvd3Nlcl91c2VyX2FnZW50IjoiTW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkgQXBwbGVXZWJLaXQvNTM3LjM2IChLSFRNTCwgbGlrZSBHZWNrbykgQ2hyb21lLzEyNS4wLjAuMCBTYWZhcmkvNTM3LjM2IiwiYnJvd3Nlcl92ZXJzaW9uIjoiMTI1LjAuMC4wIiwib3NfdmVyc2lvbiI6IjEwIiwicmVmZXJyZXIiOiIiLCJyZWZlcnJpbmdfZG9tYWluIjoiIiwicmVmZXJyZXJfY3VycmVudCI6IiIsInJlZmVycmluZ19kb21haW5fY3VycmVudCI6IiIsInJlbGVhc2VfY2hhbm5lbCI6InN0YWJsZSIsImNsaWVudF9idWlsZF9udW1iZXIiOjMxMDk5MiwiY2xpZW50X2V2ZW50X3NvdXJjZSI6bnVsbH0="
    }

    errors = []
    profile_changed = False

    # --- Update global_name and/or avatar via /users/@me ---
    me_payload = {}
    if "global_name" in data and data["global_name"]:
        me_payload["global_name"] = data["global_name"]
    if "avatar" in data and data["avatar"]:
        me_payload["avatar"] = data["avatar"]

    if me_payload:
        try:
            r = req_lib.patch("https://discord.com/api/v10/users/@me", headers=headers, json=me_payload)
            if r.status_code == 200:
                profile_changed = True
                profile = r.json()
                if "profile" not in tokens_data[token_id] or tokens_data[token_id]["profile"] is None:
                    tokens_data[token_id]["profile"] = {}
                tokens_data[token_id]["profile"].update({
                    "id": profile.get("id"),
                    "username": profile.get("username"),
                    "global_name": profile.get("global_name"),
                    "discriminator": profile.get("discriminator"),
                    "avatar": profile.get("avatar")
                })
            elif r.status_code == 400:
                resp_json = r.json()
                if "captcha_key" in resp_json:
                    errors.append("Discord blocked this request with a captcha challenge. Try changing your account name via the Discord app directly.")
                else:
                    errors.append(f"Name/Avatar update failed: {r.text}")
            else:
                errors.append(f"Name/Avatar update failed ({r.status_code}): {r.text}")
        except Exception as e:
            errors.append(f"Name/Avatar update exception: {e}")

    # --- Update bio via /users/@me/profile ---
    if "bio" in data and data["bio"] is not None:
        bio_payload = {"bio": data["bio"]}
        try:
            r2 = req_lib.patch("https://discord.com/api/v10/users/@me/profile", headers=headers, json=bio_payload)
            if r2.status_code == 200:
                profile_changed = True
            elif r2.status_code == 400:
                resp_json = r2.json()
                if "captcha_key" in resp_json:
                    errors.append("Discord blocked the bio update with a captcha. Try updating bio directly from Discord app.")
                else:
                    errors.append(f"Bio update failed: {r2.text}")
            else:
                errors.append(f"Bio update failed ({r2.status_code}): {r2.text}")
        except Exception as e:
            errors.append(f"Bio update exception: {e}")

    if profile_changed:
        save_tokens(tokens_data)

    if errors and not profile_changed:
        raise HTTPException(status_code=400, detail=" | ".join(errors))

    return {
        "message": "Profile updated" + ((" (with warnings: " + "; ".join(errors) + ")") if errors else ""),
        "profile": tokens_data[token_id].get("profile", {})
    }

# ─── VC API ───────────────────────────────────────────────────────────────────

@app.get("/api/vc-states")
async def api_vc_states(token: str = Depends(get_current_user)):
    tokens_data = load_tokens()
    result = {}
    for t in tokens_data:
        state = manager.get_vc_state(t)
        state = dict(state) if state else {}
        profile = tokens_data[t].get("profile", {}) or {}

        # Fallback details fetching if connected
        if state.get("connected"):
            guild_id = state.get("guild_id")
            channel_id = state.get("channel_id")

            if guild_id and (not state.get("guild_name") or state.get("guild_name") == guild_id):
                g_info = fetch_guild_info(t, guild_id)
                if g_info:
                    state["guild_name"] = g_info.get("name", guild_id)
                    icon_hash = g_info.get("icon")
                    state["guild_icon"] = f"https://cdn.discordapp.com/icons/{guild_id}/{icon_hash}.png" if icon_hash else None

            if channel_id and (not state.get("channel_name") or state.get("channel_name") == channel_id):
                ch_info = fetch_channel_info(t, channel_id)
                if ch_info:
                    state["channel_name"] = ch_info.get("name", channel_id)

        result[t] = {
            "profile": profile,
            "vc_state": state
        }
    return result

@app.post("/api/vc/join")
async def api_vc_join(data: dict, token: str = Depends(get_current_user)):
    token_str = data.get("token")
    guild_id = data.get("guild_id", "").strip()
    channel_id = data.get("channel_id", "").strip()
    self_mute = data.get("self_mute", True)
    self_deaf = data.get("self_deaf", False)
    self_stream = data.get("self_stream", False)
    self_video = data.get("self_video", False)

    if not token_str or not guild_id or not channel_id:
        raise HTTPException(status_code=400, detail="token, guild_id, channel_id required")

    tokens_data = load_tokens()
    if token_str not in tokens_data:
        raise HTTPException(status_code=404, detail="Token not found")

    config = tokens_data[token_str]
    config["voice"] = {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "self_mute": self_mute,
        "self_deaf": self_deaf,
        "self_video": self_video,
        "self_stream": self_stream
    }
    tokens_data[token_str] = config
    save_tokens(tokens_data)
    await manager.update_token(token_str, config)
    return {"message": "Join command sent"}

@app.post("/api/vc/disconnect")
async def api_vc_disconnect(data: dict, token: str = Depends(get_current_user)):
    token_str = data.get("token")
    guild_id = data.get("guild_id", "").strip()

    if not token_str:
        raise HTTPException(status_code=400, detail="token required")

    tokens_data = load_tokens()
    if token_str not in tokens_data:
        raise HTTPException(status_code=404, detail="Token not found")

    config = tokens_data[token_str]
    config["voice"] = {
        "guild_id": guild_id,
        "channel_id": "",
        "self_mute": True,
        "self_deaf": False
    }
    tokens_data[token_str] = config
    save_tokens(tokens_data)
    await manager.update_token(token_str, config)
    return {"message": "Disconnect command sent"}

# ─── Settings API ─────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def api_get_settings(token: str = Depends(get_current_user)):
    config = load_config()
    return {
        "theme_accent": config.get("theme_accent", "#5865f2"),
        "theme_bg": config.get("theme_bg", "#0f0f14")
    }

@app.post("/api/settings")
async def api_save_settings(data: dict, token: str = Depends(get_current_user)):
    config = load_config()
    if "theme_accent" in data:
        config["theme_accent"] = data["theme_accent"]
    if "theme_bg" in data:
        config["theme_bg"] = data["theme_bg"]
    save_config(config)
    return {"message": "Settings saved"}

@app.get("/api/settings/export")
async def api_export_settings(token: str = Depends(get_current_user)):
    return load_tokens()

@app.post("/api/settings/import")
async def api_import_settings(data: dict, token: str = Depends(get_current_user)):
    save_tokens(data)
    await manager.stop_all()
    await manager.start_all(data)
    return {"message": "Data imported and all tokens restarted"}

# ─── Active Token Count ───────────────────────────────────────────────────────

@app.get("/api/active-count")
async def api_active_count(token: str = Depends(get_current_user)):
    return {"count": manager.get_active_count()}

# ─── Reactions API ────────────────────────────────────────────────────────────

@app.post("/api/react")
async def api_react(data: dict, token: str = Depends(get_current_user)):
    channel_id = data.get("channel_id")
    message_id = data.get("message_id")
    emoji = data.get("emoji")
    count = data.get("count", 1)
    delay_min = data.get("delay_min", 1.0)
    delay_max = data.get("delay_max", 5.0)

    if not channel_id or not message_id or not emoji:
        raise HTTPException(status_code=400, detail="channel_id, message_id, and emoji are required")

    task_id = secrets.token_hex(8)
    task_info = ReactionTaskInfo(
        task_id=task_id,
        task_type="Single Reaction",
        channel_id=int(channel_id),
        message_id=int(message_id),
        emoji=emoji,
        target_count=int(count)
    )
    manager.add_task_log(task_info)

    asyncio.create_task(
        manager.run_single_reaction_task(
            task_id, int(channel_id), int(message_id), emoji, int(count), float(delay_min), float(delay_max)
        )
    )
    return {"success": True, "message": "Reaction task started.", "task_id": task_id}

@app.post("/api/react-all")
async def api_react_all(data: dict, token: str = Depends(get_current_user)):
    channel_id = data.get("channel_id")
    message_id = data.get("message_id")
    count = data.get("count", 1)
    delay_min = data.get("delay_min", 1.0)
    delay_max = data.get("delay_max", 5.0)

    if not channel_id or not message_id:
        raise HTTPException(status_code=400, detail="channel_id and message_id are required")

    task_id = secrets.token_hex(8)
    task_info = ReactionTaskInfo(
        task_id=task_id,
        task_type="All Reactions",
        channel_id=int(channel_id),
        message_id=int(message_id),
        emoji="Fetching...",
        target_count=int(count)
    )
    manager.add_task_log(task_info)

    asyncio.create_task(
        manager.run_copy_all_reactions_task(
            task_id, int(channel_id), int(message_id), int(count), float(delay_min), float(delay_max)
        )
    )
    return {"success": True, "message": "All reactions task started.", "task_id": task_id}

@app.post("/api/react-bomb")
async def api_react_bomb(data: dict, token: str = Depends(get_current_user)):
    """Send multiple different emojis to the same message."""
    channel_id = data.get("channel_id")
    message_id = data.get("message_id")
    emojis_raw = data.get("emojis", "")  # space or newline separated
    count = data.get("count", 1)
    delay_min = data.get("delay_min", 0.5)
    delay_max = data.get("delay_max", 2.0)

    if not channel_id or not message_id or not emojis_raw:
        raise HTTPException(status_code=400, detail="channel_id, message_id, and emojis are required")

    # Parse emojis (split by space, comma, or newline)
    import re
    emojis = [e.strip() for e in re.split(r"[\s,]+", str(emojis_raw)) if e.strip()]
    if not emojis:
        raise HTTPException(status_code=400, detail="At least one emoji is required")

    task_id = secrets.token_hex(8)
    task_info = ReactionTaskInfo(
        task_id=task_id,
        task_type="Emoji Bomb",
        channel_id=int(channel_id),
        message_id=int(message_id),
        emoji=" ".join(emojis[:8]),
        target_count=int(count) * len(emojis)
    )
    manager.add_task_log(task_info)

    asyncio.create_task(
        manager.run_emoji_bomb_task(
            task_id, int(channel_id), int(message_id), emojis, int(count), float(delay_min), float(delay_max)
        )
    )
    return {"success": True, "message": f"Emoji bomb started with {len(emojis)} emojis.", "task_id": task_id}

@app.post("/api/react-remove")
async def api_react_remove(data: dict, token: str = Depends(get_current_user)):
    """Remove a reaction from a message using multiple tokens."""
    channel_id = data.get("channel_id")
    message_id = data.get("message_id")
    emoji = data.get("emoji")
    count = data.get("count", 1)
    delay_min = data.get("delay_min", 0.5)
    delay_max = data.get("delay_max", 2.0)

    if not channel_id or not message_id or not emoji:
        raise HTTPException(status_code=400, detail="channel_id, message_id, and emoji are required")

    task_id = secrets.token_hex(8)
    task_info = ReactionTaskInfo(
        task_id=task_id,
        task_type="Remove Reaction",
        channel_id=int(channel_id),
        message_id=int(message_id),
        emoji=emoji,
        target_count=int(count)
    )
    manager.add_task_log(task_info)

    asyncio.create_task(
        manager.run_remove_reactions_task(
            task_id, int(channel_id), int(message_id), emoji, int(count), float(delay_min), float(delay_max)
        )
    )
    return {"success": True, "message": "Remove reaction task started.", "task_id": task_id}

@app.get("/api/tasks")
async def api_get_tasks(token: str = Depends(get_current_user)):
    return {"tasks": [t.to_dict() for t in manager.task_logs]}

# ─── Lookup helpers for frontend ──────────────────────────────────────────────

@app.get("/api/lookup/guild/{token_id:path}/{guild_id}")
async def api_lookup_guild(token_id: str, guild_id: str, token: str = Depends(get_current_user)):
    """Use the user's own token to look up a guild name."""
    info = fetch_guild_info(token_id, guild_id)
    if not info:
        return {"name": guild_id, "icon": None}
    icon_hash = info.get("icon")
    icon_url = f"https://cdn.discordapp.com/icons/{guild_id}/{icon_hash}.png" if icon_hash else None
    return {"name": info.get("name", guild_id), "icon": icon_url}

@app.get("/api/lookup/channel/{token_id:path}/{channel_id}")
async def api_lookup_channel(token_id: str, channel_id: str, token: str = Depends(get_current_user)):
    info = fetch_channel_info(token_id, channel_id)
    if not info:
        return {"name": channel_id}
    return {"name": info.get("name", channel_id)}

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
