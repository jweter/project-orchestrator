"""Current-user continuous engineering supervisor; no admin Task Scheduler required."""
from __future__ import annotations
import argparse, atexit, json, os, subprocess, sys, time, uuid
from datetime import UTC, datetime
from pathlib import Path
from project_orchestrator.state_io import atomic_json as atomic, optional_atomic_json as optional_atomic

from scripts.capability_slice_contract import derive_slice, derive_slices
from scripts.product_completion_proof import build_completion_proof
from scripts.windows_session_handoff import active_interactive_sessions, boot_owner_should_yield
from scripts.agent_voice import manager_status, worker_status
from scripts.blocker_buddy import diagnose as diagnose_blocker
from scripts.autonomous_slice_builder import ensure_workspace
from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
from project_orchestrator.runner_registry import DEFAULT_RUNNERS, RunnerRegistry
from project_orchestrator.execution_runtime import require_runtime_capabilities
from project_orchestrator.runtime_leases import RuntimeLeasePool, DEFAULT_RUNTIME_CAPACITIES
from project_orchestrator.grant_lifecycle import (
    acquire_lifecycle_lock,
    release_lifecycle_lock,
)
from project_orchestrator.grant_issuance import (
    acquire_grant_issue_lock as _acquire_grant_issue_lock,
    conflicting_nonterminal_grant as _conflicting_nonterminal_grant,
    finalize_unclaimed_grant,
    release_grant_issue_lock,
    reserve_execution_grant,
)
from project_orchestrator import recovery_manager as _recovery
from project_orchestrator import planning_dispatch as _planning
from project_orchestrator import parallel_runtime as _parallel_runtime
from project_orchestrator import telemetry_continuation as _telemetry
from project_orchestrator import activation_capability as _authority
from project_orchestrator import supervisor_runtime as _supervisor_runtime
from project_orchestrator.frontier_replenishment import replenish_frontiers, reconcile_issue_cycle
from project_orchestrator.human_ownership import human_mode_active


RUNTIME_LEASE_STATE=Path(os.environ.get("EXPONENTIAL_ENGINE_STATE",Path.home()/".exponential-engine"/"state"))/"runtime-leases.json"
SUPERVISOR_STATE_REPLACE_ATTEMPTS=60

def _acquire_lifecycle_lock(path:Path):
    """Compatibility facade over the extracted lifecycle lock authority."""
    return acquire_lifecycle_lock(path)


def _release_lifecycle_lock(handle)->None:
    """Compatibility facade over the extracted lifecycle lock authority."""
    release_lifecycle_lock(handle)


def load_runtime_leases_with_recovery(state_path:Path=RUNTIME_LEASE_STATE)->tuple[RuntimeLeasePool,dict|None]:
    """Self-heal invalid lease authority once, but never displace a provable live owner."""
    try:
        return RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES,state_path),None
    except RuntimeError as exc:
        if str(exc)!="RUNTIME_LEASE_STATE_INVALID":
            raise
    recovery=RuntimeLeasePool.recover_invalid_state(DEFAULT_RUNTIME_CAPACITIES,state_path)
    pool=RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES,state_path)
    return pool,recovery

RUNTIME_LEASES,RUNTIME_LEASE_RECOVERY=load_runtime_leases_with_recovery()

