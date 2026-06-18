"""
Cookie Run Kingdom - Guild Boss Tracker Bot
============================================
Game resets at 00:00 KST every day (= 15:00 UTC).
Wednesday reminders fire at:
  - 13:00 UTC = 22:00 KST = 2 hrs before reset
  - 14:00 UTC = 23:00 KST = 1 hr before reset

Damage format (in the damage chat topic):
  LA 24.5   → Living Abyss: 24.5 млрд (#n)
  AoD 6.1   → Avatar of Destiny: 6.1 млрд (#n)
  RVD 23.2  → Red Velvet Dragon: 23.2 млрд (#n)
  MA 27.1   → Machine: 27.1 млрд (#n)

Commands (members):
  /start         - Register yourself
  /хто_я         - View your profile and boss records
  /mystats       - Alias for /хто_я

Commands (admins):
  /roster        - View all members
  /summary       - Full boss damage summary
  /setrank       - Update a member's rank
  /deleteprofile - Remove a member
  /announce      - Post a message to the notifications topic
"""

import logging
import re
import sqlite3
from datetime import time
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = "8805765930:AAE5ZQb_nkVr-haNgn00Um4iTt7X-rNRCoY"

ADMIN_IDS = [
    660102611,   # Slava
    948310464,   # Guild master
]

GROUP_CHAT_ID        = -1003710268471
THREAD_NOTIFICATIONS = 2
THREAD_DAMAGE        = 2417

DB_PATH = "guild.db"

UTC = ZoneInfo("UTC")

REMINDER_2HR = time(13, 0, tzinfo=UTC)  # 22:00 KST
REMINDER_1HR = time(14, 0, tzinfo=UTC)  # 23:00 KST

BOSSES = {
    "aod": ("Avatar of Destiny", 0),
    "la":  ("Living Abyss",      1),
    "rvd": ("Red Velvet Dragon", 2),
    "ma":  ("Machine",           3),
}
BOSS_INDEX_TO_NAME = {v[1]: v[0] for v in BOSSES.values()}
BOSS_COUNT = len(BOSS_INDEX_TO_NAME)

# Conversation states
(
    REG_NAME,
    SETRANK_TARGET, SETRANK_VALUE,
    DEL_TARGET,
    ANNOUNCE_TEXT,
) = range(5)


# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS members (
                telegram_id  INTEGER PRIMARY KEY,
                player_name  TEXT NOT NULL,
                rank         TEXT DEFAULT 'Учасник',
                joined_at    TEXT DEFAULT (date('now'))
            );

            CREATE TABLE IF NOT EXISTS boss_records (
                telegram_id  INTEGER NOT NULL,
                boss_index   INTEGER NOT NULL,
                best_damage  REAL DEFAULT 0,
                updated_at   TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (telegram_id, boss_index),
                FOREIGN KEY (telegram_id) REFERENCES members(telegram_id)
            );
        """)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_member(telegram_id: int):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM members WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()


def get_boss_records(telegram_id: int):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT boss_index, best_damage, updated_at FROM boss_records WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchall()
    return {row["boss_index"]: row for row in rows}


def get_rank_for_boss(boss_index: int, damage: float) -> int:
    """Rank of this damage among all members for a given boss (1 = highest)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT best_damage FROM boss_records WHERE boss_index = ? ORDER BY best_damage DESC",
            (boss_index,),
        ).fetchall()
    for i, row in enumerate(rows):
        if row["best_damage"] <= damage:
            return i + 1
    return len(rows)


# ── Keyboards ─────────────────────────────────────────────────────────────────
def rank_keyboard():
    ranks = ["Лідер", "Офіцер", "Учасник"]
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(r, callback_data=f"rank_{r}")] for r in ranks]
    )


# ── Helpers ───────────────────────────────────────────────────────────────────
async def post_to_topic(bot, thread_id: int, text: str):
    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        message_thread_id=thread_id,
        text=text,
    )


