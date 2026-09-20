from scripts.project_intake_validator import validate

def _filled():
    names=("Purpose","Users","Outcomes","Scope","Non-goals","Capability Checklist",
           "Product Completion Criteria","Roadmap","Architecture and Boundaries",
           "Quality Gates","Security and Privacy","Licensing and Provenance",
           "Human Escalation Boundary")
    parts=[f"## {i}. {name}\n{("- [required] final_acceptance: Final acceptance passes | verify=acceptance" if name=="Product Completion Criteria" else "Project-specific "+name.lower()+" direction.")}" for i,name in enumerate(names,1)]
    parts.append("## 14. Activation Checklist\n- [x] complete\n\n**Activation decision:** READY")
    return "\n\n".join(parts)

def test_complete_intake_is_ready():
    report=validate(_filled())
    assert report["status"]=="READY"
    assert report["issues"]==[]

def test_missing_section_requests_exact_direction():
    text=_filled().replace("## 4. Scope\nProject-specific scope direction.\n\n","")
    report=validate(text)
    assert report["status"]=="DIRECTION_REQUIRED"
    assert "Scope" in report["direction_required"]

def test_unknown_answer_fails_closed():
    text=_filled().replace("Project-specific users direction.","Unknown")
    report=validate(text)
    assert report["status"]=="DIRECTION_REQUIRED"
    assert "Users" in report["direction_required"]

def test_not_explicitly_ready_fails_closed():
    report=validate(_filled().replace("**Activation decision:** READY","**Activation decision:** READY / DIRECTION_REQUIRED"))
    assert report["status"]=="DIRECTION_REQUIRED"
    assert "Activation Checklist" in report["direction_required"]
