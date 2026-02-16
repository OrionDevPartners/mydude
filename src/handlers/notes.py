from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from src.database import SessionLocal
from src.models import Note


async def add_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text(
                "Usage: /addnote <title> | <content> | <category>\n\n"
                "Example: /addnote Meeting Notes | Discussed project timeline | work"
            )
            return

        raw = " ".join(context.args)
        parts = [p.strip() for p in raw.split("|")]

        title = parts[0]
        content = parts[1] if len(parts) > 1 else None
        category = parts[2] if len(parts) > 2 else None

        session = SessionLocal()
        try:
            note = Note(
                user_id=user_id,
                title=title,
                content=content,
                category=category,
            )
            session.add(note)
            session.commit()
            await update.message.reply_text(f"📝 Note #{note.id} added: {title}")
        finally:
            session.close()

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        session = SessionLocal()
        try:
            notes = (
                session.query(Note)
                .filter_by(user_id=user_id)
                .order_by(Note.created_at.desc())
                .all()
            )

            if not notes:
                await update.message.reply_text("📝 No notes found.")
                return

            lines = ["📝 Your Notes\n"]
            for n in notes:
                cat = f" [{n.category}]" if n.category else ""
                lines.append(f"  #{n.id} - {n.title}{cat}")

            msg = "\n".join(lines)
            if len(msg) > 4000:
                msg = msg[:4000] + "\n... (truncated)"
            await update.message.reply_text(msg)
        finally:
            session.close()

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def view_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("Usage: /viewnote <id>")
            return

        try:
            note_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Please provide a valid note ID number.")
            return

        session = SessionLocal()
        try:
            note = session.query(Note).filter_by(id=note_id, user_id=user_id).first()
            if not note:
                await update.message.reply_text(f"Note #{note_id} not found.")
                return

            lines = [
                f"📝 Note #{note.id}",
                f"Title: {note.title}",
            ]
            if note.category:
                lines.append(f"Category: {note.category}")
            if note.tags:
                lines.append(f"Tags: {note.tags}")
            lines.append(f"Created: {note.created_at.strftime('%Y-%m-%d %H:%M')}")
            if note.content:
                lines.append(f"\n{note.content}")

            msg = "\n".join(lines)
            if len(msg) > 4000:
                msg = msg[:4000] + "\n... (truncated)"
            await update.message.reply_text(msg)
        finally:
            session.close()

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def del_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("Usage: /delnote <id>")
            return

        try:
            note_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Please provide a valid note ID number.")
            return

        session = SessionLocal()
        try:
            note = session.query(Note).filter_by(id=note_id, user_id=user_id).first()
            if not note:
                await update.message.reply_text(f"Note #{note_id} not found.")
                return

            session.delete(note)
            session.commit()
            await update.message.reply_text(f"🗑️ Note #{note_id} deleted.")
        finally:
            session.close()

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


def get_handlers():
    return [
        CommandHandler("addnote", add_note),
        CommandHandler("notes", list_notes),
        CommandHandler("viewnote", view_note),
        CommandHandler("delnote", del_note),
    ]