def start_merge_watcher(repo_root:Path,state_root:Path,poll_seconds:int)->subprocess.Popen:
    return subprocess.Popen([sys.executable,str(repo_root/"scripts"/"green_pr_watch_loop.py"),
        "--state",str(state_root/"green-pr-watcher.json"),"--poll-seconds",str(poll_seconds)],
        cwd=repo_root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def run_linear_reconciliation(repo_root:Path,state_root:Path)->int|None:
    bindings=state_root/"linear-bindings.json"
    if not bindings.exists():
        return None
    p=subprocess.run([sys.executable,str(repo_root/"scripts"/"linear_reconciliation_worker.py"),
        "--bindings",str(bindings),"--output",str(state_root/"linear-mutations.json")],
        cwd=repo_root,text=True,capture_output=True,check=False)
    (state_root/"linear-last-output.txt").write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    return p.returncode

def builder_health(state_root:Path,returncode:int,expected_invocation_id:str|None=None)->tuple[bool,str]:
    return _supervisor_runtime.builder_health(state_root,returncode,expected_invocation_id)


def observe_live_heads(repo_root:Path,workspace_root:Path,state_root:Path)->tuple[bool,str]:
    return _authority.observe_live_heads(repo_root,workspace_root,state_root,subprocess.run)

def evolution_authorized(repo_root:Path,workspace_root:Path,state_root:Path)->tuple[bool,str,list[str],list[str]]:
    return _authority.evolution_authorized(
        repo_root,workspace_root,state_root,
        observe_fn=observe_live_heads,run_fn=subprocess.run,
    )

def refresh_readiness(repo_root:Path,workspace_root:Path,state_root:Path)->tuple[bool,str]:
    return _authority.refresh_readiness(repo_root,workspace_root,state_root,subprocess.run)

def recertify_product(repo_root:Path,workspace_root:Path,state_root:Path,product:str)->tuple[bool,str]:
    return _authority.recertify_product(
        repo_root,workspace_root,state_root,product,
        merge_fn=merge_product_recertification,run_fn=subprocess.run,
    )

def merge_product_recertification(state_root:Path,product:str,partial:dict)->tuple[bool,str]:
    return _authority.merge_product_recertification(state_root,product,partial)

def request_immediate_builder(state_root:Path)->None:
    _authority.request_immediate_builder(state_root)

def reconcile_verified_merge(state_root:Path,event:dict)->dict:
    return _authority.reconcile_verified_merge(state_root,event)

def reconcile_capability_from_slice_evidence(state_root:Path,repo:str,capability_id:str)->dict:
    return _authority.reconcile_capability_from_slice_evidence(state_root,repo,capability_id)

def reconcile_merge_feedback(state_root:Path,merged:list[dict])->list[dict]:
    return _authority.reconcile_merge_feedback(
        state_root,merged,
        slice_fn=reconcile_verified_merge,
        capability_fn=reconcile_capability_from_slice_evidence,
    )

def merge_watcher_advanced(state_root:Path)->bool:
    return _authority.merge_watcher_advanced(
        state_root,reconcile_fn=reconcile_merge_feedback,
    )

def reconcile_product_completion(state_root:Path,repo:str,exact_head:str,payload:dict)->dict:
    return _authority.reconcile_product_completion(
        state_root,repo,exact_head,payload,
        derive_fn=derive_slice,proof_fn=build_completion_proof,
    )

NO_OP_EXHAUSTION_THRESHOLD=_authority.NO_OP_EXHAUSTION_THRESHOLD
NO_OP_TERMINAL_STATUSES=_authority.NO_OP_TERMINAL_STATUSES

def execution_slice_authority_digest(execution_slice:dict)->str:
    return _authority.execution_slice_authority_digest(execution_slice)

def repeated_noop_slice_evidence(state_root:Path,repo:str,execution_slice:dict,
                                 threshold:int=NO_OP_EXHAUSTION_THRESHOLD)->dict:
    return _authority.repeated_noop_slice_evidence(state_root,repo,execution_slice,threshold)

def record_exhausted_slice_direction(state_root:Path,repo:str,exact_head:str,
                                     execution_slice:dict,evidence:dict)->None:
    _authority.record_exhausted_slice_direction(
        state_root,repo,exact_head,execution_slice,evidence,
    )

def load_execution_slice(state_root:Path,repo:str,exact_head:str)->dict:
    return _authority.load_execution_slice(
        state_root,repo,exact_head,
        derive_fn=derive_slice,
        reconcile_completion_fn=reconcile_product_completion,
    )

def direction_request_outbox(state_root:Path)->list[dict]:
    return _authority.direction_request_outbox(state_root)

def publish_direction_outbox(state_root:Path)->None:
    _authority.publish_direction_outbox(state_root)

def reserved_quarantine_repositories(state_root:Path)->set[str]:
    return _planning.reserved_quarantine_repositories(state_root)


def refresh_decision_learning(state_root:Path)->dict:
    return _supervisor_runtime.refresh_decision_learning(state_root)



def decision_learning_hint(state_root:Path,decision_class:str)->dict:
    return _planning.decision_learning_hint(state_root,decision_class)



def select_execution_target(state_root:Path,activation:dict,products:list[str])->tuple[str,str]:
    policy_path=Path(__file__).resolve().parents[1]/"engineering"/"product-usability-gates.json"
    try:
        usability_policy=json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError):
        usability_policy=None
    return _planning.select_execution_target(
        state_root,activation,products,
        load_execution_slice_fn=load_execution_slice,
        conflict_check_fn=conflicting_nonterminal_grant,
        usability_policy=usability_policy,
    )


ISSUE_LEASE_SECONDS=_recovery.ISSUE_LEASE_SECONDS
RECOVERY_GENERATION_LEASE_SECONDS=_recovery.RECOVERY_GENERATION_LEASE_SECONDS
CLAIM_LEASE_SECONDS=_recovery.CLAIM_LEASE_SECONDS
UNKNOWN_LIVENESS_MAX_CHECKS=_recovery.UNKNOWN_LIVENESS_MAX_CHECKS

def process_start_fingerprint(pid:int|None=None)->str|None:
    return _recovery.process_start_fingerprint(pid)

def worker_liveness(worker:dict|None)->str:
    return _recovery.worker_liveness(worker)

def recover_stale_issued_grants(state_root:Path,now:datetime|None=None)->list[str]:
    return _recovery.recover_stale_issued_grants(state_root,now,worker_liveness)

def recover_stale_recovery_generations(state_root:Path,now:datetime|None=None,liveness_probe=None)->list[str]:
    return _recovery.recover_stale_recovery_generations(
        state_root,now,liveness_probe or worker_liveness,
    )

def recover_stale_grant_claims(state_root:Path,now:datetime|None=None,liveness_probe=None)->list[str]:
    return _recovery.recover_stale_grant_claims(
        state_root,now,liveness_probe or worker_liveness,
    )

def recheck_quarantined_claims(state_root:Path,now:datetime|None=None,liveness_probe=None)->dict[str,str]:
    return _recovery.recheck_quarantined_claims(
        state_root,now,liveness_probe or worker_liveness,
    )

def quarantined_grants(state_root:Path)->dict[str,dict]:
    return _recovery.quarantined_grants(state_root)

def slice_paths_conflict(left:dict,right:dict)->bool:
    return _planning.slice_paths_conflict(left,right)


def select_conflict_free_slices(slices:list[dict],limit:int|None=None)->list[dict]:
    return _planning.select_conflict_free_slices(slices,limit)


def task_outcome_priority(state_root:Path,task:dict)->tuple[float,int,str]:
    return _planning.task_outcome_priority(state_root,task)


def planner_decision_id(product:str,repo:str,head:str,task:dict)->str:
    return _planning.planner_decision_id(product,repo,head,task)


def persist_planner_decision(state_root:Path,product:str,candidates:list,selected:list)->None:
    _planning.persist_planner_decision(state_root,product,candidates,selected)


def ready_task_batch(state_root:Path,activation:dict,product:str,limit:int)->list[tuple[str,str,dict]]:
    return _planning.ready_task_batch(
        state_root,activation,product,limit,
        derive_slices_fn=derive_slices,
        conflict_check_fn=conflicting_nonterminal_grant,
    )


def conflicting_nonterminal_grant(state_root:Path,repo:str,execution_slice:dict)->dict|None:
    return _conflicting_nonterminal_grant(state_root,repo,execution_slice,slice_paths_conflict)



