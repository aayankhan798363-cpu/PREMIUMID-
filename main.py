import asyncio
import logging
import os
import sys
import time
import html
import re
from datetime import datetime
from collections import defaultdict
from typing import Dict, Any, List, Optional, Callable, Awaitable

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware, types
from aiogram.enums import ParseMode, ChatType
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, Message,
    CallbackQuery, ChatMemberUpdated, ChatJoinRequest,
    TelegramObject
)
from aiogram.exceptions import TelegramAPIError

import config
from config import (
    BOT_TOKEN, SUPER_ADMIN_ID, SUPER_ADMIN_IDS, WELCOME_IMAGE,
    DEFAULT_SCAMMER_DM, UNAUTHORIZED_DM, PREMIUM_EMOJIS, get_p_emoji,
    get_btn_emoji, get_btn_emoji_id, logger
)
import database as db

def strip_html_tags(text: str) -> str:
    return re.sub(r'<[^>]*>', '', text)

async def safe_send_message(bot: Bot, chat_id: int, text: str, reply_markup=None, parse_mode="HTML"):
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            return await bot.send_message(chat_id, strip_html_tags(text), reply_markup=reply_markup)
        except Exception:
            return None

# Middleware for tracking user activity without blocking routers
class UserActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            try:
                await db.register_user(
                    user_id=event.from_user.id,
                    first_name=event.from_user.first_name or "",
                    username=event.from_user.username or ""
                )
            except Exception as e:
                logger.warning(f"Failed to register user: {e}")
        return await handler(event, data)

# Initialize Routers
guard_router = Router(name="access_guard")
start_router = Router(name="start_handler")
admin_router = Router(name="admin_handler")
admin_cmd_router = Router(name="admin_commands")
emoji_router = Router(name="emoji_handler")
protection_router = Router(name="protection_engine")

HELP_GUIDE_TEXT = (
    "★ <b><u>🛡️ Aᴜᴛᴏ Pʀᴏᴛᴇᴄᴛɪᴏɴ Bᴏᴛ Gᴜɪᴅᴇ</u></b> ★\n\n"
    "★ <b>/start</b> ── <i>Start bot & control panel</i>\n"
    "★ <b>/help</b> ── <i>Show usage guide & commands</i>\n"
    "★ <b>/stats</b> ── <i>MongoDB protection & ban statistics</i>\n"
    "★ <b>/allCommand</b> ── <i>Show full command documentation</i>\n"
    "★ <b>/promote @user</b> ── <i>Promote channel admin</i>\n"
    "★ <b>/admin @user</b> ── <i>Grant admin rights</i>\n"
    "★ <b>/demote @user</b> ── <i>Demote admin rights</i>\n\n"
    "★ <b><u>Aᴅᴍɪɴ Cᴏɴᴛʀᴏʟ Cᴏᴍᴍᴀɴᴅs:</u></b>\n"
    "★ <b>/grant &lt;id&gt;</b> | <b>/revoke &lt;id&gt;</b> | <b>/users</b> | <b>/scammers</b>\n\n"
    "★ <b>Note:</b> Give bot <i>Add Admins</i> & <i>Post Messages</i> rights."
)

user_msg_tracker = defaultdict(list)

ABUSE_WORDS = {
    "bitch", "bastard", "fuck", "motherfucker", "asshole", "dick", "pussy", "cunt", "whore", "slut",
    "madarchod", "bhenchod", "bhosdike", "gandu", "gand", "harami", "chutiya", "choot", "laund", "lauda",
    "lavde", "chut", "randi", "bhadwa", "bhadwe", "bhosdi", "gaand", "saala", "saale", "kamina", "kamine"
}

async def is_admin_user(user_id: int) -> bool:
    if user_id in SUPER_ADMIN_IDS:
        return True
    return await db.is_user_authorized(user_id)

# ==================== BUTTON MAKER & SAFE EDIT HELPER ====================
def make_btn(
    text: str,
    callback_data: Optional[str] = None,
    url: Optional[str] = None,
    style: str = "primary",
    emoji_name: Optional[str] = None
) -> InlineKeyboardButton:
    """Create stylish Telegram Inline Button with native button colors (primary, success, danger) & premium emoji icons."""
    kwargs = {
        "text": text,
        "style": style if style in ["primary", "success", "danger"] else "primary"
    }
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url

    if emoji_name:
        emoji_id = get_btn_emoji_id(emoji_name)
        if emoji_id:
            kwargs["icon_custom_emoji_id"] = str(emoji_id)
        else:
            char = get_btn_emoji(emoji_name)
            if char:
                kwargs["text"] = f"{char} {text}"

    return InlineKeyboardButton(**kwargs)

def clean_markup_for_fallback(reply_markup: Optional[InlineKeyboardMarkup]) -> Optional[InlineKeyboardMarkup]:
    """Clean custom emoji IDs from markup if Telegram client raises BUTTON_DATA_INVALID."""
    if not reply_markup or not hasattr(reply_markup, "inline_keyboard"):
        return reply_markup
    new_rows = []
    for row in reply_markup.inline_keyboard:
        new_row = []
        for btn in row:
            kwargs = {"text": btn.text}
            if hasattr(btn, "style") and btn.style:
                kwargs["style"] = btn.style
            if btn.callback_data:
                kwargs["callback_data"] = btn.callback_data
            if btn.url:
                kwargs["url"] = btn.url
            new_row.append(InlineKeyboardButton(**kwargs))
        new_rows.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=new_rows)

async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup = None):
    try:
        if callback.message.photo or callback.message.caption:
            await callback.message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await callback.message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e_edit:
        err_str = str(e_edit).lower()
        if "not modified" in err_str:
            return
        plain = strip_html_tags(text)
        cleaned_markup = clean_markup_for_fallback(reply_markup)
        try:
            if callback.message.photo or callback.message.caption:
                await callback.message.edit_caption(caption=plain, reply_markup=cleaned_markup)
            else:
                await callback.message.edit_text(text=plain, reply_markup=cleaned_markup)
        except Exception:
            try:
                await callback.message.answer(text, reply_markup=cleaned_markup, parse_mode="HTML")
            except Exception:
                try:
                    await callback.message.answer(plain, reply_markup=cleaned_markup)
                except Exception:
                    pass

