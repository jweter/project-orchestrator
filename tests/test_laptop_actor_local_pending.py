import json
from scripts.laptop_actor_github_bridge import promote_local_pending
SHA="a"*40
def job():
 return {"job_id":"j1","packet_id":"p1","repository":"jweter/Project-Everward","branch":"orchestrator/probe","base_sha":SHA,"verification":["git status --short"],"merge_authority":False,"product_reality_authority":False}
def test_local_pending_is_promoted_to_runner_inbox(tmp_path):
 q=tmp_path/"q";p=q/"pending";i=q/"inbox";p.mkdir(parents=True);i.mkdir()
 (p/"j1.json").write_text(json.dumps(job()))
 assert promote_local_pending(q,i)==1
 assert not (p/"j1.json").exists();assert (i/"j1.job.json").exists()
def test_completed_job_does_not_reexecute(tmp_path):
 q=tmp_path/"q";p=q/"pending";i=q/"inbox";p.mkdir(parents=True);i.mkdir()
 (i/"j1.job.json.done").write_text("done");(p/"j1.json").write_text(json.dumps(job()))
 assert promote_local_pending(q,i)==1;assert not (i/"j1.job.json").exists()
def test_invalid_local_job_fails_closed(tmp_path):
 q=tmp_path/"q";p=q/"pending";i=q/"inbox";p.mkdir(parents=True);i.mkdir();bad=job();bad["repository"]="evil/repo";(p/"j1.json").write_text(json.dumps(bad))
 try: promote_local_pending(q,i)
 except ValueError: pass
 else: raise AssertionError("expected validation failure")
 assert (p/"j1.json").exists()