def acquire_supervisor_instance_lock(state_root:Path,machine_lock_root:Path|None=None)->dict|None:
    return _supervisor_runtime.acquire_supervisor_instance_lock(
        state_root,machine_lock_root=machine_lock_root,
        fingerprint_fn=process_start_fingerprint,
    )


def release_supervisor_instance_lock(lock:dict)->None:
    _supervisor_runtime.release_supervisor_instance_lock(lock)


def acquire_grant_issue_lock(state_root:Path)->dict:
    return _acquire_grant_issue_lock(state_root,process_start_fingerprint())

def write_execution_grant(repo_root:Path,state_root:Path,products:list[str],
                          planned_target:tuple[str,str,dict]|None=None)->Path:
    return _supervisor_runtime.write_execution_grant(
        repo_root,state_root,products,planned_target,
        acquire_issue_lock_fn=acquire_grant_issue_lock,
        release_issue_lock_fn=release_grant_issue_lock,
        recover_stale_issued_fn=recover_stale_issued_grants,
        select_target_fn=select_execution_target,
        load_slice_fn=load_execution_slice,
        planner_id_fn=planner_decision_id,
        conflict_fn=conflicting_nonterminal_grant,
        select_runner_fn=select_task_runner,
        runtime_for_task_fn=runtime_for_task,
        reserve_grant_fn=reserve_execution_grant,
        paths_conflict_fn=slice_paths_conflict,
    )


def mark_builder_running(builder_state:Path,builder_output:Path,*,invocation_id:str,
                         grant_path:Path,worker_id:str|None,runner_id:str,runtime_id:str)->dict:
    return _supervisor_runtime.mark_builder_running(
        builder_state,builder_output,invocation_id=invocation_id,
        grant_path=grant_path,worker_id=worker_id,runner_id=runner_id,runtime_id=runtime_id,
    )


def run_builder(repo_root:Path,workspace_root:Path,state_root:Path,products:list[str]|None=None,
                worker_id:str|None=None,planned_target:tuple[str,str,dict]|None=None)->tuple[int,str|None]:
    return _supervisor_runtime.run_builder(
        repo_root,workspace_root,state_root,products,worker_id,planned_target,
        write_grant_fn=write_execution_grant,
        runtime_lease_state=RUNTIME_LEASES.state_path,
        finalize_fn=finalize_unclaimed_grant,
        run_fn=subprocess.run,
        sleep_fn=time.sleep,
        mark_running_fn=mark_builder_running,
    )


def run_builder_for_product(repo_root:Path,workspace_root:Path,state_root:Path,product:str,
                            worker_id:str|None=None,planned_target:tuple[str,str,dict]|None=None)->tuple[int,str|None]:
    return _supervisor_runtime.run_builder_for_product(
        repo_root,workspace_root,state_root,product,worker_id,planned_target,
        run_builder_fn=run_builder,
    )



def harvest_parallel_builders(pool:ParallelWorkerPool,state_root:Path)->dict:
    return _supervisor_runtime.harvest_parallel_builders(
        pool,state_root,health_fn=builder_health,
    )



def runner_requirements_for_task(task:dict)->set[str]:
    return _parallel_runtime.runner_requirements_for_task(task)


def select_task_runner(task:dict,registry:RunnerRegistry=DEFAULT_RUNNERS,
                       running:dict[str,int]|None=None)->str:
    return _parallel_runtime.select_task_runner(task,registry,running)


def runtime_for_task(task:dict)->str:
    return _parallel_runtime.runtime_for_task(task,require_runtime_capabilities)


def resource_leases_for_task(task:dict,runtime_id:str)->tuple[str,...]:
    return _parallel_runtime.resource_leases_for_task(task,runtime_id)


def runner_verified_history(state_root:Path)->dict[str,dict[str,int]]:
    return _parallel_runtime.runner_verified_history(state_root)


def task_affinity_class(task:dict)->str:
    return _planning.task_affinity_class(task)


def runner_affinity_history(state_root:Path,task_class:str)->dict[str,dict[str,int]]:
    return _parallel_runtime.runner_affinity_history(state_root,task_class)


def runner_productivity_history(state_root:Path,task_class:str|None=None)->dict[str,dict[str,float]]:
    return _planning.runner_productivity_history(state_root,task_class)


def planner_decision_quality(state_root:Path)->dict[str,dict[str,float]]:
    return _telemetry.planner_decision_quality(state_root)


def reconcile_dashboard_ci(state_root:Path,workspace:Path)->dict:
    return _telemetry.reconcile_dashboard_ci(state_root,workspace,subprocess.run)


def write_dashboard_snapshot(state_root:Path)->dict:
    return _telemetry.write_dashboard_snapshot(state_root)


def productivity_penalty(runner_id:str,stats:dict[str,dict[str,float]])->float:
    return _parallel_runtime.productivity_penalty(runner_id,stats)


def adaptive_runner_score(manifest,history:dict[str,dict[str,int]],running:dict[str,int],
                          affinity:dict[str,dict[str,int]]|None=None,
                          productivity:dict[str,dict[str,float]]|None=None)->tuple[float,int,str]:
    return _parallel_runtime.adaptive_runner_score(manifest,history,running,affinity,productivity)


def runner_capacity_cooldowns(state_root:Path,now:datetime|None=None,cooldown_seconds:int=1800)->set[str]:
    return _parallel_runtime.runner_capacity_cooldowns(state_root,now,cooldown_seconds)


def select_adaptive_runner(task:dict,state_root:Path,registry:RunnerRegistry,
                           running:dict[str,int])->str:
    return _parallel_runtime.select_adaptive_runner(task,state_root,registry,running)


def durable_runner_occupancy(state_root:Path,now:datetime|None=None)->dict[str,int]:
    return _parallel_runtime.durable_runner_occupancy(
        state_root,now,liveness_probe=worker_liveness,
    )


