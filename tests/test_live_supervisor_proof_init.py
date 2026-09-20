import json
from pathlib import Path
from scripts import continuous_flow_supervisor as supervisor

def test_supervisor_initializes_proof_state_before_loop_without_undefined_loader():
    source=Path(supervisor.__file__).read_text(encoding="utf-8")
    anchor=source.index('burst_id=burst["burst_id"]')
    loop=source.index("while True:",anchor)
    preloop=source[anchor:loop]
    assert 'autonomous-burst-proof.json' in preloop
    assert 'portfolio-product-advancement-proof.json' in preloop
    assert "load_json(" not in preloop
    assert "json.loads" in preloop
