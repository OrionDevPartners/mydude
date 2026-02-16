import json
import logging
from datetime import datetime
from src.database import SessionLocal
from src.models import SwarmMemoryLayer, PerformanceLedgerEntry, SentinelEvent, ClaimProvenanceRecord

logger = logging.getLogger(__name__)


class CognitiveStatePersistence:

    def save_orchestrator_state(self, orchestrator):
        session = SessionLocal()
        try:
            if orchestrator.provenance and hasattr(orchestrator.provenance, '_records'):
                for claim_id, prov in orchestrator.provenance._records.items():
                    record = ClaimProvenanceRecord(
                        claim_id=str(claim_id),
                        origin_provider=getattr(prov, 'origin_provider', 'unknown'),
                        origin_role=getattr(prov, 'origin_role', 'unknown'),
                        wave_idx=getattr(prov, 'wave_idx', 0),
                        claim_text='',
                        evidence_json=json.dumps(getattr(prov, 'evidence_pointers', []))[:5000],
                        parent_claim_ids=json.dumps(getattr(prov, 'parent_claim_ids', [])),
                        hr_at_creation=getattr(prov, 'hr_at_creation', 0.0),
                        cs_at_creation=getattr(prov, 'cs_at_creation', 100),
                        transformations_json=json.dumps(getattr(prov, 'transformations', []))[:5000],
                    )
                    session.add(record)

            if orchestrator.auditor and hasattr(orchestrator.auditor, '_ledger'):
                ledger = orchestrator.auditor._ledger
                if hasattr(ledger, '_entries'):
                    for entry in ledger._entries:
                        record = PerformanceLedgerEntry(
                            wave_idx=getattr(entry, 'wave_idx', 0),
                            avg_cs=getattr(entry, 'avg_cs', 0.0),
                            avg_hr=getattr(entry, 'avg_hr', 0.0),
                            agent_count=getattr(entry, 'agent_count', 0),
                            consensus_confidence=getattr(entry, 'consensus_confidence', 0.0),
                            dissent_count=getattr(entry, 'dissent_count', 0),
                            meta_claims_json='[]',
                        )
                        session.add(record)

            if orchestrator.sentinel and hasattr(orchestrator.sentinel, 'get_active_alerts'):
                try:
                    alerts = orchestrator.sentinel.get_active_alerts()
                    for alert in alerts:
                        record = SentinelEvent(
                            alert_id=getattr(alert, 'alert_id', f"alert-{datetime.utcnow().timestamp()}"),
                            alert_type=getattr(alert, 'alert_type', 'unknown'),
                            severity=getattr(alert, 'severity', 'info'),
                            description=getattr(alert, 'description', '')[:5000],
                            recommended_action=getattr(alert, 'recommended_action', '')[:5000],
                        )
                        session.add(record)
                except Exception as e:
                    logger.warning("Failed to save sentinel alerts: %s", e)

            prov_count = len(orchestrator.provenance._records) if orchestrator.provenance and hasattr(orchestrator.provenance, '_records') else 0
            snapshot = {
                "saved_at": datetime.utcnow().isoformat(),
                "provenance_count": prov_count,
                "auditor_active": orchestrator.auditor is not None,
                "sentinel_active": orchestrator.sentinel is not None,
                "hr_monitor_avg": orchestrator.hr_monitor.get_average() if hasattr(orchestrator.hr_monitor, 'get_average') else 0.0,
            }
            layer = SwarmMemoryLayer(
                layer_type="cognitive_snapshot",
                content=json.dumps(snapshot),
                summary=f"Cognitive state snapshot at {datetime.utcnow().isoformat()}",
                topic="system_state",
            )
            session.add(layer)

            session.commit()
            logger.info("Cognitive state saved successfully")
        except Exception as e:
            session.rollback()
            logger.warning("Failed to save cognitive state: %s", e)
        finally:
            session.close()

    def rehydrate_orchestrator(self, orchestrator):
        session = SessionLocal()
        try:
            prov_records = session.query(ClaimProvenanceRecord).order_by(
                ClaimProvenanceRecord.created_at.desc()
            ).limit(200).all()

            if prov_records and orchestrator.provenance:
                for r in prov_records:
                    try:
                        orchestrator.provenance.add_provenance(
                            claim_id=r.claim_id,
                            provider=r.origin_provider or "unknown",
                            role=r.origin_role or "unknown",
                            wave=r.wave_idx or 0,
                            evidence=json.loads(r.evidence_json) if r.evidence_json else [],
                            parent_ids=json.loads(r.parent_claim_ids) if r.parent_claim_ids else [],
                            hr=r.hr_at_creation or 0.0,
                            cs=r.cs_at_creation or 100,
                        )
                    except Exception:
                        pass
                logger.info("Rehydrated %d provenance records into orchestrator", len(prov_records))

            perf_records = session.query(PerformanceLedgerEntry).order_by(
                PerformanceLedgerEntry.created_at.desc()
            ).limit(50).all()

            if perf_records and orchestrator.auditor and hasattr(orchestrator.auditor, '_ledger'):
                for r in perf_records:
                    try:
                        orchestrator.auditor._ledger.record(
                            wave_idx=r.wave_idx,
                            avg_cs=r.avg_cs,
                            avg_hr=r.avg_hr,
                            agent_count=r.agent_count,
                            consensus_confidence=r.consensus_confidence,
                            dissent_count=r.dissent_count,
                        )
                    except Exception:
                        pass
                logger.info("Rehydrated %d performance ledger entries", len(perf_records))

            sentinel_records = session.query(SentinelEvent).filter(
                SentinelEvent.acknowledged == False
            ).order_by(SentinelEvent.created_at.desc()).limit(50).all()

            if sentinel_records and orchestrator.sentinel:
                try:
                    from src.swarm.sentinel import SentinelAlert
                    for r in sentinel_records:
                        alert = SentinelAlert(
                            alert_type=r.alert_type or "unknown",
                            severity=r.severity or "info",
                            description=r.description or "",
                            recommended_action=r.recommended_action or "",
                            alert_id=r.alert_id or "",
                        )
                        alert.acknowledged = False
                        if hasattr(orchestrator.sentinel, '_alerts'):
                            orchestrator.sentinel._alerts.append(alert)
                    logger.info("Rehydrated %d sentinel alerts", len(sentinel_records))
                except Exception as e:
                    logger.warning("Failed to rehydrate sentinel alerts: %s", e)

            if perf_records and orchestrator.sentinel and hasattr(orchestrator.sentinel, '_hr_history'):
                try:
                    for r in perf_records:
                        if r.avg_hr is not None:
                            orchestrator.sentinel._hr_history.append(r.avg_hr)
                        if r.avg_cs is not None:
                            orchestrator.sentinel._cs_history.append(r.avg_cs)
                    if orchestrator.sentinel._hr_history:
                        orchestrator.sentinel.running_avg_hr = sum(orchestrator.sentinel._hr_history) / len(orchestrator.sentinel._hr_history)
                    if orchestrator.sentinel._cs_history:
                        orchestrator.sentinel.running_avg_cs = sum(orchestrator.sentinel._cs_history) / len(orchestrator.sentinel._cs_history)
                    logger.info("Rehydrated sentinel HR/CS history from %d performance records", len(perf_records))
                except Exception as e:
                    logger.warning("Failed to rehydrate sentinel history: %s", e)

            return True
        except Exception as e:
            logger.warning("Failed to rehydrate orchestrator: %s", e)
            return False
        finally:
            session.close()

    def get_latest_snapshot(self):
        session = SessionLocal()
        try:
            record = session.query(SwarmMemoryLayer).filter(
                SwarmMemoryLayer.layer_type == "cognitive_snapshot"
            ).order_by(SwarmMemoryLayer.created_at.desc()).first()
            if record:
                return {
                    "snapshot": json.loads(record.content) if record.content else {},
                    "saved_at": record.created_at.isoformat() if record.created_at else "",
                    "summary": record.summary or "",
                }
            return None
        except Exception as e:
            logger.warning("Failed to load cognitive snapshot: %s", e)
            return None
        finally:
            session.close()

    def get_recent_provenance(self, limit=50):
        session = SessionLocal()
        try:
            records = session.query(ClaimProvenanceRecord).order_by(
                ClaimProvenanceRecord.created_at.desc()
            ).limit(limit).all()
            return [{
                "claim_id": r.claim_id,
                "provider": r.origin_provider,
                "role": r.origin_role,
                "wave": r.wave_idx,
                "hr": r.hr_at_creation,
                "cs": r.cs_at_creation,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            } for r in records]
        except Exception as e:
            logger.warning("Failed to load provenance: %s", e)
            return []
        finally:
            session.close()

    def get_recent_sentinel_events(self, limit=20):
        session = SessionLocal()
        try:
            records = session.query(SentinelEvent).filter(
                SentinelEvent.acknowledged == False
            ).order_by(SentinelEvent.created_at.desc()).limit(limit).all()
            return [{
                "alert_id": r.alert_id,
                "type": r.alert_type,
                "severity": r.severity,
                "description": r.description[:200] if r.description else "",
                "created_at": r.created_at.isoformat() if r.created_at else "",
            } for r in records]
        except Exception as e:
            logger.warning("Failed to load sentinel events: %s", e)
            return []
        finally:
            session.close()

    def get_performance_trends(self, limit=30):
        session = SessionLocal()
        try:
            records = session.query(PerformanceLedgerEntry).order_by(
                PerformanceLedgerEntry.created_at.desc()
            ).limit(limit).all()
            return [{
                "wave": r.wave_idx,
                "avg_cs": r.avg_cs,
                "avg_hr": r.avg_hr,
                "agents": r.agent_count,
                "dissent": r.dissent_count,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            } for r in records]
        except Exception as e:
            logger.warning("Failed to load performance trends: %s", e)
            return []
        finally:
            session.close()

    def store_swarm_memory(self, layer_type, content, summary="", topic="", wave_idx=None, cs=100, hr=0.0, session_id=None):
        session = SessionLocal()
        try:
            entry = SwarmMemoryLayer(
                layer_type=layer_type,
                content=content[:10000] if content else "",
                summary=summary[:2000] if summary else "",
                topic=topic[:255] if topic else "",
                compliance_score=cs,
                hallucination_risk=hr,
                wave_idx=wave_idx,
                session_id=session_id,
            )
            session.add(entry)
            session.commit()
            return entry.id
        except Exception as e:
            session.rollback()
            logger.warning("Failed to store swarm memory: %s", e)
            return None
        finally:
            session.close()

    def search_swarm_memory(self, query, layer_type=None, limit=10):
        session = SessionLocal()
        try:
            q = session.query(SwarmMemoryLayer)
            if layer_type:
                q = q.filter(SwarmMemoryLayer.layer_type == layer_type)
            q = q.filter(
                (SwarmMemoryLayer.content.ilike(f"%{query}%")) |
                (SwarmMemoryLayer.summary.ilike(f"%{query}%")) |
                (SwarmMemoryLayer.topic.ilike(f"%{query}%"))
            )
            records = q.order_by(SwarmMemoryLayer.created_at.desc()).limit(limit).all()
            return [{
                "id": r.id,
                "type": r.layer_type,
                "summary": r.summary or r.content[:200],
                "topic": r.topic,
                "cs": r.compliance_score,
                "hr": r.hallucination_risk,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            } for r in records]
        except Exception as e:
            logger.warning("Failed to search swarm memory: %s", e)
            return []
        finally:
            session.close()