def dispatch_parallel_builders(pool:ParallelWorkerPool,repo_root:Path,workspace_root:Path,
                               state_root:Path,products:list[str],
                               runner_registry:RunnerRegistry|None=None,
                               dispatch_invocation_id:str|None=None)->list[str]:
    return _parallel_runtime.dispatch_parallel_builders(
        pool,repo_root,workspace_root,state_root,products,
        ready_task_batch_fn=ready_task_batch,
        run_builder_for_product_fn=run_builder_for_product,
        runtime_leases=RUNTIME_LEASES,
        runtime_lease_recovery=RUNTIME_LEASE_RECOVERY,
        runner_registry=runner_registry,
        dispatch_invocation_id=dispatch_invocation_id,
    )



from project_orchestrator import workspace_recovery as _workspace_recovery

classify_orphan_branch = _workspace_recovery.classify_orphan_branch
resume_orphan_branch = _workspace_recovery.resume_orphan_branch
builder_artifacts_only = _workspace_recovery.builder_artifacts_only
preserved_work_evidence = _workspace_recovery.preserved_work_evidence

def revalidate_preserved_work(ws:Path,record:dict)->str:
    return _workspace_recovery.revalidate_preserved_work(
        ws,record,evidence_fn=preserved_work_evidence,
    )
issue_preserved_recovery_capability = _workspace_recovery.issue_preserved_recovery_capability
reconcile_consumed_recovery = _workspace_recovery.reconcile_consumed_recovery
reconcile_consumed_staged_work = _workspace_recovery.reconcile_consumed_staged_work
issue_staged_commit_capability = _workspace_recovery.issue_staged_commit_capability
pending_recovery_grants = _workspace_recovery.pending_recovery_grants
verify_durable_pr = _workspace_recovery.verify_durable_pr

def begin_recovery_generation(path:Path,grant_id:str,expected:dict)->tuple[str,dict]|None:
    return _workspace_recovery.begin_recovery_generation(
        path,grant_id,expected,
        liveness_probe=worker_liveness,
        fingerprint_probe=process_start_fingerprint,
    )

commit_recovery_record = _workspace_recovery.commit_recovery_record
reconcile_abandoned_claims = _workspace_recovery.reconcile_abandoned_claims

def validate_preserved_work(ws:Path,record:dict)->str:
    return _workspace_recovery.validate_preserved_work(ws,record,revalidate_preserved_work)

def consume_preserved_recovery_capability(ws:Path,record:dict)->str:
    return _workspace_recovery.consume_preserved_recovery_capability(ws,record,revalidate_preserved_work)

def commit_preserved_work(ws:Path,record:dict)->str:
    return _workspace_recovery.commit_preserved_work(ws,record,consume_preserved_recovery_capability)

def commit_reconciled_staged_work(ws:Path,record:dict)->str:
    return _workspace_recovery.commit_reconciled_staged_work(ws,record,reconcile_consumed_staged_work)

def recheck_preserved_work_quarantines(workspace_root:Path,state_root:Path)->dict[str,str]:
    return _workspace_recovery.recheck_preserved_work_quarantines(
        workspace_root,state_root,
        revalidate_fn=revalidate_preserved_work,
        validate_fn=validate_preserved_work,
        commit_fn=commit_preserved_work,
        reconcile_consumed_fn=reconcile_consumed_recovery,
        reconcile_staged_fn=reconcile_consumed_staged_work,
        issue_staged_fn=issue_staged_commit_capability,
        staged_commit_fn=commit_reconciled_staged_work,
    )

def productive_liveness(state_root:Path,authorized_products:list[str],now:datetime|None=None,stale_seconds:int=900,
                        builder_status:str|None=None)->dict:
    return _planning.productive_liveness(state_root,authorized_products,now,stale_seconds,builder_status)


def product_convergence(state_root:Path,authorized_products:list[str],*,current_invocation_id:str|None=None)->dict:
    return _planning.product_convergence(
        state_root,authorized_products,current_invocation_id=current_invocation_id,
    )


def request_liveness_reacquire(state_root:Path,liveness:dict)->dict:
    return _planning.request_liveness_reacquire(state_root,liveness,request_immediate_builder)


def record_async_gate_rotation(state_root:Path,*,blocked_repository:str|None,blocked_status:str,
                               next_repository:str|None,burst_id:str|None,process_instance_id:str|None)->dict:
    return _telemetry.record_async_gate_rotation(
        state_root,blocked_repository=blocked_repository,blocked_status=blocked_status,
        next_repository=next_repository,burst_id=burst_id,process_instance_id=process_instance_id,
    )


def record_burst_repository_pass(state_root:Path,*,repository:str|None,builder_status:str,
                                 burst_id:str,process_instance_id:str,sequence:int)->dict:
    return _telemetry.record_burst_repository_pass(
        state_root,repository=repository,builder_status=builder_status,
        burst_id=burst_id,process_instance_id=process_instance_id,sequence=sequence,
    )


PRODUCT_BY_REPOSITORY=_planning.PRODUCT_BY_REPOSITORY


def product_for_repository(repository:str|None)->str|None:
    return _planning.product_for_repository(repository)


def record_product_identity_advancement(state_root:Path,*,repository:str|None,builder_status:str,lifecycle_advanced:bool,
                                        burst_id:str,process_instance_id:str,sequence:int)->dict:
    return _telemetry.record_product_identity_advancement(
        state_root,repository=repository,builder_status=builder_status,
        lifecycle_advanced=lifecycle_advanced,burst_id=burst_id,
        process_instance_id=process_instance_id,sequence=sequence,
    )


def selected_builder_repository(state_root:Path,expected_invocation_id:str|None)->str|None:
    return _telemetry.selected_builder_repository(state_root,expected_invocation_id)


def maybe_record_async_rotation(state_root:Path,*,previous_repository:str|None,previous_status:str|None,
                                current_repository:str|None,burst_id:str|None,process_instance_id:str|None)->dict|None:
    return _telemetry.maybe_record_async_rotation(
        state_root,previous_repository=previous_repository,previous_status=previous_status,
        current_repository=current_repository,burst_id=burst_id,process_instance_id=process_instance_id,
    )


