"""Current-user continuous engineering supervisor; no admin Task Scheduler required."""
from __future__ import annotations
import argparse, atexit, hashlib, json, os, shutil, socket, subprocess, sys, time, uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from project_orchestrator.grant_lifecycle import (
    acquire_lifecycle_lock as _acquire_lifecycle_lock,
    mutate_grant_lifecycle,
    release_lifecycle_lock as _release_lifecycle_lock,
)
from project_orchestrator.state_io import atomic_json as atomic

from scripts.capability_slice_contract import derive_slice, derive_slices
from scripts.product_completion_proof import build_completion_proof
from scripts.windows_session_handoff import active_interactive_sessions, boot_owner_should_yield
from scripts.agent_voice import manager_status, worker_status
from scripts.blocker_buddy import diagnose as diagnose_blocker
from project_orchestrator.decision_outcome import attribute as attribute_decision_outcome
from project_orchestrator.decision_learning import compile_decision_learning, planning_hint
from project_orchestrator.jev_evidence import load_jsonl
from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
from project_orchestrator.runner_registry import DEFAULT_RUNNERS, RunnerRegistry
from project_orchestrator.execution_runtime import require_runtime_capabilities, DEFAULT_EXECUTION_RUNTIMES
from project_orchestrator.runtime_leases import RuntimeLeasePool, DEFAULT_RUNTIME_CAPACITIES
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


