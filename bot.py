import os
import asyncio
import logging
import tempfile
import uuid
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
import yt_dlp
from health_check import start_health_server
from premium_check import (
    get_user,
    is_new_user,
    register_user,
    has_credits,
    add_credits,
    get_credits_expiry,
    get_refer_link,
    process_refer,
    create_deep_link,
    process_deep_link,
    get_all_user_ids,
)

# ================== LOAD ENV ==================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID"))
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL")
FB_EMAIL = os.getenv("FB_EMAIL")
FB_PASSWORD = os.getenv("FB_PASSWORD")
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
COOKIES_FILE = "cookies.txt"
IG_COOKIES_FILE = "ig_cookies.txt"
FB_IMAGE = int(os.getenv("FB_IMAGE", 0))
IG_IMAGE = int(os.getenv("IG_IMAGE", 0))
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
BOT_USERNAME = os.getenv("BOT_USERNAME", "")

MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# ================== LOGGING ==================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)


# ================== URL TYPE CHECKER ==================
def get_url_type(url):
    if "facebook.com" in url or "fb.watch" in url:
        if "facebook.com/login" in url:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if "share_url" in params:
                url = unquote(params["share_url"][0])
            elif "next" in params:
                url = unquote(params["next"][0])

        if "/share/r/" in url:
            return "facebook", "reel"
        if "/share/v/" in url:
            return "facebook", "video"
        if "/share/p/" in url:
            return "facebook", "photo"
        if "fb.watch" in url:
            return "facebook", "reel"
        return "facebook", "other"

    if "instagram.com" in url or "instagr.am" in url:
        if "/reel/" in url or "/reels/" in url:
            return "instagram", "reel"
        if "/p/" in url:
            return "instagram", "post"
        if "/tv/" in url:
            return "instagram", "video"
        if "/stories/" in url:
            return "instagram", "story"
        return "instagram", "other"

    return "unknown", "invalid"


def clean_facebook_url(url):
    if "facebook.com/login" in url:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if "share_url" in params:
            return unquote(params["share_url"][0])
        if "next" in params:
            return unquote(params["next"][0])
    return url


def format_size(size_bytes):
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes} B"