def continuation_delay(*,lifecycle_advanced:bool,liveness_reacquire:dict,builder_ok:bool,builder_status:str,normal_seconds:int)->int:
    return _telemetry.continuation_delay(
        lifecycle_advanced=lifecycle_advanced,liveness_reacquire=liveness_reacquire,
        builder_ok=builder_ok,builder_status=builder_status,normal_seconds=normal_seconds,
    )


def reconcile_continuation_journal(state_root:Path)->dict:
    return _telemetry.reconcile_continuation_journal(state_root)


def record_continuation_pass(state_root:Path,*,builder_status:str,delay_seconds:int,lifecycle_advanced:bool,liveness_reacquire:dict,burst_id:str|None=None,process_instance_id:str|None=None)->dict:
    return _telemetry.record_continuation_pass(
        state_root,builder_status=builder_status,delay_seconds=delay_seconds,
        lifecycle_advanced=lifecycle_advanced,liveness_reacquire=liveness_reacquire,
        burst_id=burst_id,process_instance_id=process_instance_id,
    )


def prove_restart_takeover(state_root:Path,instance_lock:dict,burst:dict)->dict:
    return _supervisor_runtime.prove_restart_takeover(state_root,instance_lock,burst)


def start_or_resume_burst(state_root:Path,*,process_instance_id:str|None=None,
                          now:datetime|None=None,resume_seconds:int=900)->dict:
    return _supervisor_runtime.start_or_resume_burst(
        state_root,process_instance_id=process_instance_id,
        now=now,resume_seconds=resume_seconds,
    )


def actor_revision(repo:Path)->str|None:
    return subprocess.run(["git","rev-parse","HEAD"],cwd=repo,text=True,capture_output=True,check=False).stdout.strip() or None

def publish_generation_heartbeat(state:Path,*,process_instance_id:str,burst_id:str,
                                 actor_revision:str|None,status:str="RUNNING_CURRENT")->None:
    atomic(state/"supervisor-generation-heartbeat.json",{
        "schema_version":1,"status":status,
        "process_instance_id":process_instance_id,"burst_id":burst_id,
        "actor_revision":actor_revision,
        "heartbeat_at_utc":datetime.now(UTC).isoformat(),
    })


def publish_supervisor_snapshot(state:Path,payload:dict,*,process_instance_id:str,
                                burst_id:str,revision:str|None)->bool:
    """Keep the generation alive when an external Windows reader blocks replacement."""
    try:
        atomic(state/"supervisor.json",payload,
               replace_attempts=SUPERVISOR_STATE_REPLACE_ATTEMPTS)
    except PermissionError as exc:
        publish_generation_heartbeat(state,process_instance_id=process_instance_id,
            burst_id=burst_id,actor_revision=revision,status="STATE_PUBLICATION_BLOCKED")
        atomic(state/"supervisor-publication-blocked.json",{
            "schema_version":1,"status":"STATE_PUBLICATION_BLOCKED",
            "process_instance_id":process_instance_id,"burst_id":burst_id,
            "observed_at_utc":datetime.now(UTC).isoformat(),"error":str(exc),
        })
        return False
    publish_generation_heartbeat(state,process_instance_id=process_instance_id,
        burst_id=burst_id,actor_revision=revision)
    return True

