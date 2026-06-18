"""
Cookie Run Kingdom - Guild Boss Tracker Bot
============================================
Commands:
  /start          - Register yourself
  /profile        - View your profile
  /log            - Log boss damage for this week
  /mystats        - View your weekly stats
  /roster         - View full guild roster (admins only)
  /summary        - View weekly damage summary (admins only)
  /setrank        - Set a member's rank (admins only)
  /resetweek      - Reset weekly attendance (admins only)
  /deleteprofile  - Remove a member (admins only)
"""

import logging
import sqlite3
import os
from datetime import date
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
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

# Telegram user IDs of guild admins/officers
ADMIN_IDS = [
    123456789,   # replace with real Telegram user IDs
948310464,
]

DB_PATH = "guild.db"

BOSS_NAMES = ["Boss 1", "Boss 2", "Boss 3"]  # rename to actual boss names

# Conversation states
(
    REG_NAME, REG_PLACE,
    LOG_BOSS, LOG_DAMAGE,
    SETRANK_TARGET, SETRANK_VALUE,
    DEL_TARGET,
) = range(7)


# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS members (
                telegram_id   INTEGER PRIMARY KEY,
                player_name   TEXT NOT NULL,
                rank          TEXT DEFAULT 'Member',
                guild_place   INTEGER,
                joined_at     TEXT DEFAULT (date('now'))
            );

            CREATE TABLE IF NOT EXISTS boss_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER NOT NULL,
                week          TEXT NOT NULL,
                boss_index    INTEGER NOT NULL,
                damage        INTEGER NOT NULL,
                logged_at     TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (telegram_id) REFERENCES members(telegram_id)
            );
        """)


def current_week():
    """ISO week string e.g. '2025-W23'"""
    today = date.today()
    return f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_member(telegram_id: int):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM members WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()


# ── Helpers ───────────────────────────────────────────────────────────────────
def boss_keyboard():
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"boss_{i}")]
        for i, name in enumerate(BOSS_NAMES)
    ]
    return InlineKeyboardMarkup(buttons)


def rank_keyboard():
    ranks = ["Leader", "Officer", "Elite", "Member", "Recruit"]
    buttons = [[InlineKeyboardButton(r, callback_data=f"rank_{r}")] for r in ranks]
    return InlineKeyboardMarkup(buttons)


# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    member = get_member(user.id)
    if member:
        await update.message.reply_text(
            f"You're already registered as {member['player_name']}.\n"
            "Use /profile to see your data."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Welcome! Let's register you.\n\nWhat is your in-game player name?"
    )
    return REG_NAME


async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_name"] = update.message.text.strip()
    await update.message.reply_text(
        "What is your guild place number? (e.g. 1-30)"
    )
    return REG_PLACE


async def reg_place(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Please enter a number.")
        return REG_PLACE

    place = int(text)
    name = ctx.user_data["reg_name"]
    user = update.effective_user

    with get_db() as conn:
        conn.execute(
            "INSERT INTO members (telegram_id, player_name, guild_place) VALUES (?, ?, ?)",
            (user.id, name, place),
        )

    await update.message.reply_text(
        f"Registered! Name: {name} | Place: #{place}\n"
        "Use /log to submit boss damage or /profile to view your stats."
    )
    return ConversationHandler.END


# ── /profile ──────────────────────────────────────────────────────────────────
async def profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    member = get_member(update.effective_user.id)
    if not member:
        await update.message.reply_text("You're not registered. Use /start.")
        return

    week = current_week()
    with get_db() as conn:
        logs = conn.execute(
            "SELECT boss_index, SUM(damage) as total FROM boss_logs "
            "WHERE telegram_id = ? AND week = ? GROUP BY boss_index",
            (update.effective_user.id, week),
        ).fetchall()

    lines = [
        f"Player: {member['player_name']}",
        f"Rank: {member['rank']}",
        f"Guild place: #{member['guild_place']}",
        f"Week: {week}",
        "",
        "Boss damage this week:",
    ]
    boss_totals = {row["boss_index"]: row["total"] for row in logs}
    for i, name in enumerate(BOSS_NAMES):
        dmg = boss_totals.get(i, 0)
        hit = "✅" if dmg > 0 else "❌"
        lines.append(f"  {hit} {name}: {dmg:,} dmg")

    await update.message.reply_text("\n".join(lines))


# ── /log ──────────────────────────────────────────────────────────────────────
async def log_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    member = get_member(update.effective_user.id)
    if not member:
        await update.message.reply_text("You're not registered. Use /start first.")
        return ConversationHandler.END

    await update.message.reply_text(
        "Which boss are you logging damage for?",
        reply_markup=boss_keyboard(),
    )
    return LOG_BOSS


async def log_boss_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    boss_index = int(query.data.split("_")[1])
    ctx.user_data["log_boss"] = boss_index
    boss_name = BOSS_NAMES[boss_index]
    await query.edit_message_text(f"Boss: {boss_name}\n\nHow much damage did you deal? (numbers only)")
    return LOG_DAMAGE


async def log_damage_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "").replace(".", "")
    if not text.isdigit():
        await update.message.reply_text("Please enter a number, e.g. 4500000")
        return LOG_DAMAGE

    damage = int(text)
    boss_index = ctx.user_data["log_boss"]
    user_id = update.effective_user.id
    week = current_week()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO boss_logs (telegram_id, week, boss_index, damage) VALUES (?, ?, ?, ?)",
            (user_id, week, boss_index, damage),
        )

    boss_name = BOSS_NAMES[boss_index]
    await update.message.reply_text(
        f"Logged! {boss_name}: {damage:,} dmg for week {week}."
    )
    return ConversationHandler.END


# ── /mystats ──────────────────────────────────────────────────────────────────
async def mystats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await profile(update, ctx)


# ── /roster (admin) ───────────────────────────────────────────────────────────
async def roster(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Officers only.")
        return

    with get_db() as conn:
        members = conn.execute(
            "SELECT * FROM members ORDER BY guild_place ASC"
        ).fetchall()

    if not members:
        await update.message.reply_text("No members registered yet.")
        return

    lines = ["Guild Roster:", ""]
    for m in members:
        lines.append(f"#{m['guild_place']} {m['player_name']} ({m['rank']})")

    await update.message.reply_text("\n".join(lines))


# ── /summary (admin) ──────────────────────────────────────────────────────────
async def summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Officers only.")
        return

    week = current_week()
    with get_db() as conn:
        members = conn.execute(
            "SELECT * FROM members ORDER BY guild_place ASC"
        ).fetchall()
        logs = conn.execute(
            "SELECT telegram_id, boss_index, SUM(damage) as total "
            "FROM boss_logs WHERE week = ? GROUP BY telegram_id, boss_index",
            (week,),
        ).fetchall()

    # Build lookup: {telegram_id: {boss_index: total}}
    dmg_map = {}
    for row in logs:
        dmg_map.setdefault(row["telegram_id"], {})[row["boss_index"]] = row["total"]

    lines = [f"Weekly Summary - {week}", ""]
    hit_count = 0
    for m in members:
        boss_data = dmg_map.get(m["telegram_id"], {})
        total = sum(boss_data.values())
        hit = "✅" if total > 0 else "❌"
        if total > 0:
            hit_count += 1
        boss_str = " | ".join(
            f"B{i+1}: {boss_data.get(i, 0):,}" for i in range(len(BOSS_NAMES))
        )
        lines.append(f"{hit} #{m['guild_place']} {m['player_name']}: {boss_str}")

    lines.append(f"\nAttendance: {hit_count}/{len(members)}")
    await update.message.reply_text("\n".join(lines))


# ── /setrank (admin) ──────────────────────────────────────────────────────────
async def setrank_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Officers only.")
        return ConversationHandler.END

    await update.message.reply_text(
        "Enter the player name to update rank:"
    )
    return SETRANK_TARGET


async def setrank_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    with get_db() as conn:
        member = conn.execute(
            "SELECT * FROM members WHERE LOWER(player_name) = LOWER(?)", (name,)
        ).fetchone()

    if not member:
        await update.message.reply_text(f"No member found with name '{name}'.")
        return ConversationHandler.END

    ctx.user_data["rank_target_id"] = member["telegram_id"]
    ctx.user_data["rank_target_name"] = member["player_name"]
    await update.message.reply_text(
        f"Select new rank for {member['player_name']}:",
        reply_markup=rank_keyboard(),
    )
    return SETRANK_VALUE


async def setrank_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    new_rank = query.data.split("_", 1)[1]
    target_id = ctx.user_data["rank_target_id"]
    target_name = ctx.user_data["rank_target_name"]

    with get_db() as conn:
        conn.execute(
            "UPDATE members SET rank = ? WHERE telegram_id = ?",
            (new_rank, target_id),
        )

    await query.edit_message_text(f"Rank updated: {target_name} is now {new_rank}.")
    return ConversationHandler.END


# ── /resetweek (admin) ────────────────────────────────────────────────────────
async def resetweek(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Officers only.")
        return

    week = current_week()
    with get_db() as conn:
        conn.execute("DELETE FROM boss_logs WHERE week = ?", (week,))

    await update.message.reply_text(f"All damage logs for {week} have been cleared.")


# ── /deleteprofile (admin) ────────────────────────────────────────────────────
async def delete_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Officers only.")
        return ConversationHandler.END

    await update.message.reply_text("Enter the player name to remove:")
    return DEL_TARGET


async def delete_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    with get_db() as conn:
        member = conn.execute(
            "SELECT * FROM members WHERE LOWER(player_name) = LOWER(?)", (name,)
        ).fetchone()

        if not member:
            await update.message.reply_text(f"No member found with name '{name}'.")
            return ConversationHandler.END

        conn.execute("DELETE FROM boss_logs WHERE telegram_id = ?", (member["telegram_id"],))
        conn.execute("DELETE FROM members WHERE telegram_id = ?", (member["telegram_id"],))

    await update.message.reply_text(f"Removed {member['player_name']} from the guild.")
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    logging.basicConfig(level=logging.INFO)
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Registration flow
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REG_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_place)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    # Log damage flow
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("log", log_start)],
        states={
            LOG_BOSS:   [CallbackQueryHandler(log_boss_choice, pattern="^boss_")],
            LOG_DAMAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, log_damage_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    # Set rank flow (admin)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setrank", setrank_start)],
        states={
            SETRANK_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, setrank_target)],
            SETRANK_VALUE:  [CallbackQueryHandler(setrank_value, pattern="^rank_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    # Delete profile flow (admin)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("deleteprofile", delete_start)],
        states={
            DEL_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_target)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("mystats", mystats))
    app.add_handler(CommandHandler("roster", roster))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("resetweek", resetweek))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
