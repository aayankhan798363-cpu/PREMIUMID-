import logging
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI, MONGO_DB_URI, DB_NAME, SUPER_ADMIN_ID, SUPER_ADMIN_IDS, update_emoji_cache

logger = logging.getLogger("AutoProtectionBot.DB")

client = None
db = None

async def init_db():
    """Initialize MongoDB client, collections, indexes, default permissions, and saved emojis."""
    global client, db
    try:
        client = AsyncIOMotorClient(MONGO_URI or MONGO_DB_URI)
        db = client[DB_NAME]
        
        # Collection 1: authorized_users (user_id unique)
        await db.authorized_users.create_index("user_id", unique=True)
        
        # Collection 2: bot_users (user_id unique)
        await db.bot_users.create_index("user_id", unique=True)

        # Collection 3: connected_chats (chat_id unique)
        await db.connected_chats.create_index("chat_id", unique=True)
        
        # Collection 4: channel_admins (chat_id + user_id unique compound index)
        await db.channel_admins.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
        await db.channel_admins.create_index("chat_id")
        
        # Collection 5: scammer_bans (banned_at index)
        await db.scammer_bans.create_index("banned_at")
        
        # Collection 6: custom_emojis (name unique)
        await db.custom_emojis.create_index("name", unique=True)
        
        # Always make sure Super Admin IDs are authorized by default
        for admin_id in SUPER_ADMIN_IDS:
            await db.authorized_users.update_one(
                {"user_id": admin_id},
                {"$set": {
                    "user_id": admin_id,
                    "granted_by": SUPER_ADMIN_ID,
                    "granted_at": datetime.utcnow().isoformat(),
                    "note": "Super Admin ID"
                }},
                upsert=True
            )
            
        # Load saved custom premium emojis into memory cache
        async for emoji_doc in db.custom_emojis.find():
            name = emoji_doc.get("name")
            char = emoji_doc.get("char", "✨")
            emoji_id = emoji_doc.get("emoji_id", "")
            if name and emoji_id:
                update_emoji_cache(name, char, emoji_id)
                
        logger.info("✅ MongoDB connected & initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to initialize MongoDB: {e}")
        raise e

# ==================== ACCESS CONTROL & USER REGISTRATION ====================
async def register_user(user_id: int, first_name: str = "", username: str = ""):
    if db is None:
        return
    try:
        now = datetime.utcnow().isoformat()
        await db.bot_users.update_one(
            {"user_id": int(user_id)},
            {"$set": {
                "user_id": int(user_id),
                "first_name": first_name or "",
                "username": username or "",
                "last_active": now
            }},
            upsert=True
        )
    except Exception as e:
        logger.warning(f"Error registering user {user_id}: {e}")
async def is_user_authorized(user_id: int) -> bool:
    if user_id in SUPER_ADMIN_IDS:
        return True
    doc = await db.authorized_users.find_one({"user_id": int(user_id)})
    return doc is not None

async def grant_user_access(user_id: int, granted_by: int, note: str = "") -> bool:
    await db.authorized_users.update_one(
        {"user_id": int(user_id)},
        {"$set": {
            "user_id": int(user_id),
            "granted_by": int(granted_by),
            "granted_at": datetime.utcnow().isoformat(),
            "note": note
        }},
        upsert=True
    )
    return True