# ==================== ACCESS GUARD ROUTER ====================
@guard_router.my_chat_member()
async def on_bot_chat_member_update(event: ChatMemberUpdated, bot: Bot):
    new_status = event.new_chat_member.status
    old_status = event.old_chat_member.status
    chat_id = event.chat.id
    chat_title = html.escape(event.chat.title or f"Chat {chat_id}")
    added_by_user = event.from_user

    # Bot PROMOTED or ADDED as Administrator / Member
    if new_status in ["administrator", "member"] and old_status != "administrator":
        if added_by_user and not await is_admin_user(added_by_user.id):
            logger.warning(f"⛔ Unauthorized user {added_by_user.id} added bot to {chat_id}. Leaving chat!")
            try:
                msg_text = f"{get_p_emoji('sweat')} <b>RESTRICTION NOTICE</b> {get_p_emoji('sweat')}\n\n{html.escape(UNAUTHORIZED_DM)}"
                await safe_send_message(bot, added_by_user.id, msg_text, parse_mode="HTML")
            except Exception:
                pass
            try:
                await bot.leave_chat(chat_id)
            except Exception:
                pass
            return

        user_id = added_by_user.id if added_by_user else SUPER_ADMIN_ID
        await db.register_chat(chat_id, event.chat.title or f"Chat {chat_id}", event.chat.type, user_id)
        logger.info(f"✅ Bot registered/updated in chat {chat_id} ({chat_title}) in MongoDB")

        # Auto-sync channel administrators to MongoDB
        try:
            admins = await bot.get_chat_administrators(chat_id)
            await db.save_channel_admins_list(chat_id, event.chat.title or f"Chat {chat_id}", admins)
            logger.info(f"👥 Synced {len(admins)} channel admins into MongoDB for {chat_title}")
        except Exception as e:
            logger.warning(f"Could not fetch channel admins for {chat_id}: {e}")

        # Instant Admin Alert DM on Promotion
        actor_name = f"@{html.escape(added_by_user.username)}" if (added_by_user and added_by_user.username) else (html.escape(added_by_user.first_name) if added_by_user else "Admin")
        alert_text = (
            f"🟢 <b>BOT PROMOTED AS ADMIN!</b>\n\n"
            f"• <b>Target Chat:</b> <b>{chat_title}</b> (<code>{chat_id}</code>)\n"
            f"• <b>Action By:</b> {actor_name}\n"
            f"• <b>Status:</b> Protection Active & Shield Online! {get_p_emoji('shield')}"
        )
        for super_id in SUPER_ADMIN_IDS:
            await safe_send_message(bot, super_id, alert_text, parse_mode="HTML")

    # Bot DEMOTED or REMOVED
    elif new_status in ["left", "kicked", "restricted"] and old_status in ["administrator", "member"]:
        await db.remove_chat(chat_id)
        actor_name = f"@{html.escape(added_by_user.username)}" if (added_by_user and added_by_user.username) else (html.escape(added_by_user.first_name) if added_by_user else "User")
        alert_text = (
            f"🔴 <b>BOT REMOVED / DEMOTED!</b>\n\n"
            f"• <b>Target Chat:</b> <b>{chat_title}</b> (<code>{chat_id}</code>)\n"
            f"• <b>Action By:</b> {actor_name}\n"
            f"• <b>Status:</b> Chat removed from MongoDB protection list."
        )
        for super_id in SUPER_ADMIN_IDS:
            await safe_send_message(bot, super_id, alert_text, parse_mode="HTML")

