import asyncio
import logging
import subprocess
from datetime import datetime, timedelta
from src.database import SessionLocal
from src.models import CronJob

logger = logging.getLogger(__name__)

def add_cron_job(user_id: int, schedule: str, command: str, description: str = "") -> int:
    session = SessionLocal()
    try:
        next_run = _calculate_next_run(schedule)
        job = CronJob(user_id=user_id, schedule=schedule, command=command, description=description or command[:50], next_run=next_run)
        session.add(job)
        session.commit()
        return job.id
    finally:
        session.close()

def get_user_jobs(user_id: int):
    session = SessionLocal()
    try:
        jobs = session.query(CronJob).filter(CronJob.user_id == user_id).order_by(CronJob.created_at.desc()).all()
        return [{"id": j.id, "schedule": j.schedule, "command": j.command, "description": j.description, "enabled": j.enabled, "last_run": j.last_run.isoformat() if j.last_run else "never", "next_run": j.next_run.isoformat() if j.next_run else "unknown"} for j in jobs]
    finally:
        session.close()

def toggle_job(job_id: int, user_id: int) -> str:
    session = SessionLocal()
    try:
        job = session.query(CronJob).filter(CronJob.id == job_id, CronJob.user_id == user_id).first()
        if not job:
            return "Job not found."
        job.enabled = not job.enabled
        session.commit()
        return f"Job #{job_id} {'enabled' if job.enabled else 'disabled'}."
    finally:
        session.close()

def delete_job(job_id: int, user_id: int) -> str:
    session = SessionLocal()
    try:
        job = session.query(CronJob).filter(CronJob.id == job_id, CronJob.user_id == user_id).first()
        if not job:
            return "Job not found."
        session.delete(job)
        session.commit()
        return f"Job #{job_id} deleted."
    finally:
        session.close()

def _calculate_next_run(schedule: str) -> datetime:
    """Simple interval-based scheduling: e.g. '5m', '1h', '6h', '24h', '1d'"""
    now = datetime.utcnow()
    s = schedule.strip().lower()
    try:
        if s.endswith('m'):
            return now + timedelta(minutes=int(s[:-1]))
        elif s.endswith('h'):
            return now + timedelta(hours=int(s[:-1]))
        elif s.endswith('d'):
            return now + timedelta(days=int(s[:-1]))
        else:
            return now + timedelta(hours=1)
    except Exception:
        return now + timedelta(hours=1)

def get_due_jobs():
    """Get all jobs that are due to run."""
    session = SessionLocal()
    try:
        now = datetime.utcnow()
        jobs = session.query(CronJob).filter(CronJob.enabled == True, CronJob.next_run <= now).all()
        return [{"id": j.id, "user_id": j.user_id, "command": j.command, "schedule": j.schedule} for j in jobs]
    finally:
        session.close()

def mark_job_run(job_id: int, output: str = ""):
    session = SessionLocal()
    try:
        job = session.query(CronJob).filter(CronJob.id == job_id).first()
        if job:
            job.last_run = datetime.utcnow()
            job.last_output = output[:2000] if output else ""
            job.next_run = _calculate_next_run(job.schedule)
            session.commit()
    finally:
        session.close()

async def execute_job(command: str) -> str:
    """Execute a cron job command safely."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            command, shell=True, capture_output=True, text=True, timeout=30,
            cwd="/home/runner/workspace"
        )
        output = result.stdout[:1000]
        if result.stderr:
            output += f"\nSTDERR: {result.stderr[:500]}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out (30s limit)"
    except Exception as e:
        return f"Error: {str(e)[:300]}"


class CronRunner:
    """Background cron runner that checks for due jobs."""
    
    def __init__(self, bot_app=None):
        self._running = False
        self._task = None
        self.bot_app = bot_app
    
    async def start(self, check_interval: int = 60):
        self._running = True
        self._task = asyncio.create_task(self._loop(check_interval))
        logger.info(f"CronRunner started with {check_interval}s interval")
    
    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
    
    async def _loop(self, interval: int):
        while self._running:
            try:
                await asyncio.sleep(interval)
                due_jobs = get_due_jobs()
                for job_info in due_jobs:
                    try:
                        output = await execute_job(job_info["command"])
                        mark_job_run(job_info["id"], output)
                        if self.bot_app:
                            try:
                                await self.bot_app.bot.send_message(
                                    chat_id=job_info["user_id"],
                                    text=f"CRON JOB #{job_info['id']} EXECUTED\n{'=' * 30}\nCommand: {job_info['command']}\n\nOutput:\n{output[:3000]}"
                                )
                            except Exception:
                                pass
                    except Exception as e:
                        logger.warning(f"Cron job {job_info['id']} failed: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"CronRunner error: {e}")