# ── Damage chat listener ──────────────────────────────────────────────────────
async def handle_damage_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    if msg.message_thread_id != THREAD_DAMAGE:
        return

    user_id = msg.from_user.id
    member = get_member(user_id)
    if not member:
        await msg.reply_text(
            "Ти ще не зареєстрований. Напиши боту в dm та використай /start."
        )
        return

    text = msg.text.strip()
    match = re.fullmatch(r"([A-Za-z]+)\s+([\d]+(?:[.,][\d]+)?)", text)
    if not match:
        return

    shortcut = match.group(1).lower()
    damage_str = match.group(2).replace(",", ".")

    if shortcut not in BOSSES:
        await msg.reply_text(
            f"Невідомий бос '{match.group(1)}'. Використовуй: LA, AoD, RVD, MA"
        )
        return

    try:
        damage = float(damage_str)
    except ValueError:
        return

    boss_name, boss_index = BOSSES[shortcut]

    with get_db() as conn:
        conn.execute(
            """INSERT INTO boss_records (telegram_id, boss_index, best_damage, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(telegram_id, boss_index)
               DO UPDATE SET best_damage = excluded.best_damage,
                             updated_at  = excluded.updated_at""",
            (user_id, boss_index, damage),
        )

    rank = get_rank_for_boss(boss_index, damage)
    await msg.reply_text(f"{boss_name}: {damage:.1f} млрд (#{rank})")


# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    member = get_member(user.id)
    if member:
        await update.message.reply_text(
            f"Ти вже зареєстрований як {member['player_name']}.\n"
            "Використай /хто_я щоб переглянути свій профіль."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Ласкаво просимо до бота гільдії CRKUKR!\n\nЯк тебе звати в грі?"
    )
    return REG_NAME


async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    user = update.effective_user

    with get_db() as conn:
        conn.execute(
            "INSERT INTO members (telegram_id, player_name) VALUES (?, ?)",
            (user.id, name),
        )
        for i in range(BOSS_COUNT):
            conn.execute(
                "INSERT OR IGNORE INTO boss_records (telegram_id, boss_index, best_damage) VALUES (?, ?, 0)",
                (user.id, i),
            )

    await update.message.reply_text(
        f"Зареєстровано! Нік: {name}\n\n"
        "Йди в чат урону та пиши наприклад: LA 24.5\n"
        "Використай /хто_я щоб переглянути свій профіль."
    )
    return ConversationHandler.END


