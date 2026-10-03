from pathlib import Path
from scripts import autonomous_slice_builder as b


def test_stale_checkout_lock_removed_only_when_git_absent(monkeypatch,tmp_path:Path):
    lock=tmp_path/'index.lock'; lock.write_bytes(b'')
    monkeypatch.setattr(b,'git_processes_absent',lambda ws: True)
    result=b.recover_stale_checkout_lock(tmp_path,f"fatal: Unable to create '{lock}': File exists",now=lock.stat().st_mtime+b.STALE_GIT_LOCK_SECONDS+1)
    assert result['status']=='STALE_LOCK_REMOVED'
    assert not lock.exists()


def test_recent_checkout_lock_is_preserved(monkeypatch,tmp_path:Path):
    lock=tmp_path/'index.lock'; lock.write_bytes(b'')
    monkeypatch.setattr(b,'git_processes_absent',lambda ws: True)
    result=b.recover_stale_checkout_lock(tmp_path,f"fatal: Unable to create '{lock}': File exists",now=lock.stat().st_mtime+1)
    assert result['status']=='LOCK_TOO_RECENT'
    assert lock.exists()


def test_stale_checkout_lock_with_ambiguous_owner_is_preserved(monkeypatch,tmp_path:Path):
    lock=tmp_path/'index.lock'; lock.write_bytes(b'')
    monkeypatch.setattr(b,'git_processes_absent',lambda ws: False)
    result=b.recover_stale_checkout_lock(tmp_path,f"fatal: Unable to create '{lock}': File exists",now=lock.stat().st_mtime+b.STALE_GIT_LOCK_SECONDS+1)
    assert result['status']=='LOCK_OWNER_AMBIGUOUS'
    assert lock.exists()


def test_checkout_retries_once_after_proven_stale_lock(monkeypatch,tmp_path:Path):
    calls=[]
    def fake_run(argv,cwd,**kwargs):
        calls.append(argv)
        if len(calls)==1:
            return b.subprocess.CompletedProcess(argv,128,'',"fatal: Unable to create 'x/index.lock': File exists")
        return b.subprocess.CompletedProcess(argv,0,'ok','')
    monkeypatch.setattr(b,'run',fake_run)
    monkeypatch.setattr(b,'recover_stale_checkout_lock',lambda ws,stderr:{'status':'STALE_LOCK_REMOVED','lock_path':'x/index.lock'})
    result,recovery=b.checkout_with_stale_lock_recovery(['git','checkout','main'],tmp_path)
    assert result.returncode==0
    assert recovery['status']=='STALE_LOCK_REMOVED'
    assert len(calls)==2
