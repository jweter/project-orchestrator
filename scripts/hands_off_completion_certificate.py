#!/usr/bin/env python3
"""Fail-closed machine certificate for Project Hands-Off Evolution."""
from __future__ import annotations
import argparse,json
from datetime import UTC,datetime
from pathlib import Path

REQUIRED_PRODUCTS={"knowledge-engine","rocksmith","everward","voicestudio"}

def load(path:Path)->dict:
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        return {}

def evaluate(state:Path)->dict:
    burst=load(state/"continuation-current-burst.json")
    continuation=load(state/"continuation-history.json")
    autonomy=load(state/"autonomous-burst-proof.json")
    products=load(state/"portfolio-product-advancement-proof.json")
    supervisor=load(state/"supervisor.json")
    burst_id=burst.get("burst_id")
    checks={
        "logical_burst_present":isinstance(burst_id,str) and bool(burst_id),
        "continuation_multi_pass":isinstance(burst.get("pass_count"),int) and not isinstance(burst.get("pass_count"),bool) and burst.get("pass_count",0)>=2,
        "continuation_history_present":isinstance(continuation.get("pass_count"),int) and continuation.get("pass_count",0)>=2,
        "same_burst_autonomy":autonomy.get("burst_id")==burst_id,
        "multi_repository_autonomy":autonomy.get("multi_repository_proven") is True,
        "same_burst_products":products.get("burst_id")==burst_id,
        "all_four_products":products.get("all_four_products_proven") is True and set(products.get("advanced_products",[]))==REQUIRED_PRODUCTS,
        "supervisor_same_burst":supervisor.get("burst_id")==burst_id,
        "physical_instance_present":isinstance(supervisor.get("process_instance_id"),str) and bool(supervisor.get("process_instance_id")),
    }
    return {"schema_version":1,"evaluated_at_utc":datetime.now(UTC).isoformat(),"burst_id":burst_id,
            "status":"PASS" if all(checks.values()) else "FAIL","checks":checks,
            "advanced_products":products.get("advanced_products",[])}

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--state",type=Path,required=True);p.add_argument("--output",type=Path)
    a=p.parse_args();result=evaluate(a.state)
    if a.output:
        a.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2))
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__": raise SystemExit(main())
