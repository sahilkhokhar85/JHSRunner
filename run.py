"""
JioHotstar Episode Notification Bot
Monitors SOURCE_CHANNEL for new episode text posts, parses show/episode info,
downloads the HD poster, and forwards a formatted caption + poster to TARGET_CHANNEL.
"""

import asyncio
import html
import logging
import os
import re
from io import BytesIO
from datetime import datetime

import aiohttp
from telethon.sessions import StringSession
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
STRING_SESSION = os.environ["STRING_SESSION"]

SOURCE_CHANNEL = -1002044817442
TARGET_CHANNEL = -1003570870636

client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH
)

SHOWS = {
    "anupama": "Anupama",
    "yeh-fitoor-tera": "Yeh Fitoor Tera",
    "yeh-rishta-kya-kehlata-hai": "Yeh Rishta Kya Kehlata Hai",
    "dr-aarambhi": "Dr. Aarambhi",
    "kyunki-saas-bhi-kabhi-bahu-thi": "Kyunki Saas Bhi Kabhi Bahu Thi",
    "kyunki-rishton-ke-bhi-roop-badalte-hain": "Kyunki Rishton Ke Bhi Roop Badalte Hain",
    "udne-ki-aasha": "Udne Ki Aasha",
    "juhi-mui": "Juhi Mui",
    "tuu-juliet-jatt-di": "Tuu Juliet Jatt Di",
    "bareilly-ke-bacchan": "Bareilly Ke Bacchan",
    "mannat-har-khushi-paane-ki": "Mannat - Har Khushi Paane Ki",
    "mr-and-mrs-parshuram": "Mr. And Mrs. Parshuram",
    "sairaab": "Sairaab",
    "oh-humnava-tum-dena-saath-mera": "Oh Humnava - Tum Dena Saath Mera",
    "mahadev-and-sons": "Mahadev & Sons",
    "do-duniya-ek-dil": "Do Duniya, Ek Dil",
    "seher-hone-ko-hai": "Seher Hone Ko Hai",
    "laughter-chefs-unlimited-entertainment": "Laughter Chefs Unlimited Entertainment",
    "khatron-ke-khiladi": "Khatron Ke Khiladi",
    "bigg-boss": "Bigg Boss",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("jiohotstar_bot")
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

URL_PATTERN = re.compile(r"https://(?:www\.)?hotstar\.com/in/shows/[^\s]+")
SEASON_EP_PATTERN = re.compile(r"S(\d+)\s*E(\d+)", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\b(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\b")
POSTER_PATTERN = re.compile(r"https://img\d+\.hotstar\.com/image/upload/[^\s]+")


def clean_show_url(url):
    match = re.match(
        r"(https://(?:www\.)?hotstar\.com/in/shows/.*/watch)(?:[^\s]*)",
        url
    )
    return match.group(1) if match else url

# ---------------------------------------------------------------------------
# In-memory duplicate protection
# ---------------------------------------------------------------------------

processed_message_ids = set()

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def extract_show_slug(url: str) -> str:
    """Extract the show slug from a Hotstar URL."""
    match = re.search(r"/shows/([^/]+)/", url + "/")
    return match.group(1) if match else ""


def get_show_name(url: str) -> str:
    """Map a Hotstar URL to a human-readable show name."""
    slug = extract_show_slug(url)
    return SHOWS.get(slug, "Unknown Show")


def format_release_date(raw_date: str) -> str:
    """Convert '11 Jul 2026' -> '11 July 2026'."""
    dt = datetime.strptime(raw_date.strip(), "%d %b %Y")
    return dt.strftime("%d %B %Y")


def build_hd_poster_url(poster_url: str) -> str:
    """Convert a low-res Hotstar poster URL into its HD .jpg equivalent."""
    hd_url = poster_url.replace("f_auto,h_124/", "")
    if not hd_url.endswith(".jpg"):
        hd_url += ".jpg"
    return hd_url


def extract_title(lines: list, url_index: int) -> str:
    """Find the episode title line, which follows the Hotstar URL line."""
    for line in lines[url_index + 1:]:
        stripped = line.strip()
        if stripped and not SEASON_EP_PATTERN.search(stripped):
            return stripped
    return ""


def parse_message(text: str):
    """
    Parse a source message and return a dict of extracted fields,
    or None if the message does not contain a valid Hotstar URL.
    """
    url_match = URL_PATTERN.search(text)
    if not url_match:
        return None

    hotstar_url = url_match.group(0)
    hotstar_url = clean_show_url(hotstar_url)
    lines = text.splitlines()

    url_line_index = next(
        (i for i, line in enumerate(lines) if hotstar_url in line), -1
    )
    if url_line_index == -1:
        return None

    title = extract_title(lines, url_line_index)

    season_ep_match = SEASON_EP_PATTERN.search(text)
    if not season_ep_match:
        return None
    season_number = season_ep_match.group(1).zfill(2)
    episode_number = season_ep_match.group(2).zfill(2)

    date_match = DATE_PATTERN.search(text)
    if not date_match:
        return None
    try:
        release_date = format_release_date(date_match.group(1))
    except ValueError:
        return None

    poster_match = POSTER_PATTERN.search(text)
    if not poster_match:
        return None
    poster_url = poster_match.group(0)

    return {
        "hotstar_url": hotstar_url,
        "title": title,
        "season_number": season_number,
        "episode_number": episode_number,
        "release_date": release_date,
        "poster_url": poster_url,
    }


# ---------------------------------------------------------------------------
# Caption builder
# ---------------------------------------------------------------------------


def build_caption(show_name: str, data: dict) -> str:
    """Build the final HTML-formatted caption."""
    safe_show_name = html.escape(show_name)
    safe_title = html.escape(data["title"].title())
    safe_url = html.escape(data["hotstar_url"])

    caption = (
        "<b>#NewEpisode #JioHotstar #Notification\n\n"
        f"Show Name: {safe_show_name}\n"
        f"Season Number: {data['season_number']}\n"
        f"Episode Number: {data['episode_number']}\n"
        f"Episode Title: {safe_title}\n"
        "Language: Hindi\n"
        f"Release Date: {data['release_date']}\n\n"
        "Show Link:</b>\n"
        f"<code>{safe_url}</code>"
    )
    return caption


# ---------------------------------------------------------------------------
# Poster download
# ---------------------------------------------------------------------------


async def download_poster(url: str) -> bytes:
    """Download an image from url and return raw bytes."""
    async with http_session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
        if response.status != 200:
            raise IOError(f"Bad status code {response.status} for {url}")
        return await response.read()


async def download_poster_with_fallback(data: dict) -> bytes:
    """Try downloading the HD poster; fall back to the original poster URL."""
    hd_url = build_hd_poster_url(data["poster_url"])
    try:
        content = await download_poster(hd_url)
        logger.info("Poster downloaded (HD): %s", hd_url)
        return content
    except Exception as hd_error:
        logger.warning("HD poster download failed (%s): %s", hd_url, hd_error)

    try:
        content = await download_poster(data["poster_url"])
        logger.info("Poster downloaded (original): %s", data["poster_url"])
        return content
    except Exception as fallback_error:
        logger.error("Original poster download also failed (%s): %s", data["poster_url"], fallback_error)
        raise


# ---------------------------------------------------------------------------
# Telethon client
# ---------------------------------------------------------------------------

http_session: aiohttp.ClientSession = None


@client.on(events.NewMessage(chats=SOURCE_CHANNEL))
async def handle_new_message(event):
    """Handle new messages in the source channel."""
    message = event.message

    # Ignore edited messages (NewMessage only fires for genuinely new ones,
    # but double-check defensively in case Telethon behavior changes).
    if message.edit_date is not None:
        return

    message_id = message.id

    # Duplicate protection
    if message_id in processed_message_ids:
        logger.info("Duplicate message %s skipped.", message_id)
        return
    processed_message_ids.add(message_id)

    text = message.raw_text or ""
    logger.info("Message received: %s", message_id)

    data = parse_message(text)
    if data is None:
        logger.info("Message %s ignored: no valid Hotstar URL/data found.", message_id)
        return

    show_name = get_show_name(data["hotstar_url"])
    logger.info("Show detected: %s", show_name)

    try:
        poster_bytes = await download_poster_with_fallback(data)

        caption = build_caption(show_name, data)

        poster_file = BytesIO(poster_bytes)
        poster_file.name = "poster.jpg"

        await send_with_retry(poster_file, caption)
        logger.info("Upload successful for message %s.", message_id)

    except FloodWaitError as flood_error:
        logger.warning(
            "FloodWait for message %s: sleeping %s seconds.",
            message_id,
            flood_error.seconds,
        )
        await asyncio.sleep(flood_error.seconds)
    except Exception as error:
        logger.error("Error processing message %s: %s", message_id, error)


async def send_with_retry(poster_file, caption: str, retries: int = 1):
    """Send the poster with caption to TARGET_CHANNEL, retrying once on FloodWait."""
    try:
        await client.send_file(
            TARGET_CHANNEL,
            poster_file,
            caption=caption,
            parse_mode="html",
        )
    except FloodWaitError as flood_error:
        logger.warning("FloodWait during upload: sleeping %s seconds.", flood_error.seconds)
        await asyncio.sleep(flood_error.seconds)
        if retries > 0:
            poster_file.seek(0)
            await send_with_retry(poster_file, caption, retries - 1)
        else:
            raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main():
    global http_session
    http_session = aiohttp.ClientSession()
    try:
        await client.start()
        logger.info("Bot started. Monitoring source channel %s.", SOURCE_CHANNEL)
        await client.run_until_disconnected()
    finally:
        await http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
