import subprocess
from scripts.autonomous_slice_builder import persisted_change_failure

def _repo(tmp_path):
    subprocess.run(["git","init"],cwd=tmp_path,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","test@example.com"],cwd=tmp_path,check=True)
    subprocess.run(["git","config","user.name","Test"],cwd=tmp_path,check=True)
    (tmp_path/"worker.py").write_text("VALUE = 1\n")
    subprocess.run(["git","add","worker.py"],cwd=tmp_path,check=True)
    subprocess.run(["git","commit","-m","base"],cwd=tmp_path,check=True,capture_output=True)
    return subprocess.run(["git","rev-parse","HEAD"],cwd=tmp_path,text=True,capture_output=True,check=True).stdout.strip()

def test_persisted_change_accepts_readable_valid_python_diff(tmp_path):
    base=_repo(tmp_path)
    (tmp_path/"worker.py").write_text("VALUE = 2\n")
    assert persisted_change_failure(tmp_path,base,["worker.py"]) is None

def test_persisted_change_rejects_invalid_python_after_write(tmp_path):
    base=_repo(tmp_path)
    (tmp_path/"worker.py").write_text("VALUE = 2\\nBROKEN =\n")
    assert persisted_change_failure(tmp_path,base,["worker.py"])=="persisted_python_invalid:worker.py"

def test_persisted_change_rejects_claimed_change_without_diff(tmp_path):
    base=_repo(tmp_path)
    assert persisted_change_failure(tmp_path,base,["worker.py"])=="persisted_change_missing"

def test_persisted_change_does_not_follow_symlink(tmp_path):
    base=_repo(tmp_path)
    target=tmp_path/"outside.py"; target.write_text("BROKEN =")
    link=tmp_path/"link.py"
    try:
        link.symlink_to(target)
    except OSError:
        return
    subprocess.run(["git","add","link.py"],cwd=tmp_path,check=True)
    # The boundary deliberately skips content parsing for symlinks; other path/policy guards still apply.
    assert persisted_change_failure(tmp_path,base,["link.py"]) is None
