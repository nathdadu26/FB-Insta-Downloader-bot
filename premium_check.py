import os
import random
import string
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
BOT_USERNAME = os.getenv("BOT_USERNAME")

client = AsyncIOMotorClient(MONGO_URI)
db = client["reelbot"]
users_col = db["users"]
deeplinks_col = db["deeplinks"]


def generate_code(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


# ================== USER ==================
async def get_user(user_id):
    return await users_col.find_one({"user_id": user_id})


async def is_new_user(user_id):
    return await get_user(user_id) is None


async def register_user(user_id, first_name, username):
    if await is_new_user(user_id):
        refer_code = generate_code(8)
        await users_col.insert_one({
            "user_id": user_id,
            "first_name": first_name or "N/A",
            "username": username or "N/A",
            "credits_expiry": None,
            "refer_code": refer_code,
            "joined_at": datetime.utcnow()
        })
        return True  # naya user
    return False  # purana user


async def get_all_user_ids():
    cursor = users_col.find({}, {"user_id": 1})
    return [doc["user_id"] async for doc in cursor]


# ================== CREDITS ==================
async def has_credits(user_id):
    user = await get_user(user_id)
    if not user:
        return False
    expiry = user.get("credits_expiry")
    if expiry and expiry > datetime.utcnow():
        return True
    return False


async def add_credits(user_id, hours=12):
    now = datetime.utcnow()
    user = await get_user(user_id)
    if not user:
        return None
    current_expiry = user.get("credits_expiry")
    if current_expiry and current_expiry > now:
        new_expiry = current_expiry + timedelta(hours=hours)
    else:
        new_expiry = now + timedelta(hours=hours)
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"credits_expiry": new_expiry}}
    )
    return new_expiry


async def get_credits_expiry(user_id):
    user = await get_user(user_id)
    if not user:
        return None
    expiry = user.get("credits_expiry")
    if expiry and expiry > datetime.utcnow():
        return expiry
    return None


# ================== REFER ==================
async def get_refer_link(user_id):
    user = await get_user(user_id)
    if not user:
        return None
    refer_code = user.get("refer_code")
    if not refer_code:
        refer_code = generate_code(8)
        await users_col.update_one(
            {"user_id": user_id},
            {"$set": {"refer_code": refer_code}}
        )
    return f"https://t.me/{BOT_USERNAME}?start=ref_{refer_code}", refer_code


async def process_refer(refer_code, new_user_id):
    """
    Refer code process karo.
    Returns: (referrer_user_id, new_expiry) ya None
    """
    referrer = await users_col.find_one({"refer_code": refer_code})
    if not referrer:
        return None
    if referrer["user_id"] == new_user_id:
        return None  # self refer nahi
    new_expiry = await add_credits(referrer["user_id"], hours=12)
    return referrer["user_id"], new_expiry


# ================== DEEP LINK ==================
async def create_deep_link(user_id):
    """5 minute expiry wala deep link banao."""
    code = "dl_" + generate_code(6)
    expires_at = datetime.utcnow() + timedelta(minutes=5)
    await deeplinks_col.insert_one({
        "code": code,
        "created_by": user_id,
        "created_at": datetime.utcnow(),
        "expires_at": expires_at,
        "used": False
    })
    link = f"https://t.me/{BOT_USERNAME}?start={code}"
    return link, expires_at


async def process_deep_link(code, clicker_user_id):
    """
    Deep link process karo.
    Returns: "invalid" | "used" | "expired" | ("success", creator_user_id, new_expiry)
    """
    link = await deeplinks_col.find_one({"code": code})
    if not link:
        return "invalid"
    if link["used"]:
        return "used"
    if link["expires_at"] < datetime.utcnow():
        return "expired"
    if link["created_by"] == clicker_user_id:
        return "self"  # khud ka link khud use nahi kar sakta
    # Mark as used
    await deeplinks_col.update_one(
        {"code": code},
        {"$set": {"used": True}}
    )
    # Credits do link banane wale ko
    new_expiry = await add_credits(link["created_by"], hours=12)
    return "success", link["created_by"], new_expiry
