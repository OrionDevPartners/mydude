"""FleetProvisioner — plan → operator-approval → apply provisioning pipeline.

Governs real cloud resource creation for bots and teams.  Every provisioning
run passes through three phases, each recorded in ProvisionedResource and
ProvisioningJob rows so the full audit trail is permanently recoverable:

  1. plan()    — dry-run; generate a plan_output for operator review.  No
                 real resources are created.  Records job with status=awaiting_approval.
  2. approve() — operator explicitly approves the plan (UI action).
  3. apply()   — executes the creation after approval.  Records resource_id and
                 apply_output.  Status transitions: provisioning → active | failed.

All provisioning requests route through the full broker pipeline:
  contract validation → policy gate (fleet_provision_plan/fleet_provision_approve)
  → Integrations._do_create_job/_do_apply_job → capability audit log

Private helpers (_do_create_job, _do_apply_job) are called ONLY by
Integrations after the broker grants permission.  Public wrappers
(create_provisioning_job, approve_provisioning_job) build a broker and route
through it — use these from API routes and agent code.

Supported resource types:
  vm          — virtual machine (stub/AWS EC2 via boto3 if configured)
  git_repo    — GitHub repository (stub/GitHub API if token configured)
  ml_service  — SageMaker endpoint or MLflow experiment (real path or stub)

Cost/scale guardrails: vm and ml_service are gated behind
ALLOW_FLEET_PROVISIONING=true (enforced at the policy layer).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes", "on")


def _plan_vm(config: Dict) -> str:
    name = config.get("name", "bot-vm")
    instance_type = config.get("instance_type", "t3.micro")
    region = config.get("region", "us-east-1")
    ami = config.get("ami", "ami-0c55b159cbfafe1f0")
    lines = [
        f"PLAN: Create EC2 VM",
        f"  Name:          {name}",
        f"  Instance type: {instance_type}",
        f"  Region:        {region}",
        f"  AMI:           {ami}",
        f"  Estimated cost: ~$0.01–0.10/hr depending on type",
        f"",
        f"  Resources to create:",
        f"  + aws_instance.{name} ({instance_type}, {region})",
        f"",
        f"  Gate: ALLOW_FLEET_PROVISIONING must be true to apply.",
    ]
    return "\n".join(lines)


def _plan_git_repo(config: Dict) -> str:
    repo_name = config.get("name", "bot-repo")
    private = config.get("private", True)
    org = config.get("org", "")
    owner = f"{org}/" if org else ""
    lines = [
        f"PLAN: Create GitHub Repository",
        f"  Name:    {owner}{repo_name}",
        f"  Private: {private}",
        f"",
        f"  Resources to create:",
        f"  + github_repository.{repo_name}",
        f"",
        f"  Requires: GITHUB_TOKEN in vault.",
    ]
    return "\n".join(lines)


def _plan_ml_service(config: Dict) -> str:
    svc_type = config.get("type", "mlflow")
    name = config.get("name", "bot-ml")
    lines = [
        f"PLAN: Stand up ML Service ({svc_type})",
        f"  Name:    {name}",
        f"  Type:    {svc_type}",
        f"",
        f"  Resources to create:",
        f"  + {svc_type}_tracking_server.{name}",
        f"",
        f"  Gate: ALLOW_FLEET_PROVISIONING must be true to apply.",
    ]
    return "\n".join(lines)


def _apply_vm(config: Dict) -> Dict[str, Any]:
    try:
        import boto3
        name = config.get("name", "bot-vm")
        instance_type = config.get("instance_type", "t3.micro")
        region = config.get("region", "us-east-1")
        ami = config.get("ami", "ami-0c55b159cbfafe1f0")
        ec2 = boto3.resource("ec2", region_name=region)
        instances = ec2.create_instances(
            ImageId=ami,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": name}, {"Key": "ManagedBy", "Value": "MyDude"}],
            }],
        )
        instance_id = instances[0].id
        return {
            "ok": True,
            "resource_id": instance_id,
            "output": f"EC2 instance {instance_id} launched ({instance_type}, {region})",
        }
    except ImportError:
        return {
            "ok": True,
            "resource_id": f"stub-vm-{config.get('name', 'bot')}",
            "output": "[STUB] boto3 not installed. VM creation simulated. Install boto3 + set AWS credentials to create real VMs.",
        }
    except Exception as e:
        return {"ok": False, "resource_id": None, "output": f"VM creation failed: {e}"}


def _apply_git_repo(config: Dict) -> Dict[str, Any]:
    token = os.environ.get("GITHUB_TOKEN")
    repo_name = config.get("name", "bot-repo")
    private = config.get("private", True)
    org = config.get("org", "")

    if not token:
        return {
            "ok": True,
            "resource_id": f"stub-repo/{repo_name}",
            "output": f"[STUB] No GITHUB_TOKEN found. Repository '{repo_name}' creation simulated. Add GITHUB_TOKEN to vault to create real repos.",
        }

    try:
        import urllib.request, json as _json
        owner_url = f"https://api.github.com/orgs/{org}/repos" if org else "https://api.github.com/user/repos"
        payload = _json.dumps({"name": repo_name, "private": bool(private), "auto_init": True}).encode()
        req = urllib.request.Request(
            owner_url,
            data=payload,
            headers={"Authorization": f"token {token}", "Content-Type": "application/json", "User-Agent": "MyDude-Fleet"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = _json.loads(resp.read())
        repo_url = data.get("html_url", "")
        return {"ok": True, "resource_id": repo_url, "output": f"GitHub repository created: {repo_url}"}
    except Exception as e:
        return {"ok": False, "resource_id": None, "output": f"GitHub repo creation failed: {e}"}


def _apply_ml_service(config: Dict) -> Dict[str, Any]:
    svc_type = config.get("type", "mlflow")
    name = config.get("name", "bot-ml")
    region = config.get("region", "us-east-1")

    if svc_type == "sagemaker":
        try:
            import boto3
            sm = boto3.client("sagemaker", region_name=region)
            endpoint_config_name = f"{name}-config"
            sm.create_endpoint_config(
                EndpointConfigName=endpoint_config_name,
                ProductionVariants=[{
                    "VariantName": "primary",
                    "ModelName": config.get("model_name", name),
                    "InitialInstanceCount": int(config.get("instance_count", 1)),
                    "InstanceType": config.get("instance_type", "ml.t2.medium"),
                }],
                Tags=[{"Key": "ManagedBy", "Value": "MyDude"}],
            )
            sm.create_endpoint(
                EndpointName=name,
                EndpointConfigName=endpoint_config_name,
                Tags=[{"Key": "ManagedBy", "Value": "MyDude"}],
            )
            endpoint_arn = f"arn:aws:sagemaker:{region}:*:endpoint/{name}"
            return {
                "ok": True,
                "resource_id": endpoint_arn,
                "output": f"SageMaker endpoint '{name}' creation initiated ({region}). Status will transition to InService when ready.",
            }
        except ImportError:
            pass
        except Exception as e:
            return {"ok": False, "resource_id": None, "output": f"SageMaker endpoint creation failed: {e}"}

    if svc_type == "mlflow":
        tracking_uri = config.get("tracking_uri", os.environ.get("MLFLOW_TRACKING_URI", ""))
        if tracking_uri:
            try:
                import urllib.request as _ur, json as _j
                payload = _j.dumps({"name": name}).encode()
                req = _ur.Request(
                    f"{tracking_uri.rstrip('/')}/api/2.0/mlflow/experiments/create",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with _ur.urlopen(req, timeout=15) as resp:
                    data = _j.loads(resp.read())
                exp_id = data.get("experiment_id", "unknown")
                return {
                    "ok": True,
                    "resource_id": f"mlflow-experiment-{exp_id}",
                    "output": f"MLflow experiment '{name}' created (id={exp_id}) at {tracking_uri}",
                }
            except Exception as e:
                return {"ok": False, "resource_id": None, "output": f"MLflow experiment creation failed: {e}"}

    return {
        "ok": True,
        "resource_id": f"stub-{svc_type}-{name}",
        "output": (
            f"[STUB] {svc_type} service '{name}' provisioning simulated. "
            f"For SageMaker: install boto3 + set AWS credentials. "
            f"For MLflow: set MLFLOW_TRACKING_URI. "
            f"For live provisioning: set ALLOW_FLEET_PROVISIONING=true."
        ),
    }


def plan_resource(resource_type: str, config: Dict) -> str:
    """Generate a human-readable plan for the given resource type."""
    if resource_type == "vm":
        return _plan_vm(config)
    if resource_type == "git_repo":
        return _plan_git_repo(config)
    if resource_type == "ml_service":
        return _plan_ml_service(config)
    return f"PLAN: Create {resource_type}\nConfig: {config}\n\nNo specific plan template — will attempt generic provisioning."


def _default_provider(resource_type: str) -> str:
    return {"vm": "aws", "git_repo": "github", "ml_service": "mlflow"}.get(resource_type, "stub")


# ---------------------------------------------------------------------------
# Private implementations — called ONLY by Integrations after broker gating
# ---------------------------------------------------------------------------

async def _do_create_job(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a ProvisionedResource + ProvisioningJob (plan phase).

    Called ONLY by Integrations.fleet_provision_plan after the broker has
    validated the contract and passed the policy gate.
    """
    resource_type = params.get("resource_type", "")
    config = params.get("config") or {}
    if isinstance(config, str):
        import json as _j
        try:
            config = _j.loads(config)
        except Exception:
            config = {}
    bot_id = params.get("bot_id")
    team_id = params.get("team_id")

    from src.database import SessionLocal
    from src.models import ProvisionedResource, ProvisioningJob

    plan_output = plan_resource(resource_type, config)
    name = config.get("name", f"{resource_type}-{bot_id or team_id or 'fleet'}")

    db = SessionLocal()
    try:
        resource = ProvisionedResource(
            bot_id=bot_id,
            team_id=team_id,
            resource_type=resource_type,
            provider=config.get("provider", _default_provider(resource_type)),
            name=name,
            status="pending_approval",
            plan_output=plan_output,
            config_json=config,
        )
        db.add(resource)
        db.flush()

        job = ProvisioningJob(
            bot_id=bot_id,
            team_id=team_id,
            resource_id=resource.id,
            status="awaiting_approval",
            requested_config=config,
            plan_summary=plan_output[:2000],
            planned_at=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        db.refresh(resource)
        db.refresh(job)
        return {
            "ok": True,
            "job_id": job.id,
            "resource_id": resource.id,
            "plan_output": plan_output,
            "status": "awaiting_approval",
        }
    except Exception as e:
        logger.error("Provisioning job creation failed: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def _do_apply_job(params: Dict[str, Any]) -> Dict[str, Any]:
    """Apply an approved provisioning job (create real resources).

    Called ONLY by Integrations.fleet_provision_approve after the broker
    has validated the contract and passed the policy gate.
    """
    job_id = params.get("job_id")
    if not job_id:
        return {"ok": False, "error": "job_id is required."}

    from src.database import SessionLocal
    from src.models import ProvisioningJob, ProvisionedResource

    db = SessionLocal()
    try:
        job = db.query(ProvisioningJob).filter(ProvisioningJob.id == job_id).first()
        if not job:
            return {"ok": False, "error": f"Job {job_id} not found."}
        if job.status != "awaiting_approval":
            return {"ok": False, "error": f"Job is in status '{job.status}', not 'awaiting_approval'."}

        job.status = "applying"
        job.approved_at = datetime.utcnow()
        resource = db.query(ProvisionedResource).filter(ProvisionedResource.id == job.resource_id).first()
        if resource:
            resource.status = "provisioning"
            resource.approved_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        db.close()
        return {"ok": False, "error": str(e)}

    try:
        config = job.requested_config or {}
        rtype = resource.resource_type if resource else "unknown"

        if rtype == "vm":
            result = _apply_vm(config)
        elif rtype == "git_repo":
            result = _apply_git_repo(config)
        elif rtype == "ml_service":
            result = _apply_ml_service(config)
        else:
            result = {"ok": True, "resource_id": f"stub-{rtype}", "output": f"[STUB] Generic provisioning for {rtype}."}

        now = datetime.utcnow()
        db2 = SessionLocal()
        try:
            j = db2.query(ProvisioningJob).filter(ProvisioningJob.id == job_id).first()
            r = db2.query(ProvisionedResource).filter(ProvisionedResource.id == job.resource_id).first()
            if result["ok"]:
                if j:
                    j.status = "done"
                    j.apply_summary = result.get("output", "")[:2000]
                    j.applied_at = now
                if r:
                    r.status = "active"
                    r.resource_id = result.get("resource_id", "")
                    r.apply_output = result.get("output", "")
                    r.provisioned_at = now
            else:
                if j:
                    j.status = "failed"
                    j.error = result.get("output", "")[:2000]
                if r:
                    r.status = "failed"
            db2.commit()
        finally:
            db2.close()

        return {
            "ok": result["ok"],
            "job_id": job_id,
            "resource_id": result.get("resource_id"),
            "output": result.get("output", ""),
            "status": "done" if result["ok"] else "failed",
        }
    except Exception as e:
        logger.error("Provisioning apply failed for job %s: %s", job_id, e)
        try:
            db3 = SessionLocal()
            j = db3.query(ProvisioningJob).filter(ProvisioningJob.id == job_id).first()
            if j:
                j.status = "failed"
                j.error = str(e)
            db3.commit()
            db3.close()
        except Exception:
            pass
        return {"ok": False, "job_id": job_id, "error": str(e)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Public broker-gated wrappers — use these from API routes and agent code
# ---------------------------------------------------------------------------

async def create_provisioning_job(
    resource_type: str,
    config: Dict,
    bot_id: Optional[int] = None,
    team_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Public broker-gated plan phase.

    Routes through contract → policy (fleet_provision_plan) → integration
    → audit pipeline.  No cloud resources are created in this phase.
    """
    import json
    from src.swarm.broker import CapabilityBroker
    from src.swarm.integrations import Integrations
    from src.swarm.policy import PolicyEngine

    broker = CapabilityBroker(PolicyEngine(), Integrations())
    result = await broker.request("fleet_provision_plan", {
        "resource_type": resource_type,
        "config": config,
        "bot_id": bot_id,
        "team_id": team_id,
        "source": "fleet_api",
    })
    if not result.ok:
        logger.warning("Provisioner: broker blocked fleet_provision_plan for %s: %s", resource_type, result.decision.reason)
        return {"ok": False, "error": result.decision.reason}
    try:
        return json.loads(result.output)
    except Exception:
        return {"ok": True, "raw_output": result.output}


async def approve_provisioning_job(job_id: int) -> Dict[str, Any]:
    """Public broker-gated approve+apply.

    Routes through contract → policy (fleet_provision_approve) → integration
    → audit pipeline.  This phase may create real cloud resources.
    """
    import json
    from src.swarm.broker import CapabilityBroker
    from src.swarm.integrations import Integrations
    from src.swarm.policy import PolicyEngine

    broker = CapabilityBroker(PolicyEngine(), Integrations())
    result = await broker.request("fleet_provision_approve", {
        "job_id": job_id,
        "source": "fleet_api",
    })
    if not result.ok:
        logger.warning("Provisioner: broker blocked fleet_provision_approve for job %s: %s", job_id, result.decision.reason)
        return {"ok": False, "error": result.decision.reason}
    try:
        return json.loads(result.output)
    except Exception:
        return {"ok": True, "raw_output": result.output}