# ==================== START HANDLER ROUTER ====================
async def get_main_keyboard(bot_username: str, is_admin: bool = False) -> InlineKeyboardMarkup:
    channel_url = f"https://t.me/{bot_username}?startchannel=true&admin=post_messages+edit_messages+delete_messages+restrict_members+invite_users+pin_messages+manage_chat+promote_members"
    group_url = f"https://t.me/{bot_username}?startgroup=true&admin=delete_messages+restrict_members+invite_users+pin_messages+manage_chat+promote_members"

    buttons = [
        [
            make_btn("Add to Channel", url=channel_url, style="primary", emoji_name="fire"),
            make_btn("Add to Group", url=group_url, style="primary", emoji_name="zap")
        ],
        [
            make_btn("Connect Chats / Channels", callback_data="cmd_connect_chats", style="primary", emoji_name="phone"),
            make_btn("Live Shield Stats", callback_data="cmd_live_stats", style="primary", emoji_name="chart_up")
        ]
    ]
    if is_admin:
        buttons.append([make_btn("Admin Control Panel", callback_data="cmd_admin_panel", style="danger", emoji_name="crown")])

    buttons.append([make_btn("System Status: ACTIVE", callback_data="cmd_system_status", style="success", emoji_name="diamond")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@start_router.message(CommandStart())
async def command_start_handler(message: Message, bot: Bot):
    user = message.from_user
    if not user:
        return

    if not await is_admin_user(user.id):
        dm_text = f"{get_p_emoji('sweat')} <b>RESTRICTION NOTICE</b> {get_p_emoji('sweat')}\n\n{html.escape(UNAUTHORIZED_DM)}"
        try:
            await message.answer(dm_text, parse_mode="HTML")
        except Exception:
            await message.answer(strip_html_tags(dm_text))
        return

    bot_user = await bot.get_me()
    keyboard = await get_main_keyboard(bot_user.username, is_admin=(user.id in SUPER_ADMIN_IDS))

    user_name = html.escape(user.first_name or "Admin")

    caption_text = (
        f"{get_p_emoji('shield')} <b>AUTO PROTECTION & ANTI-SCAMMER BOT</b> {get_p_emoji('shield')}\n\n"
        f"Welcome Admin, <b>{user_name}</b>!\n\n"
        f"• {get_p_emoji('check_box')} <b>Instant Premium Scam Ban</b>: Auto-detects & bans Premium account scammers.\n"
        f"• {get_p_emoji('speech')} <b>DM Warning System</b>: Dispatches instant warning DMs to caught scammers.\n"
        f"• {get_p_emoji('sparkles')} <b>Normal Accounts Safe</b>: Only Premium accounts are targeted by anti-scam ban rules.\n"
        f"• {get_p_emoji('snowflake')} <b>Channel Report Shield</b>: Protects channels against malicious report flooding.\n"
        f"• {get_p_emoji('ninja')} <b>Unauthorized Access Guard</b>: Rejects non-authorized user access instantly.\n\n"
        f"<i>Select an option below to manage your channels or groups:</i>"
    )
    try:
        await message.answer_photo(photo=WELCOME_IMAGE, caption=caption_text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e_photo:
        logger.warning(f"Failed to send welcome photo: {e_photo}, trying text fallback...")
        try:
            await message.answer(caption_text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e_text:
            logger.error(f"Failed HTML caption answer: {e_text}, falling back to plain text")
            await message.answer(strip_html_tags(caption_text), reply_markup=keyboard)

@start_router.message(Command("help"))
async def command_help_handler(message: Message):
    if not await is_admin_user(message.from_user.id):
        await message.answer(f"{get_p_emoji('sweat')} <b>RESTRICTION NOTICE:</b> {UNAUTHORIZED_DM}", parse_mode="HTML")
        return
    await message.answer(HELP_GUIDE_TEXT, parse_mode="HTML")

@start_router.message(Command("allCommand"))
async def command_all_commands_handler(message: Message):
    if not await is_admin_user(message.from_user.id):
        await message.answer(f"{get_p_emoji('sweat')} <b>RESTRICTION NOTICE:</b> {UNAUTHORIZED_DM}", parse_mode="HTML")
        return
    full_details = (
        f"{HELP_GUIDE_TEXT}\n\n"
        f"★ <b><u>Dᴇᴛᴀɪʟᴇᴅ Cᴏᴍᴍᴀɴᴅ Dᴏᴄᴜᴍᴇɴᴛᴀᴛɪᴏɴ:</u></b>\n"
        f"★ <code>/promote @user</code> ── Promotes user with Posting & Invite permissions.\n"
        f"★ <code>/admin @user</code> ── Grants admin rights with Invite Link.\n"
        f"★ <code>/demote @user</code> ── Strips all admin permissions.\n"
        f"★ <code>/grant &lt;id&gt;</code> ── Grant bot access in MongoDB (Super Admin).\n"
        f"★ <code>/revoke &lt;id&gt;</code> ── Revoke bot access in MongoDB (Super Admin).\n"
        f"★ <code>/scammers</code> ── View recent banned scammer logs in MongoDB."
    )
    await message.answer(full_details, parse_mode="HTML")

@start_router.message(Command("stats"))
async def command_stats_handler(message: Message):
    if not await is_admin_user(message.from_user.id):
        await message.answer(f"{get_p_emoji('sweat')} <b>RESTRICTION NOTICE:</b> {UNAUTHORIZED_DM}", parse_mode="HTML")
        return
    stats = await db.get_stats()
    text = (
        f"{get_p_emoji('chart_up')} <b>BOT STATISTICS & PROTECTION STATUS</b> {get_p_emoji('chart_up')}\n\n"
        f"• {get_p_emoji('ninja')} <b>Total Premium Scammers Banned:</b> {stats['total_bans']}\n"
        f"• {get_p_emoji('phone')} <b>Protected Channels & Groups:</b> {stats['total_chats']}\n"
        f"• {get_p_emoji('crown')} <b>Authorized Admins:</b> {stats['total_users']}\n"
        f"• {get_p_emoji('zap')} <b>Saved Channel Admins:</b> {stats.get('total_channel_admins', 0)}\n\n"
        f"{get_p_emoji('check_box')} <i>All protection modules active!</i>"
    )
    await message.answer(text, parse_mode="HTML")

@start_router.callback_query(F.data == "cmd_connect_chats")
async def callback_connect_chats(callback: CallbackQuery):
    if not await is_admin_user(callback.from_user.id):
        await callback.answer("⛔ Access Denied!", show_alert=True)
        return

    chats = await db.get_connected_chats()
    if not chats:
        text = f"{get_p_emoji('question')} <b>No Channels or Groups Connected Yet!</b>\n\nUse 'Add to Channel' or 'Add to Group' buttons."
        kb = InlineKeyboardMarkup(inline_keyboard=[[make_btn("Back", callback_data="cmd_back_main", style="danger", emoji_name="arrow_right")]])
        await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        return

    text = f"{get_p_emoji('phone')} <b>CONNECTED CHATS & PROTECTION STATUS</b>\n\n"
    buttons = []
    for chat in chats:
        c_title = html.escape(chat.get("chat_title") or f"Chat {chat['chat_id']}")
        c_settings = await db.get_chat_settings(chat['chat_id'])
        auto_acc = f"{get_p_emoji('check_box')} ON" if (c_settings and c_settings.get("auto_accept")) else f"{get_p_emoji('sweat')} OFF"
        scam_ban = f"{get_p_emoji('shield')} ON" if (c_settings and c_settings.get("anti_scam_premium_ban")) else f"{get_p_emoji('sweat')} OFF"
        text += f"• {get_p_emoji('phone')} <b>{c_title}</b> (ID: <code>{chat['chat_id']}</code>)\n  Auto-Accept: {auto_acc} | Premium Scam Ban: {scam_ban}\n\n"
        buttons.append([make_btn(f"Settings: {c_title[:18]}", callback_data=f"chat_settings_{chat['chat_id']}", style="primary", emoji_name="gear")])

    buttons.append([make_btn("Back to Menu", callback_data="cmd_back_main", style="danger", emoji_name="arrow_right")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

@start_router.callback_query(F.data.startswith("chat_settings_"))
async def callback_chat_settings_detail(callback: CallbackQuery):
    chat_id = int(callback.data.split("_")[2])
    settings = await db.get_chat_settings(chat_id)
    if not settings:
        await callback.answer("Chat not found!", show_alert=True)
        return

    c_title = html.escape(settings.get("chat_title", f"Chat {chat_id}"))
    aa_status = f"{get_p_emoji('check_box')} ON" if settings.get("auto_accept") else f"{get_p_emoji('sweat')} OFF"
    pb_status = f"{get_p_emoji('shield')} ON" if settings.get("anti_scam_premium_ban") else f"{get_p_emoji('sweat')} OFF"
    rs_status = f"{get_p_emoji('snowflake')} ON" if settings.get("report_shield") else f"{get_p_emoji('sweat')} OFF"

    btn_aa = "ON" if settings.get("auto_accept") else "OFF"
    btn_pb = "ON" if settings.get("anti_scam_premium_ban") else "OFF"
    btn_rs = "ON" if settings.get("report_shield") else "OFF"

    text = (
        f"{get_p_emoji('gear')} <b>Chat Protection Settings</b>\n"
        f"<b>Target Channel:</b> {get_p_emoji('phone')} <b>{c_title}</b> (<code>{chat_id}</code>)\n\n"
        f"• <b>Auto-Accept Join Requests:</b> {aa_status}\n"
        f"• <b>Premium Scammer Auto-Ban:</b> {pb_status}\n"
        f"• <b>Channel Mass Report Shield:</b> {rs_status}\n"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn(f"Auto-Accept Join Requests ({btn_aa})", callback_data=f"toggle_setting_{chat_id}_auto_accept", style="success" if btn_aa == "ON" else "danger", emoji_name="check_box" if btn_aa == "ON" else "sweat")],
        [make_btn(f"Premium Scammer Auto-Ban ({btn_pb})", callback_data=f"toggle_setting_{chat_id}_anti_scam_premium_ban", style="success" if btn_pb == "ON" else "danger", emoji_name="shield" if btn_pb == "ON" else "sweat")],
        [make_btn(f"Channel Mass Report Shield ({btn_rs})", callback_data=f"toggle_setting_{chat_id}_report_shield", style="success" if btn_rs == "ON" else "danger", emoji_name="snowflake" if btn_rs == "ON" else "sweat")],
        [make_btn("Scan & Ban Old Premium Scammers", callback_data=f"scan_old_scammers_{chat_id}", style="danger", emoji_name="ninja")],
        [make_btn("Back to Connected Chats", callback_data="cmd_connect_chats", style="danger", emoji_name="arrow_right")]
    ])
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

@start_router.callback_query(F.data.startswith("toggle_setting_"))
async def callback_toggle_setting(callback: CallbackQuery):
    parts = callback.data.split("_")
    chat_id = int(parts[2])
    setting_name = "_".join(parts[3:])

    settings = await db.get_chat_settings(chat_id)
    if settings:
        current_val = settings.get(setting_name, 0)
        new_val = 0 if current_val == 1 else 1
        await db.update_chat_setting(chat_id, setting_name, new_val)
        await callback.answer(f"Setting '{setting_name}' updated!", show_alert=False)
        await callback_chat_settings_detail(callback)

# Scan and ban old existing non-admin Premium account scammers
@start_router.callback_query(F.data.startswith("scan_old_scammers_"))
async def callback_scan_old_scammers(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    if not await is_admin_user(user_id):
        await callback.answer("⛔ Access Denied!", show_alert=True)
        return

    chat_id = int(callback.data.split("_")[3])
    settings = await db.get_chat_settings(chat_id)
    c_title = settings.get("chat_title", f"Chat {chat_id}") if settings else f"Chat {chat_id}"

    await callback.answer("🧹 Scanning for existing non-admin Premium scammers...", show_alert=True)
    
    banned_count = 0
    try:
        # Fetch admins to ensure we NEVER ban any channel admin
        admin_members = await bot.get_chat_administrators(chat_id)
        admin_ids = {m.user.id for m in admin_members}

        # Send status updates to Bot DM
        progress_msg = await bot.send_message(
            user_id,
            f"{get_p_emoji('ninja')} <b>OLD SCAMMER SCANNER ACTIVATED</b>\n\n"
            f"Scanning target chat: <b>{c_title}</b> (<code>{chat_id}</code>)...\n"
            f"Please wait while non-admin Premium accounts are processed.",
            parse_mode="HTML"
        )
        
        # Check saved channel admins from DB as extra safety
        saved_admins = await db.get_channel_admins()
        for sa in saved_admins:
            if sa.get("chat_id") == chat_id:
                admin_ids.add(sa.get("user_id"))

        # Collect candidate user IDs from database
        candidate_users = await db.get_all_users()
        scammer_logs = await db.get_scammer_ban_logs(limit=200)
        
        target_user_ids = set()
        for u in candidate_users:
            uid = u.get("user_id")
            if uid and uid not in admin_ids:
                target_user_ids.add(uid)

        for sl in scammer_logs:
            uid = sl.get("user_id")
            if uid and uid not in admin_ids:
                target_user_ids.add(uid)

        # Scan each candidate user ID for Premium account status
        for target_user_id in target_user_ids:
            try:
                member = await bot.get_chat_member(chat_id=chat_id, user_id=target_user_id)
                if member.status in ["member", "restricted"]:
                    user_obj = member.user
                    if user_obj and bool(user_obj.is_premium):
                        # Ban non-admin Premium account
                        await bot.ban_chat_member(chat_id=chat_id, user_id=target_user_id)
                        try:
                            dm_text = f"{get_p_emoji('ninja')} <b>SECURITY BAN NOTICE</b>\n\n{DEFAULT_SCAMMER_DM}"
                            await bot.send_message(user_id=target_user_id, text=dm_text, parse_mode="HTML")
                        except Exception:
                            pass
                        await db.log_scammer_ban(
                            user_id=target_user_id,
                            username=user_obj.username or "",
                            first_name=user_obj.first_name or "",
                            chat_id=chat_id,
                            chat_title=c_title,
                            reason="Old Existing Premium Account Scan & Ban"
                        )
                        banned_count += 1
            except Exception:
                pass

        # Complete scan report sent via Bot DM
        report_text = (
            f"{get_p_emoji('check_box')} <b>SCAN & CLEAN COMPLETE</b>\n\n"
            f"• <b>Target Chat:</b> {c_title} (<code>{chat_id}</code>)\n"
            f"• <b>Protected Admins Excluded:</b> {len(admin_ids)}\n"
            f"• <b>Scanned Users Checked:</b> {len(target_user_ids)}\n"
            f"• <b>Non-Admin Premium Accounts Banned:</b> {banned_count}\n\n"
            f"<i>Your channel is now fully protected against existing scammer accounts!</i>"
        )
        await progress_msg.edit_text(report_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error scanning old scammers in chat {chat_id}: {e}")
        await bot.send_message(user_id, f"❌ Error scanning scammers: {e}", parse_mode="HTML")

@start_router.callback_query(F.data == "cmd_live_stats")
async def callback_live_stats(callback: CallbackQuery):
    stats = await db.get_stats()
    text = (
        f"{get_p_emoji('chart_up')} <b>LIVE SHIELD STATISTICS</b> {get_p_emoji('chart_up')}\n\n"
        f"• {get_p_emoji('ninja')} <b>Total Premium Scammers Banned:</b> {stats['total_bans']}\n"
        f"• {get_p_emoji('phone')} <b>Protected Channels & Groups:</b> {stats['total_chats']}\n"
        f"• {get_p_emoji('crown')} <b>Authorized Admins:</b> {stats['total_users']}\n"
        f"• {get_p_emoji('zap')} <b>Saved Channel Admins:</b> {stats.get('total_channel_admins', 0)}\n\n"
        f"{get_p_emoji('check_box')} <i>Shield Status: Active & Monitoring</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[make_btn("Back to Menu", callback_data="cmd_back_main", style="danger", emoji_name="arrow_right")]])
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

@start_router.callback_query(F.data == "cmd_system_status")
async def callback_system_status(callback: CallbackQuery):
    await callback.answer("⚡ All protection modules active & actively guarding channels/groups!", show_alert=True)

@start_router.callback_query(F.data == "cmd_back_main")
async def callback_back_main(callback: CallbackQuery, bot: Bot):
    user = callback.from_user
    bot_user = await bot.get_me()
    keyboard = await get_main_keyboard(bot_user.username, is_admin=(user.id in SUPER_ADMIN_IDS))

    user_name = html.escape(user.first_name or "Admin")

    caption_text = (
        f"{get_p_emoji('shield')} <b>AUTO PROTECTION & ANTI-SCAMMER BOT</b> {get_p_emoji('shield')}\n\n"
        f"Welcome Admin, <b>{user_name}</b>!\n\n"
        f"• {get_p_emoji('check_box')} <b>Instant Premium Scam Ban</b>: Auto-detects & bans Premium account scammers.\n"
        f"• {get_p_emoji('speech')} <b>DM Warning System</b>: Dispatches instant warning DMs to caught scammers.\n"
        f"• {get_p_emoji('sparkles')} <b>Normal Accounts Safe</b>: Only Premium accounts are targeted by anti-scam ban rules.\n"
        f"• {get_p_emoji('snowflake')} <b>Channel Report Shield</b>: Protects channels against malicious report flooding.\n"
        f"• {get_p_emoji('ninja')} <b>Unauthorized Access Guard</b>: Rejects non-authorized user access instantly.\n\n"
        f"<i>Select an option below to manage your channels or groups:</i>"
    )
    await safe_edit_message(callback, caption_text, reply_markup=keyboard)
    await callback.answer()

# ==================== ADMIN CONTROL PANEL ROUTER ====================
@admin_router.message(Command("admin"))
async def command_admin_handler(message: Message, bot: Bot):
    if not await is_admin_user(message.from_user.id):
        await message.answer(f"{get_p_emoji('sweat')} <b>RESTRICTION:</b> Authorized Admin only!", parse_mode="HTML")
        return

    stats = await db.get_stats()
    text = (
        f"{get_p_emoji('crown')} <b>SUPER ADMIN CONTROL PANEL</b> {get_p_emoji('crown')}\n\n"
        f"• <b>Total Banned Premium Scammers:</b> {stats['total_bans']}\n"
        f"• <b>Connected Protected Chats:</b> {stats['total_chats']}\n"
        f"• <b>Authorized Admins Count:</b> {stats['total_users']}\n"
        f"• <b>Saved Channel Admins:</b> {stats.get('total_channel_admins', 0)}\n\n"
        f"<b>Admin Commands:</b>\n"
        f"• <code>/grant &lt;user_id&gt;</code> ── Grant bot access\n"
        f"• <code>/revoke &lt;user_id&gt;</code> ── Revoke bot access\n"
        f"• <code>/users</code> ── List authorized users\n"
        f"• <code>/scammers</code> ── View banned scammer logs"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("Users List", callback_data="admin_view_users", style="primary", emoji_name="crown"), make_btn("Scammer Logs", callback_data="admin_view_logs", style="primary", emoji_name="ninja")],
        [make_btn("Channel Admins", callback_data="admin_view_channel_admins", style="primary", emoji_name="zap"), make_btn("Emojis Manager", callback_data="cmd_emojis_manager", style="primary", emoji_name="sparkles")],
        [make_btn("Connected Chats", callback_data="cmd_connect_chats", style="primary", emoji_name="phone")],
        [make_btn("Main Menu", callback_data="cmd_back_main", style="danger", emoji_name="arrow_right")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.message(Command("grant"))
async def command_grant_user(message: Message):
    if message.from_user.id not in SUPER_ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) >= 2 and args[1].isdigit():
        target_id = int(args[1])
        await db.grant_user_access(user_id=target_id, granted_by=message.from_user.id, note="Granted via /grant")
        await message.answer(f"{get_p_emoji('check_box')} <b>ACCESS GRANTED!</b> User ID <code>{target_id}</code> is authorized.", parse_mode="HTML")

@admin_router.message(Command("revoke"))
async def command_revoke_user(message: Message):
    if message.from_user.id not in SUPER_ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) >= 2 and args[1].isdigit():
        target_id = int(args[1])
        if target_id not in SUPER_ADMIN_IDS:
            await db.revoke_user_access(target_id)
            await message.answer(f"{get_p_emoji('check_box')} <b>ACCESS REVOKED!</b> User ID <code>{target_id}</code> removed.", parse_mode="HTML")

@admin_router.message(Command("users"))
async def command_list_users(message: Message):
    if not await is_admin_user(message.from_user.id):
        return
    users = await db.get_all_authorized_users()
    text = f"{get_p_emoji('crown')} <b>AUTHORIZED USERS LIST ({len(users)})</b>\n\n"
    for u in users:
        is_main = " (SUPER ADMIN)" if u["user_id"] in SUPER_ADMIN_IDS else ""
        text += f"• <code>{u['user_id']}</code>{is_main}\n"
    await message.answer(text, parse_mode="HTML")

@admin_router.message(Command("scammers"))
async def command_scammer_logs(message: Message):
    if not await is_admin_user(message.from_user.id):
        return
    logs = await db.get_scammer_ban_logs(limit=25)
    if not logs:
        await message.answer("No scammer ban logs recorded yet.", parse_mode="HTML")
        return
    text = f"{get_p_emoji('ninja')} <b>RECENT BANNED PREMIUM SCAMMERS ({len(logs)})</b>\n\n"
    for log in logs:
        text += f"• <b>{log.get('first_name', 'Scammer')}</b> (@{log.get('username', 'none')}) in {log.get('chat_title', 'Chat')}\n"
    await message.answer(text, parse_mode="HTML")

@admin_router.callback_query(F.data == "cmd_admin_panel")
async def callback_admin_panel(callback: CallbackQuery):
    if not await is_admin_user(callback.from_user.id):
        await callback.answer("⛔ Admin Access Required!", show_alert=True)
        return
    stats = await db.get_stats()
    text = f"{get_p_emoji('crown')} <b>SUPER ADMIN CONTROL PANEL</b>\n\nBanned Scammers: {stats['total_bans']} | Chats: {stats['total_chats']} | Channel Admins: {stats.get('total_channel_admins', 0)}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("Users", callback_data="admin_view_users", style="primary", emoji_name="crown"), make_btn("Scammer Logs", callback_data="admin_view_logs", style="primary", emoji_name="ninja")],
        [make_btn("Channel Admins", callback_data="admin_view_channel_admins", style="primary", emoji_name="zap"), make_btn("Emojis Manager", callback_data="cmd_emojis_manager", style="primary", emoji_name="sparkles")],
        [make_btn("Main Menu", callback_data="cmd_back_main", style="danger", emoji_name="arrow_right")]
    ])
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(F.data == "admin_view_users")
async def callback_view_users(callback: CallbackQuery):
    if not await is_admin_user(callback.from_user.id):
        return
    users = await db.get_all_authorized_users()
    text = f"{get_p_emoji('crown')} <b>AUTHORIZED USERS ({len(users)})</b>\n\n"
    for u in users:
        text += f"• <code>{u['user_id']}</code>\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[make_btn("Back", callback_data="cmd_admin_panel", style="danger")]])
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(F.data == "admin_view_logs")
async def callback_view_logs(callback: CallbackQuery):
    if not await is_admin_user(callback.from_user.id):
        return
    logs = await db.get_scammer_ban_logs(limit=15)
    text = f"{get_p_emoji('ninja')} <b>BANNED SCAMMERS ({len(logs)})</b>\n\n"
    for log in logs:
        text += f"• {log.get('first_name', 'User')} (@{log.get('username', 'none')}) in {log.get('chat_title', '')[:15]}\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[make_btn("Back", callback_data="cmd_admin_panel", style="danger")]])
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

@admin_router.callback_query(F.data == "admin_view_channel_admins")
async def callback_view_channel_admins(callback: CallbackQuery):
    if not await is_admin_user(callback.from_user.id):
        await callback.answer("⛔ Access Denied!", show_alert=True)
        return
    admins = await db.get_channel_admins()
    if not admins:
        text = f"{get_p_emoji('check_box')} No channel administrators saved in MongoDB yet."
    else:
        text = f"{get_p_emoji('zap')} <b>SAVED CHANNEL ADMINS ({len(admins)})</b>\n\n"
        for adm in admins[:20]:
            uname = f"@{adm['username']}" if adm.get('username') else f"ID: {adm['user_id']}"
            title = adm.get('chat_title') or f"Chat {adm['chat_id']}"
            text += f"• <b>{adm.get('first_name', 'Admin')}</b> ({uname}) | Role: {adm.get('status', 'admin')}\n  Chat: {title[:20]}\n\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[make_btn("Back", callback_data="cmd_admin_panel", style="danger")]])
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

# ==================== EMOJI UPLOAD & MANAGEMENT ROUTER ====================
@emoji_router.callback_query(F.data == "cmd_emojis_manager")
async def callback_emojis_manager(callback: CallbackQuery):
    if not await is_admin_user(callback.from_user.id):
        await callback.answer("⛔ Access Denied!", show_alert=True)
        return

    custom_list = await db.get_all_custom_emojis()
    text = (
        f"{get_p_emoji('sparkles')} <b>PREMIUM EMOJIS & ID MANAGER</b> {get_p_emoji('sparkles')}\n\n"
        f"<b>Active Saved Premium Emojis ({len(custom_list)} custom + {len(PREMIUM_EMOJIS)} defaults):</b>\n\n"
    )

    if not custom_list:
        text += "<i>No custom emoji IDs uploaded to MongoDB yet.</i>\n"
    else:
        for item in custom_list[:15]:
            formatted = get_p_emoji(item['name'])
            text += f"• <b>{item['name']}</b>: <code>{item['emoji_id']}</code> ({formatted})\n"

    text += (
        "\n💡 <b>AUTOMATIC EMOJI ID EXTRACTION:</b>\n"
        "Simply send or forward ANY Telegram Premium Custom Emoji in this chat! The bot will automatically detect its 19-digit Emoji ID, extract it, and save it to your MongoDB database."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            make_btn("View Full List", callback_data="cmd_emojis_list", style="primary", emoji_name="eye"),
            make_btn("How to Add Emoji", callback_data="cmd_emojis_help", style="primary", emoji_name="gear")
        ],
        [
            make_btn("Back to Admin Panel", callback_data="cmd_admin_panel", style="danger", emoji_name="arrow_right")
        ]
    ])
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

@emoji_router.callback_query(F.data == "cmd_emojis_list")
async def callback_emojis_list_view(callback: CallbackQuery):
    if not await is_admin_user(callback.from_user.id):
        return
    custom_list = await db.get_all_custom_emojis()
    text = f"{get_p_emoji('sparkles')} <b>ALL SAVED CUSTOM EMOJI IDs ({len(custom_list)})</b>\n\n"
    if not custom_list:
        text += "<i>No custom emoji IDs uploaded yet. Send any custom premium emoji to auto-save!</i>"
    else:
        for item in custom_list:
            formatted = get_p_emoji(item['name'])
            text += f"• <b>{item['name']}</b>: <code>{item['emoji_id']}</code> ({formatted})\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[make_btn("Back", callback_data="cmd_emojis_manager", style="danger")]])
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

@emoji_router.callback_query(F.data == "cmd_emojis_help")
async def callback_emojis_help(callback: CallbackQuery):
    text = (
        f"{get_p_emoji('gear')} <b>EMOJI ID SAVER INSTRUCTIONS</b>\n\n"
        f"<b>Method 1: Automatic Extraction (Easiest)</b>\n"
        f"Just send or forward any Telegram Premium Custom Emoji in chat. The bot automatically fetches the Emoji ID and saves it to MongoDB!\n\n"
        f"<b>Method 2: Command Syntax</b>\n"
        f"Send: <code>/addemoji &lt;name&gt; &lt;emoji_id&gt; [char]</code>\n"
        f"Example: <code>/addemoji fire 6165899765042122114 🔥</code>\n\n"
        f"<b>Method 3: Direct ID Number</b>\n"
        f"Send any 19-digit Emoji ID directly into the chat."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[make_btn("Back", callback_data="cmd_emojis_manager", style="danger")]])
    await safe_edit_message(callback, text, reply_markup=kb)
    await callback.answer()

@emoji_router.message(Command("addemoji"))
@emoji_router.message(Command("saveemoji"))
async def command_add_emoji(message: Message):
    user_id = message.from_user.id
    if not await is_admin_user(user_id):
        await message.answer("⛔ Only authorized admins can manage custom premium emoji IDs.")
        return

    parts = message.text.split()
    if len(parts) < 3:
        usage_text = (
            f"{get_p_emoji('gear')} <b>UPLOAD & SAVE EMOJI ID USAGE</b>\n\n"
            f"<b>Syntax:</b> <code>/addemoji &lt;emoji_name&gt; &lt;emoji_id&gt; [char]</code>\n\n"
            f"<b>Examples:</b>\n"
            f"• <code>/addemoji fire 6165899765042122114 🔥</code>\n"
            f"• <code>/addemoji crown 6165733687246720248 👑</code>\n"
            f"• <code>/addemoji down 6158868637290601091 ⬇️</code>"
        )
        await message.answer(usage_text, parse_mode="HTML")
        return

    name = parts[1]
    emoji_id = parts[2]
    char = parts[3] if len(parts) > 3 else "✨"

    success = await db.save_custom_emoji(name, emoji_id, char=char, updated_by=user_id)
    if success:
        formatted_preview = get_p_emoji(name)
        await message.answer(
            f"{get_p_emoji('check_mark')} <b>Emoji ID Saved & Applied Successfully!</b>\n\n"
            f"• <b>Name:</b> <code>{name}</code>\n"
            f"• <b>Emoji ID:</b> <code>{emoji_id}</code>\n"
            f"• <b>Preview:</b> {formatted_preview}",
            parse_mode="HTML"
        )

@emoji_router.message(Command("emojis"))
async def command_list_emojis(message: Message):
    if not await is_admin_user(message.from_user.id):
        return
    custom_list = await db.get_all_custom_emojis()
    text = f"{get_p_emoji('sparkles')} <b>SAVED CUSTOM PREMIUM EMOJI IDs</b> {get_p_emoji('sparkles')}\n\n"
    if not custom_list:
        text += "<i>No custom emoji IDs uploaded to MongoDB yet. Use <code>/addemoji &lt;name&gt; &lt;id&gt;</code> to upload.</i>"
    else:
        for item in custom_list:
            formatted = get_p_emoji(item['name'])
            text += f"• <b>{item['name']}</b>: <code>{item['emoji_id']}</code> ({formatted})\n"

    text += "\n<i>All saved emoji IDs render automatically in bot buttons and messages.</i>"
    await message.answer(text, parse_mode="HTML")

@emoji_router.message(Command("delemoji"))
async def command_delete_emoji(message: Message):
    user_id = message.from_user.id
    if not await is_admin_user(user_id):
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: <code>/delemoji &lt;emoji_name&gt;</code>", parse_mode="HTML")
        return

    name = parts[1]
    res = await db.delete_custom_emoji(name)
    if res:
        await message.answer(f"✅ Emoji <code>{name}</code> deleted from database.", parse_mode="HTML")
    else:
        await message.answer(f"❌ Emoji <code>{name}</code> not found.", parse_mode="HTML")

# Auto detect custom premium emojis sent by authorized admin
@emoji_router.message()
async def auto_detect_and_save_custom_emoji(message: Message):
    if not message.from_user or not await is_admin_user(message.from_user.id):
        return

    # Skip standard slash commands
    if message.text and message.text.startswith("/"):
        return

    detected_emojis = []

    # Check for Telegram Custom Emoji entities
    if message.entities:
        for entity in message.entities:
            if entity.type == "custom_emoji" and hasattr(entity, "custom_emoji_id") and entity.custom_emoji_id:
                char = message.text[entity.offset : entity.offset + entity.length] if message.text else "✨"
                emoji_id = str(entity.custom_emoji_id)
                detected_emojis.append((char, emoji_id))

    # Check if user sent a raw 18-20 digit numeric Telegram Emoji ID
    if not detected_emojis and message.text and message.text.strip().isdigit() and len(message.text.strip()) >= 18:
        emoji_id = message.text.strip()
        detected_emojis.append(("✨", emoji_id))

    if detected_emojis:
        saved_previews = []
        for idx, (char, emoji_id) in enumerate(detected_emojis, start=1):
            name = f"custom_emoji_{emoji_id[-6:]}"
            await db.save_custom_emoji(name=name, emoji_id=emoji_id, char=char, updated_by=message.from_user.id)
            saved_previews.append(f"• <b>Emoji:</b> {char} <tg-emoji emoji-id=\"{emoji_id}\">{char}</tg-emoji> | <b>ID:</b> <code>{emoji_id}</code>")

        response_text = (
            f"{get_p_emoji('check_mark')} <b>AUTOMATIC EMOJI ID DETECTED & SAVED!</b>\n\n"
            + "\n".join(saved_previews)
            + f"\n\n<i>Successfully saved to MongoDB & activated for bot buttons & messages!</i>"
        )
        await message.answer(response_text, parse_mode="HTML")

# ==================== ADMIN COMMANDS ROUTER ====================
async def extract_target_user(message: Message, bot: Bot):
    target_user_id = None
    target_username = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.username or message.reply_to_message.from_user.first_name
    else:
        args = message.text.split()
        if len(args) >= 2:
            arg = args[1].strip()
            if arg.startswith("@"):
                target_username = arg[1:]
                try:
                    chat_member = await bot.get_chat_member(message.chat.id, arg)
                    target_user_id = chat_member.user.id
                except Exception:
                    pass
            elif arg.isdigit():
                target_user_id = int(arg)
                target_username = f"User_{target_user_id}"
    return target_user_id, target_username

@admin_cmd_router.message(Command("promote"))
@admin_cmd_router.message(Command("admin"))
async def command_promote_user(message: Message, bot: Bot):
    if not await is_admin_user(message.from_user.id):
        return
    if message.chat.type not in ["channel", "group", "supergroup"]:
        return

    target_id, target_uname = await extract_target_user(message, bot)
    if not target_id:
        await message.answer(f"{get_p_emoji('question')} <b>Usage:</b> Reply with <code>/promote</code> or <code>/promote @username</code>", parse_mode="HTML")
        return

    display_name = f"@{target_uname}" if target_uname else f"User <code>{target_id}</code>"
    try:
        await bot.promote_chat_member(
            chat_id=message.chat.id,
            user_id=target_id,
            can_post_messages=True,
            can_invite_users=True,
            can_edit_messages=False,
            can_delete_messages=False,
            can_change_info=False,
            can_pin_messages=False,
            can_manage_chat=False,
            can_promote_members=False
        )
        msg = f"{get_p_emoji('check_box')} <b>PROMOTED SUCCESSFULLY!</b> {display_name}\nGranted: Post Messages & Invite Link."
        await message.answer(msg, parse_mode="HTML")
        
        # Save admin into MongoDB
        await db.save_channel_admin(
            chat_id=message.chat.id,
            user_id=target_id,
            username=target_uname or "",
            first_name=target_uname or f"User_{target_id}",
            status="administrator",
            permissions={"can_post_messages": True, "can_invite_users": True},
            updated_by=message.from_user.id,
            chat_title=message.chat.title or "Channel/Group"
        )
    except TelegramAPIError as e:
        await message.answer(f"{get_p_emoji('sweat')} <b>PROMOTION FAILED!</b> Error: {e}", parse_mode="HTML")

@admin_cmd_router.message(Command("demote"))
async def command_demote_user(message: Message, bot: Bot):
    if not await is_admin_user(message.from_user.id):
        return
    target_id, target_uname = await extract_target_user(message, bot)
    if target_id:
        try:
            await bot.promote_chat_member(chat_id=message.chat.id, user_id=target_id, is_anonymous=False)
            await message.answer(f"{get_p_emoji('zap')} <b>DEMOTED!</b> Rights removed from @{target_uname or target_id}.", parse_mode="HTML")
            await db.remove_channel_admin(chat_id=message.chat.id, user_id=target_id)
        except Exception as e:
            await message.answer(f"Error demoting: {e}", parse_mode="HTML")

# ==================== PROTECTION ENGINE ROUTER ====================
@protection_router.chat_join_request()
async def handle_chat_join_request(event: ChatJoinRequest, bot: Bot):
    chat_id = event.chat.id
    user = event.from_user
    if not user:
        return

    is_premium = bool(user.is_premium)
    chat_settings = await db.get_chat_settings(chat_id)
    auto_accept_enabled = chat_settings.get("auto_accept", 0) if chat_settings else 0
    anti_scam_enabled = chat_settings.get("anti_scam_premium_ban", 1) if chat_settings else 1

    # Premium account scammer: Approve (to trigger ban) & Ban instantly
    if is_premium and anti_scam_enabled:
        try:
            await event.approve()
        except Exception:
            pass
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        except Exception:
            pass
        # Send warning DM to Scammer's Bot DM (NOT channel!)
        try:
            dm_text = f"{get_p_emoji('ninja')} <b>SECURITY BAN NOTICE</b>\n\n{DEFAULT_SCAMMER_DM}"
            await bot.send_message(user_id=user.id, text=dm_text, parse_mode="HTML")
        except Exception:
            pass
        await db.log_scammer_ban(user.id, user.username or "", user.first_name or "", chat_id, event.chat.title or "", "Premium Account Scammer Join Request Ban")
        return

    # Normal account: Approve ONLY if auto_accept is enabled by admin
    if auto_accept_enabled:
        try:
            await event.approve()
        except Exception:
            pass

@protection_router.chat_member()
async def handle_direct_member_join_or_dismiss(event: ChatMemberUpdated, bot: Bot):
    chat_id = event.chat.id
    new_status = event.new_chat_member.status
    old_status = event.old_chat_member.status
    user = event.new_chat_member.user or event.from_user
    if not user or user.is_bot:
        return

    # Check if someone dismissed or removed members
    if old_status in ["administrator", "member"] and new_status in ["kicked", "left", "restricted"]:
        actor = event.from_user
        if actor and actor.id != bot.id and actor.id != user.id:
            # SAFE PROMOTER EXEMPTION: If actor is an authorized promoter admin / Super Admin, DO NOT BAN!
            if await is_admin_user(actor.id):
                logger.info(f"🛡️ Promoter Admin {actor.id} performed member action in chat {chat_id}. Exempted from ban.")
                return

            # Rogue non-promoter admin acting without permission: Demote & Ban
            try:
                await bot.promote_chat_member(chat_id, actor.id, is_anonymous=False)
                await bot.ban_chat_member(chat_id, actor.id)
            except Exception:
                pass

            # Dispatch Safety Warning DM to Bot DM (NOT spamming channel!)
            safety_msg = (
                f"{get_p_emoji('sweat')} <b>ROGUE ADMIN SAFETY ALERT!</b> {get_p_emoji('sweat')}\n\n"
                f"• <b>Offender:</b> @{actor.username or actor.id}\n"
                f"• <b>Target Chat:</b> {event.chat.title or chat_id}\n"
                f"• <b>Reason:</b> Dismissing / Removing channel members!\n"
                f"• <b>Action Taken:</b> Demoted & Permanently Banned from chat!"
            )
            # Send DM warning to bot admins
            for super_id in SUPER_ADMIN_IDS:
                try:
                    await bot.send_message(super_id, safety_msg, parse_mode="HTML")
                except Exception:
                    pass

    # Direct Join check for Premium Scammer Accounts
    if new_status == "member" and old_status not in ["member", "administrator"] and bool(user.is_premium):
        chat_settings = await db.get_chat_settings(chat_id)
        if chat_settings and chat_settings.get("anti_scam_premium_ban", 1):
            try:
                await bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
                dm_text = f"{get_p_emoji('ninja')} <b>SECURITY BAN NOTICE</b>\n\n{DEFAULT_SCAMMER_DM}"
                await bot.send_message(user_id=user.id, text=dm_text, parse_mode="HTML")
                await db.log_scammer_ban(user.id, user.username or "", user.first_name or "", chat_id, event.chat.title or "", "Direct Premium Join Ban")
            except Exception:
                pass

@protection_router.message()
async def monitor_messages_anti_spam_and_abuse(message: Message, bot: Bot):
    if not message.from_user or message.from_user.is_bot:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    
    # Exclude authorized promoter admins / Super Admins from anti-abuse monitoring
    if await is_admin_user(user_id):
        return

    uname = f"@{message.from_user.username}" if message.from_user.username else f"User <code>{user_id}</code>"
    now = time.time()

    key = (chat_id, user_id)
    user_msg_tracker[key].append(now)
    user_msg_tracker[key] = [t for t in user_msg_tracker[key] if now - t <= 5.0]

    if len(user_msg_tracker[key]) >= 10:
        user_msg_tracker[key].clear()
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await bot.promote_chat_member(chat_id, user_id, is_anonymous=False)
        except Exception:
            pass
        try:
            await bot.ban_chat_member(chat_id, user_id)
        except Exception:
            pass

        spam_alert = f"🚨 <b>ANTI-SPAM TRIGGERED!</b> Offender {uname} posted 10+ messages in 5s in {message.chat.title}. Demoted & Banned!"
        for super_id in SUPER_ADMIN_IDS:
            try:
                await bot.send_message(super_id, spam_alert, parse_mode="HTML")
            except Exception:
                pass
        return

    if message.text:
        text_raw = message.text.lower()
        if any(word in text_raw for word in ABUSE_WORDS):
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await bot.promote_chat_member(chat_id, user_id, is_anonymous=False)
            except Exception:
                pass
            try:
                await bot.ban_chat_member(chat_id, user_id)
            except Exception:
                pass

            abuse_alert = f"🚫 <b>ANTI-ABUSE TRIGGERED!</b> Abusive message from {uname} in {message.chat.title}. Message deleted & User Banned!"
            for super_id in SUPER_ADMIN_IDS:
                try:
                    await bot.send_message(super_id, abuse_alert, parse_mode="HTML")
                except Exception:
                    pass

# ==================== MAIN APPLICATION ENTRY ====================
async def main():
    logger.info("🚀 Starting Auto Protection & Anti-Scammer Bot...")
    
    # Initialize MongoDB Connection
    await db.init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    dp = Dispatcher()

    # Outer Middleware for tracking user activity
    dp.message.outer_middleware(UserActivityMiddleware())

    # Include all protection bot routers
    dp.include_router(guard_router)
    dp.include_router(start_router)
    dp.include_router(admin_router)
    dp.include_router(admin_cmd_router)
    dp.include_router(emoji_router)
    dp.include_router(protection_router)

    bot_info = await bot.get_me()
    logger.info(f"🤖 Protection Bot Username: @{bot_info.username} [ID: {bot_info.id}]")
    logger.info(f"👑 Main Super Admin ID: {SUPER_ADMIN_ID}")

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🛡️ Protection Bot is ONLINE with MongoDB & actively guarding channels/groups!")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("👋 Bot stopped cleanly.")
