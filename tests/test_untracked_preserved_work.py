import subprocess
import scripts.continuous_flow_supervisor as supervisor

def _repo(tmp_path):
    subprocess.run(["git","init"],cwd=tmp_path,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","test@example.com"],cwd=tmp_path,check=True)
    subprocess.run(["git","config","user.name","Test"],cwd=tmp_path,check=True)
    (tmp_path/"tracked.txt").write_text("base")
    subprocess.run(["git","add","tracked.txt"],cwd=tmp_path,check=True)
    subprocess.run(["git","commit","-m","base"],cwd=tmp_path,check=True,capture_output=True)
    return subprocess.run(["git","rev-parse","HEAD"],cwd=tmp_path,text=True,capture_output=True,check=True).stdout.strip()

def test_preserved_evidence_hashes_untracked_content_without_storing_it(tmp_path):
    base=_repo(tmp_path)
    secret="sensitive-untracked-content"
    (tmp_path/"new.txt").write_text(secret)
    evidence=supervisor.preserved_work_evidence(tmp_path,base)
    assert evidence["capture_status"]=="CAPTURED"
    assert evidence["repair_eligible"] is False
    assert evidence["untracked"][0]["path"]=="new.txt"
    assert evidence["untracked"][0]["kind"]=="file"
    assert secret not in str(evidence)

def test_untracked_content_change_breaks_preserved_identity(tmp_path):
    base=_repo(tmp_path)
    (tmp_path/"new.txt").write_text("one")
    expected=supervisor.preserved_work_evidence(tmp_path,base)
    record={"base_sha":base,"preserved_work_evidence":expected}
    assert supervisor.revalidate_preserved_work(tmp_path,record)=="PRESERVED_WORK_EXACT"
    (tmp_path/"new.txt").write_text("two")
    assert supervisor.revalidate_preserved_work(tmp_path,record)=="PRESERVED_WORK_CHANGED"

def test_untracked_symlink_hashes_target_not_target_contents(tmp_path):
    base=_repo(tmp_path)
    target=tmp_path/"target.txt"; target.write_text("target-secret")
    link=tmp_path/"link.txt"
    try:
        link.symlink_to("target.txt")
    except OSError:
        return
    evidence=supervisor.preserved_work_evidence(tmp_path,base)
    link_record=next(x for x in evidence["untracked"] if x["path"]=="link.txt")
    assert link_record["kind"]=="symlink"
    assert "target-secret" not in str(evidence)

def test_large_untracked_file_fails_closed(tmp_path):
    base=_repo(tmp_path)
    with (tmp_path/"huge.bin").open("wb") as fh:
        fh.truncate(10*1024*1024+1)
    evidence=supervisor.preserved_work_evidence(tmp_path,base)
    assert evidence["capture_status"]=="UNTRACKED_FILE_TOO_LARGE"