def format_expiry(expiry: datetime):
    now = datetime.utcnow()
    diff = expiry - now
    hours = int(diff.total_seconds() // 3600)
    minutes = int((diff.total_seconds() % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ================== DOWNLOAD ==================
def download_video(url, platform="facebook"):
    temp_dir = tempfile.mkdtemp()
    unique_name = str(uuid.uuid4())

    ydl_opts = {
        "outtmpl": f"{temp_dir}/{unique_name}.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "format": "best",
        "merge_output_format": "mp4",
    }

    if platform == "facebook":
        if FB_EMAIL and FB_PASSWORD:
            ydl_opts["username"] = FB_EMAIL
            ydl_opts["password"] = FB_PASSWORD
        elif os.path.exists(COOKIES_FILE):
            ydl_opts["cookiefile"] = COOKIES_FILE

    elif platform == "instagram":
        if os.path.exists(IG_COOKIES_FILE):
            ydl_opts["cookiefile"] = IG_COOKIES_FILE
        elif IG_USERNAME and IG_PASSWORD:
            ydl_opts["username"] = IG_USERNAME
            ydl_opts["password"] = IG_PASSWORD

    direct_url = ""

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        direct_url = info.get("url", "")
        if not direct_url and "formats" in info:
            formats = info.get("formats", [])
            if formats:
                direct_url = formats[-1].get("url", "")

        if "requested_downloads" in info and info["requested_downloads"]:
            file_path = info["requested_downloads"][0]["filepath"]
        else:
            file_path = ydl.prepare_filename(info)

    return file_path, direct_url


# ================== FORCE JOIN ==================
async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        member = await context.bot.get_chat_member(FORCE_CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False


async def send_force_join(update: Update):
    user = update.effective_user
    name = user.first_name

    text = (
        f"👋 Hello {name}, welcome!\n\n"
        f"🚫 To use this bot, you must join our update channel.\n\n"
        f"👉 After joining, click the button below to continue."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{FORCE_CHANNEL.replace('@', '')}")],
        [InlineKeyboardButton("✅ Joined", callback_data="check_join")]
    ])

    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=keyboard)


# ================== NO CREDITS MESSAGE ==================
async def send_no_credits(update: Update, name: str):
    await update.message.reply_text(
        f"❌ <b>You have no credits!</b>\n\n"
        f"To download videos, you need credits.\n\n"
        f"🎁 <b>Earn credits for free:</b>\n"
        f"1. /refer — Share your refer link\n"
        f"   When a new user joins, you get <b>12 hours</b> free!\n\n"
        f"2. /earn — Get a time-limited link\n"
        f"   Share it, when someone uses it you get <b>12 hours</b> free!",
        parse_mode="HTML"
    )


# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_join(update, context):
        await send_force_join(update)
        return

    user = update.effective_user
    user_id = user.id
    name = user.first_name
    username = user.username or "N/A"

    args = context.args
    start_param = args[0] if args else None

    new_user = await is_new_user(user_id)

    # ================== REFER LINK PROCESS ==================
    if start_param and start_param.startswith("ref_") and new_user:
        refer_code = start_param[4:]
        await register_user(user_id, name, username)
        result = await process_refer(refer_code, user_id)
        if result:
            referrer_id, expiry = result
            # Referrer ko notify karo
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=(
                        f"🎉 <b>You earned credits!</b>\n\n"
                        f"Someone joined using your refer link.\n"
                        f"✅ <b>12 hours</b> of free downloads added!\n"
                        f"⏰ <b>Expires:</b> {format_expiry(expiry)}"
                    ),
                    parse_mode="HTML"
                )
            except:
                pass

    # ================== DEEP LINK PROCESS ==================
    elif start_param and start_param.startswith("dl_"):
        await register_user(user_id, name, username)
        result = await process_deep_link(start_param, user_id)

        if result == "expired":
            # Naya link do
            new_link, new_expiry = await create_deep_link(user_id)
            await update.message.reply_text(
                f"⏰ <b>Link expired!</b>\n\n"
                f"This link has expired. Here is your new link:\n\n"
                f"🔗 <code>{new_link}</code>\n\n"
                f"⚠️ This link expires in <b>5 minutes</b>.",
                parse_mode="HTML"
            )
            return

        elif result == "used":
            await update.message.reply_text(
                "❌ This link has already been used.",
                parse_mode="HTML"
            )
            return

        elif result == "self":
            await update.message.reply_text(
                "❌ You cannot use your own link.",
                parse_mode="HTML"
            )
            return

        elif result == "invalid":
            pass  # Normal start karo

        elif isinstance(result, tuple) and result[0] == "success":
            _, creator_id, new_expiry = result
            # Creator ko notify karo
            try:
                await context.bot.send_message(
                    chat_id=creator_id,
                    text=(
                        f"🎉 <b>Someone used your earn link!</b>\n\n"
                        f"✅ <b>12 hours</b> of free downloads added!\n"
                        f"⏰ <b>Expires in:</b> {format_expiry(new_expiry)}"
                    ),
                    parse_mode="HTML"
                )
            except:
                pass

    else:
        # Normal start
        await register_user(user_id, name, username)

    # ================== WELCOME MESSAGE ==================
    await update.message.reply_text(
        f"👋 Hello {name}!\n"
        f"🤖 Welcome to Facebook, Insta Downloader Bot\n\n"
        f"📌 How to use:\n"
        f"1. Copy a Facebook, Insta video or reel link\n"
        f"2. Send it here\n"
        f"3. Receive your file instantly\n\n"
        f"🌐 Supported:\n"
        f"facebook.com, instagram.com\n\n"
        f"⚡ Features:\n"
        f"- High quality downloads\n"
        f"- Fast processing\n"
        f"- No ads"
    )


# ================== REFER COMMAND ==================
async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_join(update, context):
        await send_force_join(update)
        return

    user = update.effective_user
    user_id = user.id

    await register_user(user_id, user.first_name, user.username or "N/A")
    result = await get_refer_link(user_id)
    if not result:
        await update.message.reply_text("❌ Error generating refer link. Please try again.")
        return

    link, code = result

    await update.message.reply_text(
        f"🔗 <b>Your Refer Link:</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"📢 Share this link with your friends.\n"
        f"When a <b>new user</b> starts the bot using your link,\n"
        f"you get <b>12 hours</b> of free downloads! 🎉\n\n"
        f"⚠️ Only new users count (users who haven't used the bot before).",
        parse_mode="HTML"
    )


# ================== EARN COMMAND ==================
async def earn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_join(update, context):
        await send_force_join(update)
        return

    user = update.effective_user
    user_id = user.id

    await register_user(user_id, user.first_name, user.username or "N/A")
    link, expires_at = await create_deep_link(user_id)

    await update.message.reply_text(
        f"⚡ <b>Your Earn Link:</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"📢 Share this link. When someone starts the bot using it,\n"
        f"you get <b>12 hours</b> of free downloads! 🎉\n\n"
        f"⏰ <b>This link expires in 5 minutes!</b>\n"
        f"Use /earn again to get a new link.",
        parse_mode="HTML"
    )


# ================== CREDITS COMMAND ==================
async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_join(update, context):
        await send_force_join(update)
        return

    user = update.effective_user
    user_id = user.id
    name = user.first_name

    expiry = await get_credits_expiry(user_id)

    if expiry:
        await update.message.reply_text(
            f"✅ <b>You have active credits!</b>\n\n"
            f"⏰ <b>Expires in:</b> {format_expiry(expiry)}\n\n"
            f"You can download unlimited videos until your credits expire.",
            parse_mode="HTML"
        )
    else:
        await send_no_credits(update, name)


# ================== BROADCAST COMMAND ==================
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return

    # Reply to message se broadcast karo
    if update.message.reply_to_message:
        broadcast_msg = update.message.reply_to_message
        user_ids = await get_all_user_ids()
        total = len(user_ids)
        success = 0
        failed = 0

        status_msg = await update.message.reply_text(
            f"📢 Broadcasting to {total} users..."
        )

        for uid in user_ids:
            try:
                await broadcast_msg.copy(chat_id=uid)
                success += 1
            except:
                failed += 1
            await asyncio.sleep(0.05)  # Flood wait se bachne ke liye

        await status_msg.edit_text(
            f"✅ <b>Broadcast Complete!</b>\n\n"
            f"👥 Total: {total}\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}",
            parse_mode="HTML"
        )
        return

    # Text ke sath broadcast karo /broadcast message
    text = " ".join(context.args) if context.args else None
    if not text:
        await update.message.reply_text(
            "📢 <b>Broadcast Usage:</b>\n\n"
            "1. Reply to any message with /broadcast\n"
            "2. Or: /broadcast Your message here",
            parse_mode="HTML"
        )
        return

    user_ids = await get_all_user_ids()
    total = len(user_ids)
    success = 0
    failed = 0

    status_msg = await update.message.reply_text(
        f"📢 Broadcasting to {total} users..."
    )

    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=text
            )
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"👥 Total: {total}\n"
        f"✅ Success: {success}\n"
        f"❌ Failed: {failed}",
        parse_mode="HTML"
    )


# ================== HANDLE MESSAGE ==================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_join(update, context):
        await send_force_join(update)
        return

    user = update.effective_user
    user_id = user.id
    name = user.first_name

    # User register karo agar nahi hai
    await register_user(user_id, name, user.username or "N/A")

    # Credits check karo
    if not await has_credits(user_id):
        await send_no_credits(update, name)
        return

    raw_url = update.message.text.strip()
    platform, url_type = get_url_type(raw_url)

    if url_type == "invalid":
        await update.message.reply_text(
            f"Hello {name}! Please send a valid Facebook or Instagram link."
        )
        return

    if platform == "facebook" and url_type in ("photo", "other"):
        await update.message.reply_text(
            f"Hello {name}! This bot only downloads Reels and Videos.\n\n"
