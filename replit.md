# Telegram Bot - Replit Manager

## Overview
A Telegram bot for managing tasks, notes, shell commands, and git operations on a Replit project. Built with Python 3.11, python-telegram-bot v22, SQLAlchemy, and PostgreSQL.

## Project Structure
```
main.py              - Entry point
src/
  bot.py             - Bot application setup and polling
  database.py        - SQLAlchemy engine, session, and Base
  models.py          - Database models (Task, Note, CommandLog, UserSettings)
  handlers/
    help.py          - /start, /help, /authorize, /whoami
    shell.py         - /shell command execution
    tasks.py         - /addtask, /tasks, /donetask, /deltask
    notes.py         - /addnote, /notes, /viewnote, /delnote
    git.py           - /gitstatus, /gitlog, /gitdiff, /gitcommit, /gitpull, /gitpush
```

## Environment Variables
- `TELEGRAM_BOT_TOKEN` - Bot token from @BotFather (required)
- `ADMIN_PASSWORD` - Password for authorizing users for shell/git commands (required)
- `DATABASE_URL` - PostgreSQL connection string (provided by Replit)

## Authorization System
- Task and note commands are available to all users
- Shell and git commands require authorization
- Users authorize via `/authorize <password>` using the ADMIN_PASSWORD

## Dependencies
- python-telegram-bot==22.6
- SQLAlchemy>=2.0
- psycopg2-binary>=2.9

## Running
The bot runs via `python main.py` and uses long polling to receive Telegram updates.
