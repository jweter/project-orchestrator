from scripts.capability_slice_contract import derive_slice

def payload(slices):
    return {"capabilities":[{"id":"C1","capability":"Build thing","state":"PLANNED","depends_on":[],"completion_evidence":["acceptance green"],"slices":slices}],"roadmap":["C1"]}

def test_derives_exact_head_bounded_slice():
    r=derive_slice(payload([{"id":"S1","objective":"Implement parser","acceptance":["parser tests pass"],"state":"PLANNED","allowed_paths":["src/"],"forbidden_paths":["secrets/"]}]),repository="o/r",exact_head="abc")
    assert r["status"]=="READY"
    assert r["slice"]["capability_id"]=="C1"
    assert r["slice"]["slice_id"]=="S1"
    assert r["slice"]["repository"]=="o/r"
    assert r["slice"]["exact_head"]=="abc"
    assert r["slice"]["acceptance"]==["parser tests pass"]

def test_missing_slices_requests_direction():
    r=derive_slice(payload([]),repository="o/r",exact_head="abc")
    assert r["status"]=="DIRECTION_REQUIRED"
    assert r["issues"][0]["code"]=="EXECUTION_SLICE_REQUIRED"

def test_incomplete_slice_authority_fails_closed():
    r=derive_slice(payload([{"id":"S1","objective":"","acceptance":[]}]),repository="o/r",exact_head="abc")
    assert r["status"]=="DIRECTION_REQUIRED"
    assert r["issues"][0]["code"]=="SLICE_AUTHORITY_INCOMPLETE"

def test_verified_slice_advances_to_next_slice():
    r=derive_slice(payload([
        {"id":"S1","objective":"First","acceptance":["green"],"state":"VERIFIED_COMPLETE","verification":{"pr":1}},
        {"id":"S2","objective":"Second","acceptance":["green"],"state":"PLANNED","depends_on":["S1"]},
    ]),repository="o/r",exact_head="def")
    assert r["status"]=="READY"
    assert r["slice"]["slice_id"]=="S2"


def test_derives_all_dependency_ready_slices_for_parallel_scheduler():
    from scripts.capability_slice_contract import derive_slices
    r=derive_slices(payload([
        {"id":"S1","objective":"Parser","acceptance":["green"],"state":"PLANNED","allowed_paths":["src/parser/"]},
        {"id":"S2","objective":"UI","acceptance":["green"],"state":"PLANNED","allowed_paths":["src/ui/"]},
        {"id":"S3","objective":"Integration","acceptance":["green"],"state":"PLANNED","depends_on":["S1","S2"],"allowed_paths":["tests/integration/"]},
    ]),repository="o/r",exact_head="abc")
    assert r["status"]=="READY"
    assert [s["slice_id"] for s in r["slices"]]==["S1","S2"]
