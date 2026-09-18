from __future__ import annotations

import hashlib
import json
from typing import Any

from .learning import _contains_secret

PRODUCT_REPOSITORIES = {
    "jweter/knowledge-engine-core", "jweter/knowledge-engine-web", "jweter/knowledge-engine-ai",
    "jweter/rocksmith-cdlc-generator", "jweter/Project-Everward",
}
ACTIVE = {"CANDIDATE", "VERIFIED", "PREFERRED"}
DEMOTED = "DEMOTED"


def fingerprint(repository: str, failure_class: str, signature: str) -> str:
    if repository not in PRODUCT_REPOSITORIES or not failure_class.strip() or not signature.strip():
        raise ValueError("product repository, failure class, and signature are required")
    normalized = " ".join(signature.lower().split())
    return hashlib.sha256(f"{repository}|{failure_class}|{normalized}".encode()).hexdigest()[:20]


def capture_episode(record: dict[str, Any]) -> dict[str, Any]:
    required = {"repository", "source_ref", "kind", "failure_class", "signature", "diagnosis",
                "action", "verification_ref", "capability", "evidence_state"}
    missing = sorted(required - record.keys())
    if missing:
        raise ValueError(f"episode missing fields: {missing}")
    if record["repository"] not in PRODUCT_REPOSITORIES:
        raise ValueError("episode repository is not a product repository")
    if record["kind"] not in {"repair", "experiment"} or record["evidence_state"] != "VERIFIED":
        raise ValueError("only VERIFIED repair/experiment episodes are accepted")
    if _contains_secret(record):
        raise ValueError("episode appears to contain secret material")
    item = {key: record[key] for key in required}
    item["fingerprint"] = fingerprint(item["repository"], item["failure_class"], item["signature"])
    stable = json.dumps(item, sort_keys=True, separators=(",", ":"))
    item["episode_id"] = hashlib.sha256(stable.encode()).hexdigest()[:20]
    return item


def recurring_fingerprints(episodes: list[dict[str, Any]], minimum: int = 2) -> dict[str, int]:
    counts: dict[str, int] = {}
    for episode in episodes:
        if episode.get("evidence_state") == "VERIFIED":
            key = str(episode.get("fingerprint", ""))
            if key:
                counts[key] = counts.get(key, 0) + 1
    return {key: count for key, count in counts.items() if count >= minimum}


def compile_capability(episodes: list[dict[str, Any]], *, minimum_verified: int = 2) -> dict[str, Any]:
    if not episodes or any(item.get("evidence_state") != "VERIFIED" for item in episodes):
        raise ValueError("capability compilation requires verified episodes")
    fps = {item.get("fingerprint") for item in episodes}
    repos = {item.get("repository") for item in episodes}
    if len(fps) != 1 or len(repos) != 1 or len(episodes) < minimum_verified:
        raise ValueError("insufficient matching verified episodes")
    candidates = {json.dumps(item.get("capability"), sort_keys=True) for item in episodes}
    if len(candidates) != 1:
        raise ValueError("verified episodes disagree on capability")
    capability = json.loads(next(iter(candidates)))
    seed = f"{next(iter(repos))}|{next(iter(fps))}|{next(iter(candidates))}"
    return {
        "capability_id": "cap-" + hashlib.sha256(seed.encode()).hexdigest()[:12],
        "repository": next(iter(repos)), "fingerprint": next(iter(fps)),
        "rule": capability, "status": "VERIFIED", "verified_episode_count": len(episodes),
        "authority": "ADVISORY_NORMAL_REPOSITORY_GATES_REQUIRED",
    }


def inject_capabilities(packet: dict[str, Any], capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = []
    for cap in capabilities:
        if cap.get("status") not in ACTIVE or cap.get("repository") != packet.get("repository"):
            continue
        failure = packet.get("failure_fingerprint")
        if failure and cap.get("fingerprint") != failure:
            continue
        applicable.append(cap)
    return {**packet, "capabilities_to_reuse": sorted(applicable, key=lambda x: x["capability_id"])}


def measure_reuse(capability: dict[str, Any], applications: list[dict[str, Any]]) -> dict[str, Any]:
    relevant = [x for x in applications if x.get("capability_id") == capability.get("capability_id") and x.get("verified") is True]
    successes = sum(x.get("outcome") in {"PREVENTED_FAILURE", "EARLIER_DETECTION", "SHORTENED_REPAIR", "SUCCESS_PATTERN_REUSED"} for x in relevant)
    regressions = sum(x.get("outcome") in {"REPEATED_FAILURE", "QUALITY_REGRESSION", "HUMAN_INTERVENTION_INCREASED"} for x in relevant)
    status = capability.get("status", "VERIFIED")
    if regressions:
        status = DEMOTED
    elif len(relevant) >= 3 and successes == len(relevant):
        status = "PREFERRED"
    return {**capability, "status": status, "reuse_count": len(relevant),
            "successful_reuse_count": successes, "regression_count": regressions}