RUNTIME_LEASE_STATE=Path(os.environ.get("EXPONENTIAL_ENGINE_STATE",Path.home()/".exponential-engine"/"state"))/"runtime-leases.json"

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
    path=state_root/"builder.json"
    if returncode != 0:
        return False,"PROCESS_FAILURE"
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        status=payload.get("status","UNKNOWN")
    except (OSError,json.JSONDecodeError):
        return False,"STATE_UNREADABLE"
    if expected_invocation_id is not None and payload.get("invocation_id")!=expected_invocation_id:
        return False,"STALE_BUILDER_STATE"
    degraded={"P1_BUILDER_FAILURE","BUILDER_FAILED","PREFLIGHT_FAILED","PUSH_FAILED","PR_CREATE_FAILED"}
    productive={"USER_ACTIVE","IMPLEMENTING","PREFLIGHT_GREEN","PR_OPEN","CI_PENDING","GREEN","MERGED","PRODUCT_REALITY_PENDING","PRODUCT_REALITY_VERIFIED"}
    return status in productive and status not in degraded,status

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
    """Reconcile terminal worker evidence into advisory decision-learning state."""
    ledger=state_root/"telemetry"/"jev-shadow"/"decisions.jsonl"
    records=load_jsonl(ledger)
    try:
        lifecycle=json.loads((state_root/"hands-off-grant-lifecycle.json").read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        lifecycle={}
    rows=[]
    for record in records:
        for grant_id,result in lifecycle.items():
            if not isinstance(result,dict):
                continue
            enriched=dict(result)
            enriched.setdefault("candidate_id",result.get("lane_id") or result.get("slice_id") or grant_id)
            row=attribute_decision_outcome(record,enriched)
            if row:
                rows.append(row)
    learning=compile_decision_learning(rows)
    payload={"schema_version":1,"attributed_outcomes":len(rows),"learning":learning,
             "updated_at_utc":datetime.now(UTC).isoformat()}
    atomic(state_root/"decision-learning.json",payload)
    return payload


def decision_learning_hint(state_root:Path,decision_class:str)->dict:
    return _planning.decision_learning_hint(state_root,decision_class)



def select_execution_target(state_root:Path,activation:dict,products:list[str])->tuple[str,str]:
    return _planning.select_execution_target(
        state_root,activation,products,
        load_execution_slice_fn=load_execution_slice,
        conflict_check_fn=conflicting_nonterminal_grant,
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

def _acquire_os_file_lock(handle)->bool:
    """Acquire a nonblocking kernel-owned one-byte lock."""
    handle.seek(0,os.SEEK_END)
    if handle.tell()==0:
        handle.write(b"0"); handle.flush()
    handle.seek(0)
    try:
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return True
    except (OSError,BlockingIOError):
        return False

def _release_os_file_lock(handle)->None:
    handle.seek(0)
    if os.name=="nt":
        import msvcrt
        msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

def acquire_supervisor_instance_lock(state_root:Path)->dict|None:
    """Allow exactly one physical supervisor process per state root."""
    state_root.mkdir(parents=True,exist_ok=True)
    path=state_root/"supervisor-instance.lock"
    handle=path.open("a+b",buffering=0)
    if not _acquire_os_file_lock(handle):
        handle.close()
        return None
    lock_id=str(uuid.uuid4())
    worker={"host":socket.gethostname(),"pid":os.getpid(),
            "process_start_fingerprint":process_start_fingerprint(),
            "instance_id":uuid.uuid4().hex}
    metadata=state_root/"supervisor-instance.lock.json"
    atomic(metadata,{"schema_version":1,"lock_id":lock_id,"worker":worker,
                     "acquired_at_utc":datetime.now(UTC).isoformat()})
    return {"path":path,"metadata_path":metadata,"handle":handle,"lock_id":lock_id,"worker":worker}

def release_supervisor_instance_lock(lock:dict)->None:
    """Release this process's supervisor fence without deleting another owner's metadata."""
    handle=lock["handle"]; metadata=lock["metadata_path"]
    try:
        if metadata.exists():
            try:
                current=json.loads(metadata.read_text(encoding="utf-8"))
                if current.get("lock_id")==lock.get("lock_id"):
                    metadata.unlink()
            except (OSError,json.JSONDecodeError,TypeError):
                pass
    finally:
        try:
            _release_os_file_lock(handle)
        finally:
            handle.close()

def acquire_grant_issue_lock(state_root:Path)->dict:
    return _acquire_grant_issue_lock(state_root,process_start_fingerprint())

def write_execution_grant(repo_root:Path,state_root:Path,products:list[str],
                          planned_target:tuple[str,str,dict]|None=None)->Path:
    """Issue one exact-head task grant, optionally from a preplanned task batch."""
    lock=acquire_grant_issue_lock(state_root)
    try:
        activation_path=state_root/"hands-off-activation.json"
        activation_bytes=activation_path.read_bytes()
        activation=json.loads(activation_bytes)
        contract_path=repo_root/"engineering"/"hands-off-evolution.json"
        contract_bytes=contract_path.read_bytes()
        recover_stale_issued_grants(state_root)
        if planned_target is None:
            repo,exact_head=select_execution_target(state_root,activation,products)
            execution_slice=load_execution_slice(state_root,repo,exact_head)
        else:
            repo,exact_head,execution_slice=planned_target
            if execution_slice.get("repository")!=repo or execution_slice.get("exact_head")!=exact_head:
                raise RuntimeError("PLANNED_SLICE_IDENTITY_MISMATCH")
            if not any(repo in activation["products"][name]["repositories"] and
                       activation["products"][name]["repositories"][repo]==exact_head
                       for name in products if name in activation.get("products",{})):
                raise RuntimeError("PLANNED_SLICE_HEAD_STALE")
        selected_products={
            name for name in products
            if repo in activation["products"][name]["repositories"]
        }
        planner_id=None
        if planned_target is not None and len(selected_products)==1:
            planner_id=planner_decision_id(next(iter(selected_products)),repo,exact_head,execution_slice)
        conflict=conflicting_nonterminal_grant(state_root,repo,execution_slice)
        if conflict:
            raise RuntimeError(f"EXECUTION_SLICE_ALREADY_OWNED:{conflict['grant_id']}:{conflict['state']}")
        grant_id=str(uuid.uuid4())
        runner_id=execution_slice.get("runner_id") or select_task_runner(execution_slice)
        runtime_id=execution_slice.get("execution_runtime") or runtime_for_task(execution_slice)
        execution_slice={**execution_slice,"runner_id":runner_id,"execution_runtime":runtime_id}
        grant={"schema_version":5,
               "grant_id":grant_id,
               "worker_id":execution_slice.get("worker_id"),
               "contract_sha256":hashlib.sha256(contract_bytes).hexdigest(),
               "activation_sha256":hashlib.sha256(activation_bytes).hexdigest(),
               "products":{
            name:{"repositories":activation["products"][name]["repositories"]}
            for name in selected_products},
               "execution_slice":execution_slice}
        return reserve_execution_grant(
            state_root,grant,repo,exact_head,execution_slice,runner_id,runtime_id,
            planner_id,lock["worker"],slice_paths_conflict,
        )
    finally:
        release_grant_issue_lock(lock)

def mark_builder_running(builder_state:Path,builder_output:Path,*,invocation_id:str,
                         grant_path:Path,worker_id:str|None,runner_id:str,runtime_id:str)->dict:
    """Replace stale terminal worker telemetry before the new child process starts."""
    try:
        grant_id=json.loads(grant_path.read_text(encoding="utf-8")).get("grant_id")
    except (OSError,json.JSONDecodeError,TypeError):
        grant_id=None
    payload={"schema_version":1,"status":"BUILDER_RUNNING","invocation_id":invocation_id,
             "worker_id":worker_id,"runner_id":runner_id,"runtime_id":runtime_id,
             "started_at_utc":datetime.now(UTC).isoformat()}
    if grant_id:
        payload["grant_id"]=grant_id
    atomic(builder_state,payload)
    builder_output.write_text("",encoding="utf-8")
    return payload

def run_builder(repo_root:Path,workspace_root:Path,state_root:Path,products:list[str]|None=None,
                worker_id:str|None=None,planned_target:tuple[str,str,dict]|None=None)->tuple[int,str|None]:
    worker_dir=state_root/"worker-state"/worker_id if worker_id else state_root
    worker_dir.mkdir(parents=True,exist_ok=True)
    builder_state=worker_dir/"builder.json"
    builder_output=worker_dir/"builder-last-output.txt"
    if not products:
        builder_output.write_text("EXECUTION_GRANT_REQUIRED\n",encoding="utf-8")
        return 2,None
    try:
        grant_path=None
        for attempt in range(3):
            try:
                grant_path=write_execution_grant(repo_root,state_root,products,planned_target=planned_target)
                break
            except RuntimeError as exc:
                if str(exc)!="GRANT_ISSUANCE_BUSY":
                    raise
                if attempt==2:
                    atomic(builder_state,{"status":"OWNERSHIP_HELD","reason":str(exc)})
                    return 0,None
                time.sleep(0.05*(attempt+1))
        assert grant_path is not None
    except RuntimeError as exc:
        if str(exc)=="ALL_EXECUTION_LANES_COMPLETE":
            atomic(builder_state,{"status":"NO_WORK","reason":"ALL_EXECUTION_LANES_COMPLETE"})
            builder_output.write_text("ALL_EXECUTION_LANES_COMPLETE\n",encoding="utf-8")
            return 0,None
        if str(exc).startswith("EXECUTION_SLICE_ALREADY_OWNED:"):
            atomic(builder_state,{"status":"OWNERSHIP_HELD","reason":str(exc)})
            builder_output.write_text(str(exc)+"\n",encoding="utf-8")
            return 0,None
        raise
    invocation_id=uuid.uuid4().hex
    decision_path=state_root/"dispatch-decision.json"
    try:
        decision=json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError):
        decision={}
    if isinstance(decision,dict):
        decision["invocation_id"]=invocation_id
        decision["grant_path"]=str(grant_path)
        atomic(decision_path,decision)
    grant_payload=json.loads(grant_path.read_text(encoding="utf-8"))
    grant_authority=grant_payload.get("execution_slice",{})
    runner_id=grant_authority.get("runner_id")
    runtime_id=grant_authority.get("execution_runtime")
    runtime_lease_id=grant_authority.get("runtime_lease_id")
    resource_lease_ids=grant_authority.get("resource_lease_ids",{})
    if not isinstance(resource_lease_ids,dict):
        raise RuntimeError("RESOURCE_LEASE_AUTHORITY_INVALID")
    if not runner_id or not runtime_id:
        raise RuntimeError("EXECUTION_GRANT_IDENTITY_REQUIRED")
    if not runtime_lease_id:
        raise RuntimeError("RUNTIME_LEASE_ID_REQUIRED")
    cmd=[sys.executable,str(repo_root/"scripts"/"autonomous_slice_builder.py"),
        "--workspace-root",str(workspace_root),"--state",str(builder_state),
        "--execution-grant",str(grant_path),"--invocation-id",invocation_id,
        "--runner-id",runner_id,"--runtime-id",runtime_id,
        "--runtime-lease-id",runtime_lease_id,
        "--runtime-lease-state",str(RUNTIME_LEASES.state_path),
        "--resource-lease-ids",json.dumps(resource_lease_ids,sort_keys=True)]
    mark_builder_running(builder_state,builder_output,invocation_id=invocation_id,
                         grant_path=grant_path,worker_id=worker_id,
                         runner_id=runner_id,runtime_id=runtime_id)
    p=subprocess.run(cmd,
        cwd=repo_root,text=True,capture_output=True,check=False)
    builder_output.write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    finalize_unclaimed_grant(grant_path,p.returncode,builder_state)
    return p.returncode,invocation_id

def run_builder_for_product(repo_root:Path,workspace_root:Path,state_root:Path,product:str,
                            worker_id:str|None=None,planned_target:tuple[str,str,dict]|None=None)->tuple[int,str|None]:
    """Run one task-scoped builder with worker-isolated result state."""
    return run_builder(repo_root,workspace_root,state_root,[product],worker_id=worker_id,
                       planned_target=planned_target)


def harvest_parallel_builders(pool:ParallelWorkerPool,state_root:Path)->dict:
    """Persist completed worker outcomes without blocking other product lanes."""
    completed=pool.harvest()
    rows={}
    for worker_id,result in completed.items():
        if isinstance(result,BaseException):
            rows[worker_id]={"status":"WORKER_EXCEPTION","error_class":type(result).__name__}
        else:
            rc,invocation_id=result
            worker_state=state_root/"worker-state"/worker_id
            ok,status=builder_health(worker_state,rc,invocation_id)
            rows[worker_id]={"status":status,"returncode":rc,"healthy":ok,
                             "invocation_id":invocation_id}
    if rows:
        atomic(state_root/"parallel-worker-results.json",{
            "schema_version":1,"completed":rows,
            "recorded_at_utc":datetime.now(UTC).isoformat(),
        })
    return rows


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
revalidate_preserved_work = _workspace_recovery.revalidate_preserved_work
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
    """Record why this physical supervisor may continue a prior logical burst."""
    current=instance_lock["worker"]
    previous_id=burst.get("previous_process_instance_id")
    proof={"schema_version":1,"burst_id":burst["burst_id"],
           "process_instance_id":current["instance_id"],
           "previous_process_instance_id":previous_id,
           "resumed":bool(burst.get("resumed")),
           "proven_at_utc":datetime.now(UTC).isoformat()}
    if not burst.get("resumed"):
        proof["status"]="NEW_BURST_NO_TAKEOVER"
        return proof
    if not previous_id:
        proof["status"]="RESTART_PROVEN_BY_EXCLUSIVE_KERNEL_LOCK"
        proof["previous_owner_evidence"]="LEGACY_IDENTITY_UNAVAILABLE"
        return proof
    if previous_id==current["instance_id"]:
        proof["status"]="RESTART_IDENTITY_COLLISION"
        return proof
    # Acquiring the non-shareable kernel lock is the authoritative proof that
    # no prior supervisor still owns this state root. Preserve the predecessor
    # identity so restart provenance is explicit rather than inferred.
    proof["status"]="RESTART_PROVEN_BY_EXCLUSIVE_KERNEL_LOCK"
    proof["previous_owner_evidence"]="KERNEL_LOCK_RELEASED_OR_PROCESS_EXITED"
    return proof

def start_or_resume_burst(state_root:Path,*,process_instance_id:str|None=None,now:datetime|None=None,resume_seconds:int=900)->dict:
    """Resume a recently interrupted immediate burst; otherwise create a new wake identity."""
    now=now or datetime.now(UTC)
    path=state_root/"continuation-current-burst.json"
    try:
        prior=json.loads(path.read_text(encoding="utf-8"))
        updated=datetime.fromisoformat(str(prior.get("updated_at_utc","")).replace("Z","+00:00"))
        age=(now-updated).total_seconds()
        schema=prior.get("schema_version")
        burst_id=prior.get("burst_id")
        pass_count=prior.get("pass_count")
        prior_instance=prior.get("process_instance_id")
        valid_schema=schema in {1,2}
        valid_burst=isinstance(burst_id,str) and 1 <= len(burst_id) <= 128
        valid_pass=isinstance(pass_count,int) and not isinstance(pass_count,bool) and 0 <= pass_count <= 1000000
        valid_instance=(schema==1 or (isinstance(prior_instance,str) and 1 <= len(prior_instance) <= 128))
        if (prior.get("immediate_continuation") is True and valid_schema and valid_burst
                and valid_pass and valid_instance and 0 <= age <= resume_seconds):
            return {"burst_id":burst_id,"burst_pass":pass_count,
                    "resumed":True,"resume_age_seconds":age,
                    "previous_process_instance_id":prior_instance,
                    "process_instance_id":process_instance_id}
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        pass
    return {"burst_id":now.strftime("%Y%m%dT%H%M%S.%fZ"),"burst_pass":0,
            "resumed":False,"resume_age_seconds":None,"previous_process_instance_id":None,
            "process_instance_id":process_instance_id}

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--repo-root",required=True); p.add_argument("--workspace-root",required=True)
    p.add_argument("--state-root",required=True); p.add_argument("--builder-seconds",type=int,default=300)
    p.add_argument("--merge-seconds",type=int,default=60); p.add_argument("--once",action="store_true")
    p.add_argument("--yield-on-interactive-session",action="store_true",
                   help="SYSTEM cold-boot owner exits when an interactive user session appears")
    a=p.parse_args(); repo=Path(a.repo_root); workspace=Path(a.workspace_root); state=Path(a.state_root)
    state.mkdir(parents=True,exist_ok=True)
    instance_lock=acquire_supervisor_instance_lock(state)
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
    burst_id=burst["burst_id"]
    burst_pass=burst["burst_pass"]
    try: burst_proof=json.loads((state/"autonomous-burst-proof.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError): burst_proof={}
    try: product_proof=json.loads((state/"portfolio-product-advancement-proof.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError): product_proof={}
    previous_repository=None
    previous_builder_status=None
    rotation_evidence=None
    worker_pool=ParallelWorkerPool(max_workers_per_product=2,max_total_workers=6)
    atexit.register(worker_pool.shutdown,wait=False)
    while True:
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
        released_recovery_generations=recover_stale_recovery_generations(state)
        abandoned_grants=recover_stale_grant_claims(state)
        recovery_queue=pending_recovery_grants(state,abandoned_grants)
        recovery_results=reconcile_abandoned_claims(workspace,state,recovery_queue)
        preserved_work_rechecks=recheck_preserved_work_quarantines(workspace,state)
        quarantine_rechecks=recheck_quarantined_claims(state)
        newly_released=[g for g,outcome in quarantine_rechecks.items() if outcome=="RELEASED_TO_RECOVERY"]
        if newly_released:
            recovery_results.update(reconcile_abandoned_claims(workspace,state,newly_released))
        quarantines=quarantined_grants(state)
        publish_direction_outbox(state)
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
        parallel_results=harvest_parallel_builders(worker_pool,state)
        dispatch_invocation_id=uuid.uuid4().hex if products else None
        dispatched_workers=dispatch_parallel_builders(
            worker_pool,repo,workspace,state,products,
            dispatch_invocation_id=dispatch_invocation_id
        ) if products else []
        # A completed nonproductive worker has already released its slot. Refill
        # immediately in the same burst pass instead of waiting for the next loop.
        # This turns ownership/no-work/exception outcomes into routing feedback.
        if products and parallel_results and any(not row.get("healthy",False) for row in parallel_results.values()):
            dispatch_invocation_id=uuid.uuid4().hex
            dispatched_workers.extend(
                dispatch_parallel_builders(worker_pool,repo,workspace,state,products,
                                           dispatch_invocation_id=dispatch_invocation_id)
            )
        pool_snapshot=worker_pool.snapshot()
        # Persist the authoritative post-harvest/post-dispatch pool, not the
        # earlier dispatch-time snapshot. Otherwise completed futures can remain
        # on disk as phantom RUNNING workers until another dispatch occurs.
        atomic(state/"parallel-worker-pool.json",{
            "schema_version":2,"pool":pool_snapshot,"runtime_leases":RUNTIME_LEASES.snapshot(),
            "dispatched":dispatched_workers,"dispatch_invocation_id":dispatch_invocation_id,
            "runtime_lease_recovery":RUNTIME_LEASE_RECOVERY,
            "recorded_at_utc":datetime.now(UTC).isoformat(),
        })
        # The supervisor no longer blocks on implementation. A worker holding a
        # repository grant is active work; other product slots remain schedulable.
        rc=0 if (dispatched_workers or pool_snapshot["running_total"] or parallel_results) else 3
        builder_invocation_id=None
        if parallel_results:
            statuses=[row["status"] for row in parallel_results.values()]
            builder_status=statuses[-1]
            builder_ok=all(row.get("healthy",False) for row in parallel_results.values())
        elif pool_snapshot["running_total"]:
            builder_status="PARALLEL_WORKERS_ACTIVE"
            builder_ok=True
        else:
            builder_status="NO_READY_EXECUTION_SLICE"
            builder_ok=False
        current_repository=selected_builder_repository(state,builder_invocation_id)
        rotation_evidence=maybe_record_async_rotation(state,previous_repository=previous_repository,
            previous_status=previous_builder_status,current_repository=current_repository,
            burst_id=burst_id,process_instance_id=process_instance_id)
        previous_repository=current_repository
        previous_builder_status=builder_status
        lifecycle_advanced=merge_watcher_advanced(state)
        decision_learning=refresh_decision_learning(state)
        if lifecycle_advanced:
            request_immediate_builder(state)
        liveness=productive_liveness(state,products,builder_status=builder_status)
        atomic(state/"productive-liveness.json",liveness)
        convergence=product_convergence(state,products,current_invocation_id=dispatch_invocation_id)
        idle_streaks=_planning.record_eligible_idle_streaks(
            state,products,current_invocation_id=dispatch_invocation_id,
        )
        liveness_reacquire=request_liveness_reacquire(state,liveness)
        if convergence["status"]=="UNEXPLAINED_ELIGIBLE_IDLE":
            request_immediate_builder(state)
            liveness_reacquire={"requested":True,"reason":"PRODUCT_CONVERGENCE_DEFECT",
                "products":convergence["unexplained_eligible_products"],
                "requested_at_utc":datetime.now(UTC).isoformat()}
        healthy=(builder_ok and linear_rc in (None,0) and not quarantines
                 and liveness["status"] not in {"STALE_ACTIVE_WORK","ELIGIBLE_IDLE","AUTHORIZED_NO_SLICE"}
                 and convergence["status"]=="CONVERGED"
                 and idle_streaks["status"]!="P1_LIVENESS_DEFECT")
        atomic(state/"supervisor.json",{"updated_at_utc":datetime.now(UTC).isoformat(),
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
            "decision_learning":decision_learning})
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
        burst_proof=record_burst_repository_pass(state,repository=selected_builder_repository(state,builder_invocation_id),
            builder_status=builder_status,burst_id=burst_id,process_instance_id=process_instance_id,
            sequence=record["sequence"])
        product_proof=record_product_identity_advancement(state,repository=selected_builder_repository(state,builder_invocation_id),
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

