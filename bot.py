import os
import asyncio
import logging
import tempfile
import uuid
from urllib.parse import urlparse, parse_qs, unquote
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

MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# ================== USER MEMORY ==================
users_db = set()

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
    """
    Returns: (platform, type)
    platform: "facebook", "instagram"
    type: "reel", "video", "post", "photo", "story", "other", "invalid"
    """

    # ---- FACEBOOK ----
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

    # ---- INSTAGRAM ----
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


# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_join(update, context):
        await send_force_join(update)
        return

    user = update.effective_user
    name = user.first_name

    if user.id in users_db:
        await update.message.reply_text(
            f"👋 Welcome back {name}!\n\n"
            f"📥 Send a Facebook or Instagram Reel/Video link.\n\n"
            f"📘 Facebook:\n"
            f"✅ Reels\n"
            f"✅ Videos\n"
            f"❌ Stories\n"
            f"❌ Pictures\n\n"
            f"📸 Instagram:\n"
            f"✅ Reels\n"
            f"✅ Videos\n"
            f"❌ Stories\n"
            f"❌ Pictures"
        )
    else:
        users_db.add(user.id)

        await update.message.reply_text(
            f"👋 Hello {name}!\n\n"
            f"🤖 Welcome to Reel Downloader Bot\n\n"
            f"📌 How to use:\n"
            f"1. Open a Facebook or Instagram Reel/Video\n"
            f"2. Tap Share → Copy Link\n"
            f"3. Paste the link here\n"
            f"4. Your video will be sent instantly!\n\n"
            f"📘 Facebook:\n"
            f"✅ Reels\n"
            f"✅ Videos\n"
            f"❌ Stories\n"
            f"❌ Pictures\n\n"
            f"📸 Instagram:\n"
            f"✅ Reels\n"
            f"✅ Videos\n"
            f"❌ Stories\n"
            f"❌ Pictures\n\n"
            f"⚡ Features:\n"
            f"- Fast downloads\n"
            f"- High quality\n"
            f"- No ads"
        )


# ================== HANDLE MESSAGE ==================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_join(update, context):
        await send_force_join(update)
        return

    raw_url = update.message.text.strip()
    platform, url_type = get_url_type(raw_url)
    name = update.effective_user.first_name

    # Invalid link
    if url_type == "invalid":
        await update.message.reply_text(
            f"Hello {name}! Please send a valid Facebook or Instagram link."
        )
        return

    # Facebook unsupported
    if platform == "facebook" and url_type in ("photo", "other"):
        await update.message.reply_text(
            f"Hello {name}! This bot only downloads Reels and Videos.\n\n"
            f"✅ Reels\n"
            f"✅ Videos\n"
            f"❌ Stories\n"
            f"❌ Pictures"
        )
        return

    # Instagram unsupported
    if platform == "instagram" and url_type in ("story", "other"):
        await update.message.reply_text(
            f"Hello {name}! This bot only downloads Reels and Videos.\n\n"
            f"✅ Reels\n"
            f"✅ Videos\n"
            f"❌ Stories\n"
            f"❌ Pictures"
        )
        return

    # Clean URL
    if platform == "facebook":
        url = clean_facebook_url(raw_url)
    else:
        url = raw_url

    msg = await update.message.reply_text("⏳ Processing...")

    user = update.effective_user
    name = user.first_name or "N/A"
    username = f"@{user.username}" if user.username else "N/A"
    user_id = user.id

    platform_label = "📘 Facebook" if platform == "facebook" else "📸 Instagram"
    large_file_image = FB_IMAGE if platform == "facebook" else IG_IMAGE

    link_msg_id = None

    try:
        # Step 1: User ka link storage channel me copy karo
        copied = await context.bot.copy_message(
            chat_id=STORAGE_CHANNEL_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
        link_msg_id = copied.message_id

        # Step 2: User info reply karke bhejo
        info_text = (
            f"{platform_label}\n\n"
            f"👤 <b>Name</b>: {name}\n"
            f"🔗 <b>Username</b>: {username}\n"
            f"🆔 <b>User ID</b>: <code>{user_id}</code>"
        )
        await context.bot.send_message(
            chat_id=STORAGE_CHANNEL_ID,
            text=info_text,
            parse_mode="HTML",
            reply_to_message_id=link_msg_id
        )

        # Step 3: Download karo
        await msg.edit_text("📥 Downloading...")
        file_path, direct_url = download_video(url, platform=platform)

        # Step 4: Actual file size check karo
        actual_size = os.path.getsize(file_path)
        size_readable = format_size(actual_size)
        logging.info(f"[{platform}] File size: {size_readable}")

        # Step 5: 50MB se bada?
        if actual_size > MAX_SIZE_BYTES:
            os.remove(file_path)

            caption = (
                f"📦 <b>File Size: {size_readable}</b>\n\n"
                f"Please use the button below to download it directly ✅"
            )

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬇️ Download Video", url=direct_url)]
            ])

            # Log channel me note karo
            await context.bot.send_message(
                chat_id=STORAGE_CHANNEL_ID,
                text=(
                    f"⚠️ <b>Large File — Direct Link Sent</b>\n\n"
                    f"📦 <b>Size</b>: {size_readable}\n"
                    f"👤 <b>User</b>: {name}\n"
                    f"🆔 <b>ID</b>: <code>{user_id}</code>"
                ),
                parse_mode="HTML",
                reply_to_message_id=link_msg_id
            )

            # User ko image + caption + button bhejo
            if large_file_image:
                await msg.delete()
                await context.bot.copy_message(
                    chat_id=update.effective_chat.id,
                    from_chat_id=STORAGE_CHANNEL_ID,
                    message_id=large_file_image,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            else:
                await msg.edit_text(
                    caption,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            return

        # Step 6: Upload karo
        await msg.edit_text("📤 Uploading...")

        sent_msg = await context.bot.send_video(
            chat_id=STORAGE_CHANNEL_ID,
            video=open(file_path, "rb"),
            reply_to_message_id=link_msg_id,
            supports_streaming=True
        )

        # Step 7: User ko forward karo
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=STORAGE_CHANNEL_ID,
            message_id=sent_msg.message_id
        )

        await msg.delete()
        os.remove(file_path)

    except Exception as e:
        error_str = str(e)
        logging.error(f"[{platform}] Error: {error_str}")

        if link_msg_id:
            await context.bot.send_message(
                chat_id=STORAGE_CHANNEL_ID,
                text=(
                    f"❌ <b>Download Failed</b> {platform_label}\n\n"
                    f"👤 <b>User</b>: {name}\n"
                    f"🆔 <b>ID</b>: <code>{user_id}</code>\n\n"
                    f"⚠️ <b>Error</b>:\n<code>{error_str[:500]}</code>"
                ),
                parse_mode="HTML",
                reply_to_message_id=link_msg_id
            )

        await msg.edit_text(
            "❌ <b>Download failed!</b>\n\n"
            "Possible reasons:\n"
            "• Post is private or restricted\n"
            "• Link has expired\n"
            "• Temporarily unavailable\n\n"
            "Please try again later. 🙏",
            parse_mode="HTML"
        )


# ================== BUTTON ==================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if await check_join(update, context):
        await query.message.edit_text("✅ You can now use the bot. Send a Facebook or Instagram Reel link.")
    else:
        await send_force_join(update)


# ================== MAIN ==================
def main():
    async def run():
        # Health check server start karo
        await start_health_server()

        app = ApplicationBuilder().token(BOT_TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(CallbackQueryHandler(button_handler))

        print("🤖 Bot is running...")

        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        # Bot ko hamesha chalu rakho
        await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    main()
