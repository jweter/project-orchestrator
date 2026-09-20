import json
from pathlib import Path
from scripts import continuous_flow_supervisor as supervisor

def test_supervisor_source_initializes_proof_state_before_loop():
    source=Path(supervisor.__file__).read_text(encoding="utf-8")
    init=source.index('burst_proof=load_json(state/"autonomous-burst-proof.json")')
    loop=source.index("while True:",init)
    publish=source.index('"autonomous_burst_proof":burst_proof',loop)
    assert init < loop < publish
    assert 'product_proof=load_json(state/"portfolio-product-advancement-proof.json")' in source[init:loop]