def generation_handoff_status(state:Path,*,process_instance_id:str,worker_pool:ParallelWorkerPool)->str|None:
    request={}
    try:
        request=json.loads((state/"supervisor-takeover-request.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError):
        pass
    if (request.get("status")!="TAKEOVER_REQUESTED"
            or request.get("predecessor_process_instance_id")!=process_instance_id):
        return None
    active_workers=worker_pool.snapshot()["running_total"]
    status="WAITING_FOR_WORKERS" if active_workers else "DRAINING_FOR_TAKEOVER"
    atomic(state/"supervisor-generation-handoff.json",{
        "schema_version":1,"status":status,"process_instance_id":process_instance_id,
        "active_workers":active_workers,"observed_at_utc":datetime.now(UTC).isoformat(),
    })
    return status

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--repo-root",required=True); p.add_argument("--workspace-root",required=True)
    p.add_argument("--state-root",required=True); p.add_argument("--builder-seconds",type=int,default=300)
    p.add_argument("--merge-seconds",type=int,default=60); p.add_argument("--once",action="store_true")
    p.add_argument("--yield-on-interactive-session",action="store_true",
                   help="SYSTEM cold-boot owner exits when an interactive user session appears")
    a=p.parse_args(); repo=Path(a.repo_root); workspace=Path(a.workspace_root); state=Path(a.state_root)
    state.mkdir(parents=True,exist_ok=True)
    if human_mode_active(state):
        atomic(state/"supervisor-human-ownership.json",{
            "schema_version":1,"status":"HUMAN_MODE_ACTIVE",
            "observed_at_utc":datetime.now(UTC).isoformat(),
        })
        return 0
    instance_lock=acquire_supervisor_instance_lock(state,RUNTIME_LEASE_STATE.parent)
    if instance_lock is None:
        owner=None
        try:
            owner=json.loads((state/"supervisor-instance.lock.json").read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError,TypeError):
            pass
        atomic(state/"supervisor-reentry.json",{"schema_version":2,
            "status":"SUPERVISOR_INSTANCE_ALREADY_ACTIVE",
            "observed_at_utc":datetime.now(UTC).isoformat(),
            "active_process_instance_id":((owner or {}).get("worker") or {}).get("instance_id"),
            "active_worker":(owner or {}).get("worker")})
        return 0
    atexit.register(release_supervisor_instance_lock,instance_lock)
    merge=None
    def cleanup_merge_watcher()->None:
        if merge is not None and merge.poll() is None:
            merge.terminate()
            try:
                merge.wait(timeout=5)
            except subprocess.TimeoutExpired:
                merge.kill()
    atexit.register(cleanup_merge_watcher)
    process_instance_id=instance_lock["worker"]["instance_id"]
    burst=start_or_resume_burst(state,process_instance_id=process_instance_id)
    takeover_proof=prove_restart_takeover(state,instance_lock,burst)
    if takeover_proof["status"]=="RESTART_IDENTITY_COLLISION":
        release_supervisor_instance_lock(instance_lock)
        return 2
    atomic(state/"supervisor-restart-takeover.json",takeover_proof)
    atomic(state/"supervisor-process-instance.json",{"schema_version":1,
        "process_instance_id":process_instance_id,"burst_id":burst["burst_id"],
        "resumed":burst["resumed"],"previous_process_instance_id":burst.get("previous_process_instance_id"),
        "takeover_status":takeover_proof["status"],
        "worker":instance_lock["worker"],"started_at_utc":datetime.now(UTC).isoformat()})
    burst_id=burst["burst_id"]; burst_pass=burst["burst_pass"]
    loaded_actor_revision=actor_revision(repo)
    try: burst_proof=json.loads((state/"autonomous-burst-proof.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError): burst_proof={}
    try: product_proof=json.loads((state/"portfolio-product-advancement-proof.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError): product_proof={}
    previous_repository=previous_builder_status=rotation_evidence=None
    worker_pool=ParallelWorkerPool(max_workers_per_product=2,max_total_workers=6); atexit.register(worker_pool.shutdown,wait=False)
    dead_builder_reconciliation=_supervisor_runtime.reconcile_dead_builder_states(state); workspace_provisioning=_authority.ensure_portfolio_workspaces(repo,workspace,state,ensure_workspace_fn=ensure_workspace)
    progress_sequence=0
    while True:
        progress_sequence+=1
        revision_rollover=_supervisor_runtime.prepare_actor_revision_rollover(repo,workspace,state,loaded_actor_revision=loaded_actor_revision,current_actor_revision=actor_revision(repo),process_instance_id=process_instance_id,worker_pool=worker_pool,builder_seconds=a.builder_seconds,merge_seconds=a.merge_seconds,yield_on_interactive_session=a.yield_on_interactive_session)
        if _supervisor_runtime.guard_generation_iteration(state,process_instance_id=process_instance_id,burst_id=burst_id,actor_revision=loaded_actor_revision,sequence=progress_sequence,worker_pool=worker_pool,heartbeat_fn=publish_generation_heartbeat,handoff_fn=generation_handoff_status): return 0
        if human_mode_active(state):
            atomic(state/"supervisor-human-ownership.json",{
                "schema_version":1,"status":"HUMAN_MODE_ACTIVE",
                "process_instance_id":process_instance_id,
                "observed_at_utc":datetime.now(UTC).isoformat(),
            })
            if merge is not None and merge.poll() is None:
                merge.terminate()
            return 0
        if a.yield_on_interactive_session:
            try:
                sessions=active_interactive_sessions()
                should_yield=boot_owner_should_yield(sessions)
                session_status="INTERACTIVE_SESSION_PRESENT" if should_yield else "NO_INTERACTIVE_SESSION"
            except OSError as exc:
                # Fail closed: a SYSTEM owner that cannot prove session absence
                # must not retain mutation authority.
                sessions=[]
                should_yield=True
                session_status=f"SESSION_DETECTION_FAILED:{exc}"
            atomic(state/"supervisor-session-handoff.json",{
                "schema_version":1,"status":session_status,"sessions":sessions,
                "process_instance_id":process_instance_id,
                "observed_at_utc":datetime.now(UTC).isoformat()})
            if should_yield:
                if merge is not None and merge.poll() is None:
                    merge.terminate()
                return 0
        burst_pass+=1
        if merge is None or merge.poll() is not None:
            merge=start_merge_watcher(repo,state,a.merge_seconds)
        linear_rc=run_linear_reconciliation(repo,state)
        lifecycle_recovery=_supervisor_runtime.run_lifecycle_recovery_pass(state,workspace,
            recover_generations_fn=recover_stale_recovery_generations,recover_claims_fn=recover_stale_grant_claims,
            pending_fn=pending_recovery_grants,preserved_fn=recheck_preserved_work_quarantines,quarantined_fn=recheck_quarantined_claims)
        released_recovery_generations,abandoned_grants,recovery_queue=(lifecycle_recovery["released"],lifecycle_recovery["abandoned"],lifecycle_recovery["queue"])
        recovery_results={}; preserved_work_rechecks=lifecycle_recovery["preserved"]; quarantine_rechecks=lifecycle_recovery["quarantine_rechecks"]
        newly_released=[g for g,outcome in quarantine_rechecks.items() if outcome=="RELEASED_TO_RECOVERY"]
        quarantines=quarantined_grants(state)
        publish_direction_outbox(state)
        dead_builder_reconciliation=_supervisor_runtime.reconcile_dead_builder_states(state)
        workspace_provisioning=_authority.ensure_portfolio_workspaces(repo,workspace,state,ensure_workspace_fn=ensure_workspace)
        authorized,activation_reason,products,stale_products=evolution_authorized(repo,workspace,state)
        if stale_products:
            for product in stale_products:
                recertify_product(repo,workspace,state,product)
            authorized,activation_reason,products,stale_products=evolution_authorized(repo,workspace,state)
        if not authorized and (activation_reason=="ACTIVATION_EVIDENCE_MISSING" or stale_products):
            refreshed,refresh_reason=refresh_readiness(repo,workspace,state)
            if refreshed:
                authorized,activation_reason,products,stale_products=evolution_authorized(repo,workspace,state)
            else:
                activation_reason=refresh_reason
        products=products if authorized else []
        # Exclusive focus must not silently dispatch product work when engine authority is absent.
        if (state / 'iron-god-exclusive.json').is_file():
            products = ['iron-god'] if 'iron-god' in products else []
        capability_authority_compilation=_authority.compile_runtime_capability_authority(workspace,state) if products else {"schema_version":1,"repositories":{}}
        issue_frontier=reconcile_issue_cycle(state) if products else {"status":"NO_AUTHORIZED_PRODUCTS"}; frontier_replenishment=replenish_frontiers(workspace,state,compile_fn=_authority.compile_runtime_capability_authority,load_slice_fn=load_execution_slice) if products else {"status":"NO_AUTHORIZED_PRODUCTS"}
        parallel_results=harvest_parallel_builders(worker_pool,state)
        # Harvest first, then reclaim dead-owner leases plus only this supervisor
        # generation's leases whose worker slots are no longer active. Generation
        # fencing prevents revoking legitimate work inherited across a restart.
        runtime_lease_reconciliation=_supervisor_runtime.reconcile_runtime_leases(
            RUNTIME_LEASES,worker_pool
        )
        dispatch_invocation_id=uuid.uuid4().hex if products else None
        dispatched_workers=dispatch_parallel_builders(
            worker_pool,repo,workspace,state,products,
            dispatch_invocation_id=dispatch_invocation_id
        ) if products and not revision_rollover["requested"] else []
        # Historical abandoned-work recovery is idle-capacity work. Never make
        # an active or newly dispatched product lane wait behind backlog cleanup.
        if not revision_rollover["requested"] and not dispatched_workers and worker_pool.snapshot()["running_total"]==0:
            recovery_results.update(reconcile_abandoned_claims(workspace,state,recovery_queue))
            if newly_released:
                recovery_results.update(reconcile_abandoned_claims(workspace,state,newly_released))
        elif recovery_queue or newly_released:
            recovery_results["deferred"]={
                "status":"DEFERRED_ACTIVE_PRODUCT_WORK",
                "queued":len(recovery_queue)+len(newly_released),
            }
        # A completed nonproductive worker has already released its slot. Refill
        # immediately in the same burst pass instead of waiting for the next loop.
        # This turns ownership/no-work/exception outcomes into routing feedback.
        if products and not revision_rollover["requested"] and parallel_results and any(not row.get("healthy",False) for row in parallel_results.values()):
            dispatch_invocation_id=uuid.uuid4().hex
            dispatched_workers.extend(
                dispatch_parallel_builders(worker_pool,repo,workspace,state,products,
                                           dispatch_invocation_id=dispatch_invocation_id)
            )
        pool_snapshot=worker_pool.snapshot()
        # Persist authoritative post-harvest/post-dispatch pool so completed futures cannot remain as phantom RUNNING workers.
        atomic(state/"parallel-worker-pool.json",{
            "schema_version":2,"pool":pool_snapshot,"runtime_leases":RUNTIME_LEASES.snapshot(),
            "runtime_lease_reconciliation":runtime_lease_reconciliation,
            "dispatched":dispatched_workers,"dispatch_invocation_id":dispatch_invocation_id,
            "runtime_lease_recovery":RUNTIME_LEASE_RECOVERY,
            "recorded_at_utc":datetime.now(UTC).isoformat(),
        })
        # The supervisor no longer blocks on implementation. A worker holding a
        # repository grant is active work; other product slots remain schedulable.
        rc=0 if (dispatched_workers or pool_snapshot["running_total"] or parallel_results) else 3
        builder_invocation_id=None
        current_repository=None
        if parallel_results:
            completed_rows=list(parallel_results.values())
            latest_row=completed_rows[-1]
            builder_status=latest_row["status"]
            builder_ok=all(row.get("healthy",False) for row in completed_rows)
            repository=latest_row.get("repository")
            if isinstance(repository,str) and repository:
                current_repository=repository
        elif pool_snapshot["running_total"]:
            builder_status="PARALLEL_WORKERS_ACTIVE"
            builder_ok=True
        else:
            builder_status="NO_READY_EXECUTION_SLICE"
            builder_ok=False
        if current_repository is None:
            current_repository=selected_builder_repository(state,builder_invocation_id)
        rotation_evidence=maybe_record_async_rotation(state,previous_repository=previous_repository,
            previous_status=previous_builder_status,current_repository=current_repository,
            burst_id=burst_id,process_instance_id=process_instance_id)
        previous_repository=current_repository
        previous_builder_status=builder_status
        lifecycle_advanced=merge_watcher_advanced(state); decision_learning=refresh_decision_learning(state)
        if lifecycle_advanced:
            request_immediate_builder(state)
        liveness=productive_liveness(state,products,builder_status=builder_status)
        liveness_published=optional_atomic(state/"productive-liveness.json",liveness,replace_attempts=SUPERVISOR_STATE_REPLACE_ATTEMPTS)
        convergence=product_convergence(state,products,current_invocation_id=dispatch_invocation_id)
        idle_streaks=_planning.record_eligible_idle_streaks(
            state,products,current_invocation_id=dispatch_invocation_id,
        )
        degraded_reasons=[]
        if lifecycle_recovery["publication_blocked"]: degraded_reasons.append({"reason":"LIFECYCLE_STATE_PUBLICATION_BLOCKED"})
        if not liveness_published: degraded_reasons.append({"reason":"PRODUCTIVE_LIVENESS_PUBLICATION_BLOCKED"})
        if workspace_provisioning.get("status") != "OK":
            degraded_reasons.append({"reason":"WORKSPACE_PROVISIONING_DEGRADED","details":workspace_provisioning})
        if convergence.get("status") != "CONVERGED":
            degraded_reasons.append({"reason":convergence.get("status"),"products":convergence.get("unexplained_eligible_products",[])})
        if idle_streaks.get("status") == "P1_LIVENESS_DEFECT":
            degraded_reasons.append({"reason":"P1_LIVENESS_DEFECT"})
        if liveness.get("status") in {"STALE_ACTIVE_WORK","ELIGIBLE_IDLE","AUTHORIZED_NO_SLICE"}:
            degraded_reasons.append({"reason":liveness.get("status")})
        liveness_reacquire=request_liveness_reacquire(state,liveness)
        if convergence["status"]=="UNEXPLAINED_ELIGIBLE_IDLE":
            request_immediate_builder(state)
            liveness_reacquire={"requested":True,"reason":"PRODUCT_CONVERGENCE_DEFECT",
                "products":convergence["unexplained_eligible_products"],
                "requested_at_utc":datetime.now(UTC).isoformat()}
        if not builder_ok and builder_status!="NO_READY_EXECUTION_SLICE": degraded_reasons.append({"reason":"BUILDER_FAILED","status":builder_status,"returncode":rc})
        if linear_rc not in (None,0): degraded_reasons.append({"reason":"LINEAR_RECONCILIATION_FAILED","returncode":linear_rc})
        if quarantines: degraded_reasons.append({"reason":"QUARANTINED_GRANTS","count":len(quarantines)})
        healthy=not degraded_reasons
        publish_supervisor_snapshot(state,{"updated_at_utc":datetime.now(UTC).isoformat(),
            "status":"HEALTHY" if healthy else "FLOW_DEGRADED","builder_returncode":rc,
            "builder_status":builder_status,
            "linear_reconciliation_returncode":linear_rc,
            "evolution_authorized":authorized,"activation_reason":activation_reason,
            "authorized_products":products,
            "parallel_worker_pool":pool_snapshot,
            "parallel_workers_dispatched":dispatched_workers,
            "parallel_worker_results":parallel_results,
            "productive_liveness":liveness,
            "product_convergence":convergence,
            "eligible_idle_streaks":idle_streaks,
            "workspace_provisioning":workspace_provisioning,
            "dead_builder_reconciliation":dead_builder_reconciliation,
            "degraded_reasons":degraded_reasons,
            "liveness_reacquire":liveness_reacquire,
            "abandoned_grants":abandoned_grants,
            "recovery_queue":recovery_queue,
            "abandoned_recovery":recovery_results,
            "preserved_work_rechecks":preserved_work_rechecks,
            "quarantine_rechecks":quarantine_rechecks,
            "quarantined_grants":quarantines,
            "quarantine_count":len(quarantines),
            "pr_lifecycle_advanced":lifecycle_advanced,
            "merge_watcher_pid":merge.pid,"merge_watcher_running":merge.poll() is None,
            "process_instance_id":process_instance_id,"burst_id":burst_id,
            "autonomous_burst_proof":burst_proof,
            "portfolio_product_advancement_proof":product_proof,
            "async_gate_rotation":rotation_evidence,
            "decision_learning":decision_learning},process_instance_id=process_instance_id,
            burst_id=burst_id,revision=loaded_actor_revision)
        # Presentation-only persona output. Never feeds control decisions.
        (state/"manager-voice.txt").write_text(
            manager_status(healthy,liveness["status"],builder_status)+"\n",encoding="utf-8")
        # Persona diagnostics must use the supervisor-validated status. Details
        # from builder.json are accepted only for this exact invocation.
        builder_payload={"status":builder_status,"reason":""}
        try:
            raw_builder=json.loads((state/"builder.json").read_text(encoding="utf-8"))
            if raw_builder.get("invocation_id")==builder_invocation_id:
                builder_payload["reason"]=str(raw_builder.get("reason",""))
        except (OSError,json.JSONDecodeError,TypeError):
            pass
        worker_detail=builder_payload["reason"]
        (state/"worker-voice.txt").write_text(
            worker_status(builder_status,worker_detail)+"\n",encoding="utf-8")
        buddy=diagnose_blocker(builder_payload,liveness)
        atomic(state/"blocker-buddy.json",buddy)
        (state/"buddy-voice.txt").write_text(buddy["voice"]+"\n",encoding="utf-8")
        if a.once:
            merge.terminate(); return 0 if rc==0 else 1
        # A merge exposes new roadmap work. Do not impose the normal idle delay
        # after lifecycle advancement; re-enter selection immediately.
        delay=continuation_delay(lifecycle_advanced=lifecycle_advanced,
            liveness_reacquire=liveness_reacquire,builder_ok=builder_ok,
            builder_status=builder_status,normal_seconds=a.builder_seconds)
        record=record_continuation_pass(state,builder_status=builder_status,delay_seconds=delay,
            lifecycle_advanced=lifecycle_advanced,liveness_reacquire=liveness_reacquire,burst_id=burst_id,
            process_instance_id=process_instance_id)
        burst_proof=record_burst_repository_pass(state,repository=current_repository,
            builder_status=builder_status,burst_id=burst_id,process_instance_id=process_instance_id,
            sequence=record["sequence"])
        product_proof=record_product_identity_advancement(state,repository=current_repository,
            builder_status=builder_status,lifecycle_advanced=lifecycle_advanced,burst_id=burst_id,
            process_instance_id=process_instance_id,sequence=record["sequence"])
        # Dashboard telemetry is observational only and must never consume or block
        # an execution opportunity. CI reconciliation failure degrades the UI but
        # does not change lifecycle or promotion authority.
        try:
            reconcile_dashboard_ci(state,repo)
        except (OSError,TypeError,subprocess.SubprocessError):
            pass
        try:
            write_dashboard_snapshot(state)
        except (OSError,TypeError,ValueError):
            pass
        record["burst_pass"]=burst_pass
        atomic(state/"continuation-current-burst.json",{"schema_version":2,"burst_id":burst_id,
            "process_instance_id":process_instance_id,
            "previous_process_instance_id":burst.get("previous_process_instance_id"),
            "resumed":burst["resumed"],"pass_count":burst_pass,"last_sequence":record["sequence"],"last_builder_status":builder_status,
            "immediate_continuation":record["immediate_continuation"],"updated_at_utc":datetime.now(UTC).isoformat()})
        time.sleep(delay)
if __name__=="__main__": raise SystemExit(main())