async def revoke_user_access(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return False
    result = await db.authorized_users.delete_one({"user_id": int(user_id)})
    return result.deleted_count > 0

async def get_all_authorized_users():
    cursor = db.authorized_users.find({}).sort("granted_at", -1)
    users = await cursor.to_list(length=1000)
    return users

async def get_all_users():
    if db is None:
        return []
    cursor = db.bot_users.find({})
    return await cursor.to_list(length=5000)

# ==================== CHAT MANAGEMENT ====================
async def register_chat(chat_id: int, chat_title: str, chat_type: str, added_by: int):
    now = datetime.utcnow().isoformat()
    await db.connected_chats.update_one(
        {"chat_id": int(chat_id)},
        {
            "$set": {
                "chat_id": int(chat_id),
                "chat_title": chat_title,
                "chat_type": chat_type,
                "added_by": int(added_by),
                "updated_at": now
            },
            "$setOnInsert": {
                "auto_accept": 1,
                "anti_scam_premium_ban": 1,
                "report_shield": 1,
                "added_at": now,
                "admins": []
            }
        },
        upsert=True
    )

async def remove_chat(chat_id: int):
    await db.connected_chats.delete_one({"chat_id": int(chat_id)})
    await db.channel_admins.delete_many({"chat_id": int(chat_id)})

async def get_connected_chats():
    cursor = db.connected_chats.find({}).sort("added_at", -1)
    chats = await cursor.to_list(length=1000)
    return chats

async def get_chat_settings(chat_id: int):
    doc = await db.connected_chats.find_one({"chat_id": int(chat_id)})
    return doc

async def update_chat_setting(chat_id: int, setting_name: str, value: int):
    allowed_settings = ["auto_accept", "anti_scam_premium_ban", "report_shield"]
    if setting_name not in allowed_settings:
        return
    await db.connected_chats.update_one(
        {"chat_id": int(chat_id)},
        {"$set": {setting_name: int(value), "updated_at": datetime.utcnow().isoformat()}}
    )

# ==================== CHANNEL ADMINS PERSISTENCE ====================
async def save_channel_admin(chat_id: int, user_id: int, username: str = "", first_name: str = "", status: str = "administrator", custom_title: str = "", permissions: dict = None, updated_by: int = None, chat_title: str = ""):
    """Save or update a specific channel/group admin in MongoDB."""
    now = datetime.utcnow().isoformat()
    admin_doc = {
        "chat_id": int(chat_id),
        "chat_title": chat_title or "",
        "user_id": int(user_id),
        "username": username or "",
        "first_name": first_name or "",
        "status": status,
        "custom_title": custom_title or "",
        "permissions": permissions or {},
        "updated_by": int(updated_by) if updated_by else None,
        "updated_at": now
    }
    
    # Update in channel_admins collection
    await db.channel_admins.update_one(
        {"chat_id": int(chat_id), "user_id": int(user_id)},
        {"$set": admin_doc},
        upsert=True
    )
    
    # Update array element or push inside connected_chats document
    chat_doc = await db.connected_chats.find_one({"chat_id": int(chat_id)})
    if chat_doc:
        existing_admins = chat_doc.get("admins", [])
        # Filter out existing admin if present and append new
        updated_admins = [a for a in existing_admins if a.get("user_id") != int(user_id)]
        updated_admins.append(admin_doc)
        await db.connected_chats.update_one(
            {"chat_id": int(chat_id)},
            {"$set": {"admins": updated_admins, "admins_count": len(updated_admins), "updated_at": now}}
        )

async def save_channel_admins_list(chat_id: int, chat_title: str, admins_list: list):
    """Save full list of channel administrators fetched from Telegram into MongoDB."""
    now = datetime.utcnow().isoformat()
    processed_admins = []
    
    for cm in admins_list:
        user = cm.user
        perms = {}
        if hasattr(cm, 'can_post_messages'): perms['can_post_messages'] = bool(cm.can_post_messages)
        if hasattr(cm, 'can_edit_messages'): perms['can_edit_messages'] = bool(cm.can_edit_messages)
        if hasattr(cm, 'can_delete_messages'): perms['can_delete_messages'] = bool(cm.can_delete_messages)
        if hasattr(cm, 'can_restrict_members'): perms['can_restrict_members'] = bool(cm.can_restrict_members)
        if hasattr(cm, 'can_promote_members'): perms['can_promote_members'] = bool(cm.can_promote_members)
        if hasattr(cm, 'can_change_info'): perms['can_change_info'] = bool(cm.can_change_info)
        if hasattr(cm, 'can_invite_users'): perms['can_invite_users'] = bool(cm.can_invite_users)
        if hasattr(cm, 'can_pin_messages'): perms['can_pin_messages'] = bool(cm.can_pin_messages)

        admin_doc = {
            "chat_id": int(chat_id),
            "chat_title": chat_title or "",
            "user_id": int(user.id),
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "status": cm.status,
            "custom_title": getattr(cm, 'custom_title', '') or "",
            "is_anonymous": getattr(cm, 'is_anonymous', False),
            "permissions": perms,
            "updated_at": now
        }
        
        await db.channel_admins.update_one(
            {"chat_id": int(chat_id), "user_id": int(user.id)},
            {"$set": admin_doc},
            upsert=True
        )
        processed_admins.append(admin_doc)

    await db.connected_chats.update_one(
        {"chat_id": int(chat_id)},
        {"$set": {"admins": processed_admins, "admins_count": len(processed_admins), "updated_at": now}}
    )

async def remove_channel_admin(chat_id: int, user_id: int):
    """Remove an admin entry from MongoDB when demoted."""
    await db.channel_admins.delete_one({"chat_id": int(chat_id), "user_id": int(user_id)})
    chat_doc = await db.connected_chats.find_one({"chat_id": int(chat_id)})
    if chat_doc:
        existing_admins = chat_doc.get("admins", [])
        updated_admins = [a for a in existing_admins if a.get("user_id") != int(user_id)]
        await db.connected_chats.update_one(
            {"chat_id": int(chat_id)},
            {"$set": {"admins": updated_admins, "admins_count": len(updated_admins), "updated_at": datetime.utcnow().isoformat()}}
        )

async def get_channel_admins(chat_id: int = None):
    """Fetch all channel admins from MongoDB. If chat_id is specified, filter by chat."""
    query = {"chat_id": int(chat_id)} if chat_id else {}
    cursor = db.channel_admins.find(query).sort("updated_at", -1)
    admins = await cursor.to_list(length=1000)
    return admins

# ==================== SCAMMER BAN LOGGING ====================
async def log_scammer_ban(user_id: int, username: str, first_name: str, chat_id: int, chat_title: str, reason: str = "Premium Account Scammer"):
    now = datetime.utcnow().isoformat()
    log_doc = {
        "user_id": int(user_id),
        "username": username or "",
        "first_name": first_name or "",
        "chat_id": int(chat_id),
        "chat_title": chat_title or "",
        "reason": reason,
        "banned_at": now
    }
    await db.scammer_bans.insert_one(log_doc)

async def get_scammer_ban_logs(limit: int = 50):
    cursor = db.scammer_bans.find({}).sort("banned_at", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    return logs

async def get_stats():
    total_bans = await db.scammer_bans.count_documents({})
    total_chats = await db.connected_chats.count_documents({})
    total_users = await db.authorized_users.count_documents({})
    total_channel_admins = await db.channel_admins.count_documents({})
    return {
        "total_bans": total_bans,
        "total_chats": total_chats,
        "total_users": total_users,
        "total_channel_admins": total_channel_admins
    }

# ==================== CUSTOM PREMIUM EMOJI MANAGEMENT ====================
async def save_custom_emoji(name: str, emoji_id: str, char: str = "✨", updated_by: int = None) -> bool:
    now = datetime.utcnow().isoformat()
    await db.custom_emojis.update_one(
        {"name": name},
        {"$set": {
            "name": name,
            "emoji_id": str(emoji_id),
            "char": char,
            "updated_by": int(updated_by) if updated_by else None,
            "updated_at": now
        }},
        upsert=True
    )
    update_emoji_cache(name, char, emoji_id)
    return True

async def get_all_custom_emojis():
    cursor = db.custom_emojis.find({}).sort("name", 1)
    return await cursor.to_list(length=500)

async def delete_custom_emoji(name: str) -> bool:
    res = await db.custom_emojis.delete_one({"name": name})
    return res.deleted_count > 0
