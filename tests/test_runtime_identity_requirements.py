import pytest
import scripts.continuous_flow_supervisor as supervisor

class Runtime:
    runtime_id="laptop-actor"
    def satisfies(self,requirements): return set(requirements)<= {"ollama","unreal"}
class Registry:
    def require(self,runtime_id):
        assert runtime_id=="laptop-actor"; return Runtime()

def test_laptop_actor_is_identity_not_capability(monkeypatch):
    seen=[]
    monkeypatch.setattr(supervisor,"require_runtime_capabilities",
                        lambda runtime_id,requirements:seen.append((runtime_id,requirements)))
    assert supervisor.runtime_for_task({"runtime_requirements":["laptop-actor"]})=="laptop-actor"
    assert seen==[("laptop-actor",[])]

def test_identity_and_real_capability_can_be_combined(monkeypatch):
    seen=[]
    monkeypatch.setattr(supervisor,"require_runtime_capabilities",lambda runtime_id,requirements:seen.append((runtime_id,requirements)))
    assert supervisor.runtime_for_task({"runtime_requirements":["laptop-actor","ollama"]})=="laptop-actor"
    assert seen==[("laptop-actor",["ollama"])]

@pytest.mark.parametrize("bad",["ollama",{"ollama":True},[1]])
def test_resource_requirements_fail_closed_on_malformed_input(bad):
    with pytest.raises(RuntimeError,match="INVALID_RUNTIME_REQUIREMENTS"):
        supervisor.resource_leases_for_task({"runtime_requirements":bad},"laptop-actor")

def test_resource_leases_ignore_runtime_identity():
    assert supervisor.resource_leases_for_task({"runtime_requirements":["laptop-actor"]},"laptop-actor")==()
    assert supervisor.resource_leases_for_task({"runtime_requirements":["laptop-actor","ollama","unreal"]},"laptop-actor")==("resource:ollama","resource:unreal")
