from scripts.project_intake_validator import validate

def intake(criteria):
    names=["Purpose","Users","Outcomes","Scope","Non-goals","Capability Checklist",
           "Product Completion Criteria","Roadmap","Architecture and Boundaries","Quality Gates",
           "Security and Privacy","Licensing and Provenance","Human Escalation Boundary"]
    bodies={name:"Project-specific direction." for name in names}
    bodies["Product Completion Criteria"]=criteria
    parts=[f"## {i}. {name}\n{bodies[name]}" for i,name in enumerate(names,1)]
    parts.append(f"## {len(names)+1}. Activation Checklist\n**Activation decision:** READY")
    return "\n\n".join(parts)

def test_structured_completion_criteria_are_machine_readable_authority():
    result=validate(intake("- [required] final_acceptance: Final acceptance suite passes | verify=acceptance\n- [required] product_reality: Product Reality debt is resolved | verify=product_reality"))
    assert result["status"]=="READY"
    assert [c["id"] for c in result["completion_criteria"]]==["final_acceptance","product_reality"]

def test_prose_only_completion_criteria_fail_closed():
    result=validate(intake("The product should be done and useful."))
    assert result["status"]=="DIRECTION_REQUIRED"
    assert any(i["code"]=="STRUCTURED_COMPLETION_CRITERIA_REQUIRED" for i in result["issues"])

def test_duplicate_completion_criterion_ids_fail_closed():
    result=validate(intake("- [required] done: One | verify=acceptance\n- [required] done: Two | verify=product_reality"))
    assert result["status"]=="DIRECTION_REQUIRED"
    assert any(i["code"]=="DUPLICATE_COMPLETION_CRITERION" for i in result["issues"])
