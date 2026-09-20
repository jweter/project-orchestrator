#!/usr/bin/env python3
"""Fail-closed validator for Hands-Off project intake forms."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

REQUIRED = (
    "Purpose","Users","Outcomes","Scope","Non-goals","Capability Checklist",
    "Product Completion Criteria","Roadmap","Architecture and Boundaries",
    "Quality Gates","Security and Privacy","Licensing and Provenance",
    "Human Escalation Boundary","Activation Checklist",
)
PLACEHOLDER = re.compile(r"(?im)^\s*(?:\*\*[^*]+:\*\*\s*)?(?:unknown|tbd|todo|n/?a)?\s*$")

def section(text: str, name: str) -> str | None:
    m=re.search(rf"(?ms)^##\s+\d+\.\s+{re.escape(name)}\s*$\n(.*?)(?=^##\s+\d+\.|\Z)",text)
    return m.group(1).strip() if m else None

def validate(text: str) -> dict:
    missing=[]; incomplete=[]
    for name in REQUIRED:
        body=section(text,name)
        if body is None:
            missing.append(name)
        elif not body.strip() or PLACEHOLDER.fullmatch(body.strip()):
            incomplete.append(name)
    # Intake must explicitly assert READY; unchecked template/default is not authority.
    activation=section(text,"Activation Checklist") or ""
    ready=bool(re.search(r"(?im)^\*\*Activation decision:\*\*\s*READY\s*$",activation))
    # Any explicit Unknown in a required section remains a direction request.
    unknown=[name for name in REQUIRED if (body:=section(text,name)) and re.search(r"(?i)\bUnknown\b",body)]
    issues=[]
    if missing: issues.append({"code":"MISSING_SECTION","sections":missing})
    if incomplete: issues.append({"code":"INCOMPLETE_SECTION","sections":incomplete})
    if unknown: issues.append({"code":"UNKNOWN_DIRECTION","sections":unknown})
    if not ready: issues.append({"code":"ACTIVATION_NOT_READY","sections":["Activation Checklist"]})
    return {
        "schema_version":1,
        "status":"READY" if not issues else "DIRECTION_REQUIRED",
        "issues":issues,
        "direction_required": sorted(set(missing+incomplete+unknown+([] if ready else ["Activation Checklist"]))),
    }

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("intake",type=Path);p.add_argument("--output",type=Path);a=p.parse_args()
    try: report=validate(a.intake.read_text(encoding="utf-8"))
    except OSError as e: report={"schema_version":1,"status":"DIRECTION_REQUIRED","issues":[{"code":"INTAKE_UNAVAILABLE","detail":str(e)}],"direction_required":["project intake"]}
    rendered=json.dumps(report,indent=2,sort_keys=True)+"\n"
    if a.output: a.output.write_text(rendered,encoding="utf-8")
    print(rendered,end="");return 0 if report["status"]=="READY" else 1
if __name__=="__main__": sys.exit(main())