# ── /хто_я ───────────────────────────────────────────────────────────────────
async def who_am_i(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    member = get_member(update.effective_user.id)
    if not member:
        await update.message.reply_text(
            "Ти ще не зареєстрований. Використай /start."
        )
        return

    records = get_boss_records(update.effective_user.id)

    lines = [
        f"Нік {member['player_name']}",
        f"Ранг: {member['rank']}",
        "⚔ Урон у млрд.:",
    ]

    for i in range(BOSS_COUNT):
        boss_name = BOSS_INDEX_TO_NAME[i]
        rec = records.get(i)
        dmg = rec["best_damage"] if rec else 0.0
        if dmg > 0:
            rank = get_rank_for_boss(i, dmg)
            lines.append(f"{boss_name} - {dmg:.1f} (#{rank})")
        else:
            lines.append(f"{boss_name} - не записано")

    await update.message.reply_text("\n".join(lines))


async def mystats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await who_am_i(update, ctx)


# ── /roster (admin) ───────────────────────────────────────────────────────────
async def roster(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Тільки для офіцерів.")
        return

    with get_db() as conn:
        members = conn.execute(
            "SELECT * FROM members ORDER BY player_name ASC"
        ).fetchall()

    if not members:
        await update.message.reply_text("Ще немає зареєстрованих учасників.")
        return

    lines = ["Список гільдії:", ""]
    for m in members:
        lines.append(f"{m['player_name']} ({m['rank']})")

    await update.message.reply_text("\n".join(lines))


# ── /summary (admin) ──────────────────────────────────────────────────────────
async def summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Тільки для офіцерів.")
        return

    with get_db() as conn:
        members = conn.execute(
            "SELECT * FROM members ORDER BY player_name ASC"
        ).fetchall()
        records = conn.execute(
            "SELECT telegram_id, boss_index, best_damage FROM boss_records"
        ).fetchall()

    dmg_map = {}
    for row in records:
        dmg_map.setdefault(row["telegram_id"], {})[row["boss_index"]] = row["best_damage"]

    lines = ["Зведення урону по босах", ""]
    for m in members:
        boss_data = dmg_map.get(m["telegram_id"], {})
        boss_str = " | ".join(
            f"{BOSS_INDEX_TO_NAME[i][:2]}: {boss_data.get(i, 0):.1f}"
            for i in range(BOSS_COUNT)
        )
        lines.append(f"{m['player_name']}: {boss_str}")

    await update.message.reply_text("\n".join(lines))


# ── /setrank (admin) ──────────────────────────────────────────────────────────
async def setrank_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Тільки для офіцерів.")
        return ConversationHandler.END
    await update.message.reply_text("Введи нік гравця для зміни рангу:")
    return SETRANK_TARGET


async def setrank_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    with get_db() as conn:
        member = conn.execute(
            "SELECT * FROM members WHERE LOWER(player_name) = LOWER(?)", (name,)
        ).fetchone()

    if not member:
        await update.message.reply_text(f"Гравця з ніком '{name}' не знайдено.")
        return ConversationHandler.END

    ctx.user_data["rank_target_id"] = member["telegram_id"]
    ctx.user_data["rank_target_name"] = member["player_name"]
    await update.message.reply_text(
        f"Обери новий ранг для {member['player_name']}:",
        reply_markup=rank_keyboard(),
    )
    return SETRANK_VALUE


async def setrank_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    new_rank = query.data.split("_", 1)[1]

    with get_db() as conn:
        conn.execute(
            "UPDATE members SET rank = ? WHERE telegram_id = ?",
            (new_rank, ctx.user_data["rank_target_id"]),
        )

    await query.edit_message_text(
        f"Ранг оновлено: {ctx.user_data['rank_target_name']} тепер {new_rank}."
    )
    return ConversationHandler.END


# ── /deleteprofile (admin) ────────────────────────────────────────────────────
async def delete_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Тільки для офіцерів.")
        return ConversationHandler.END
    await update.message.reply_text("Введи нік гравця для видалення:")
    return DEL_TARGET


async def delete_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    with get_db() as conn:
        member = conn.execute(
            "SELECT * FROM members WHERE LOWER(player_name) = LOWER(?)", (name,)
        ).fetchone()

        if not member:
            await update.message.reply_text(f"Гравця з ніком '{name}' не знайдено.")
            return ConversationHandler.END

        conn.execute("DELETE FROM boss_records WHERE telegram_id = ?", (member["telegram_id"],))
        conn.execute("DELETE FROM members WHERE telegram_id = ?", (member["telegram_id"],))

    await update.message.reply_text(
        f"Гравця {member['player_name']} видалено з гільдії."
    )
    return ConversationHandler.END


# ── /announce (admin) ─────────────────────────────────────────────────────────
async def announce_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Тільки для офіцерів.")
        return ConversationHandler.END
    await update.message.reply_text("Введи повідомлення для публікації в чаті сповіщень:")
    return ANNOUNCE_TEXT


async def announce_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await post_to_topic(ctx.bot, THREAD_NOTIFICATIONS, update.message.text.strip())
        await update.message.reply_text("Повідомлення опубліковано в чаті сповіщень.")
    except Exception as e:
        await update.message.reply_text(f"Помилка публікації: {e}")
    return ConversationHandler.END


# ── Scheduled reminders ───────────────────────────────────────────────────────
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    is_two_hour = context.job.data["is_two_hour"]

    if is_two_hour:
        text = (
            "До скидання квитків на боса гільдії залишилось 2 години!\n"
            "Це 22:00 KST. Ви вже використали свої квитки сьогодні?"
        )
    else:
        text = (
            "Залишилась 1 година! Скидання о 00:00 KST (зараз 23:00 KST).\n"
            "Останній шанс вдарити по босу!"
        )

    try:
        await post_to_topic(context.bot, THREAD_NOTIFICATIONS, text)
    except Exception as e:
        logging.warning(f"Не вдалося опублікувати нагадування: {e}")


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано.")
    return ConversationHandler.END


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    logging.basicConfig(level=logging.INFO)
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setrank", setrank_start)],
        states={
            SETRANK_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, setrank_target)],
            SETRANK_VALUE:  [CallbackQueryHandler(setrank_value, pattern="^rank_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("deleteprofile", delete_start)],
        states={
            DEL_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_target)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("announce", announce_start)],
        states={
            ANNOUNCE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, announce_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(CommandHandler("хто_я", who_am_i))
    app.add_handler(CommandHandler("mystats", mystats))
    app.add_handler(CommandHandler("roster", roster))
    app.add_handler(CommandHandler("summary", summary))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Chat(GROUP_CHAT_ID),
        handle_damage_message,
    ))

    jq = app.job_queue
    jq.run_daily(
        send_reminder,
        time=REMINDER_2HR,
        days=(2,),
        data={"is_two_hour": True},
        name="reminder_2hr",
    )
    jq.run_daily(
        send_reminder,
        time=REMINDER_1HR,
        days=(2,),
        data={"is_two_hour": False},
        name="reminder_1hr",
    )

    print("Бот запущено...")
    app.run_polling()


if __name__ == "__main__":
    main()
