from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean, DateTime, Float, JSON
)
from src.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(String(20), default="medium")
    status = Column(String(20), default="pending")
    category = Column(String(100), nullable=True)
    due_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    tags = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CommandLog(Base):
    __tablename__ = "command_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    command = Column(String(500), nullable=False)
    output = Column(Text, nullable=True)
    status = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    authorized = Column(Boolean, default=False)
    timezone = Column(String(50), default="UTC")
    asana_project_gid = Column(String, nullable=True)
    linear_token = Column(String, nullable=True)
    github_token = Column(String, nullable=True)
    slack_webhook = Column(String, nullable=True)
    discord_webhook = Column(String, nullable=True)
    digest_enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    command = Column(String(100), nullable=False)
    args = Column(Text, nullable=True)
    status = Column(String(20), default="ok")
    output_preview = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationMemory(Base):
    __tablename__ = "conversation_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    source = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    entities = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    objective = Column(Text, nullable=False)
    status = Column(String(30), default="active")
    progress_pct = Column(Integer, default=0)
    last_result = Column(Text, nullable=True)
    wave_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CronJob(Base):
    __tablename__ = "cron_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    schedule = Column(String(100), nullable=False)
    command = Column(String(500), nullable=False)
    description = Column(String(255), nullable=True)
    enabled = Column(Boolean, default=True)
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)
    last_output = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProviderMetric(Base):
    __tablename__ = "provider_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(30), nullable=False)
    model = Column(String(100), nullable=False)
    prompt_type = Column(String(50), nullable=False)
    latency_ms = Column(Integer, nullable=False)
    success = Column(Boolean, nullable=False)
    token_count = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IntegrationConfig(Base):
    __tablename__ = "integration_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    provider = Column(String(50), nullable=False)
    config_json = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PipelineTrigger(Base):
    __tablename__ = "pipeline_triggers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    trigger_command = Column(String(100), nullable=False)
    actions_json = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DigestConfig(Base):
    __tablename__ = "digest_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    frequency = Column(String(20), default="daily")
    hour_utc = Column(Integer, default=9)
    day_of_week = Column(Integer, default=1)
    enabled = Column(Boolean, default=True)
    last_sent = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
