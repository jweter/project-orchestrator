from __future__ import annotations
import json, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from project_orchestrator.context_compression_benchmark import EvidenceCase, benchmark_suite, promotion_decision

def load_cases(path:Path)->list[EvidenceCase]:
    raw=json.loads(path.read_text(encoding="utf-8"))["cases"]
    cases=[]
    for item in raw:
        payload=item["payload"]
        if item["name"]=="github-ci-failure":
            payload=("step=dependency-check status=success\n"*500)+payload+("step=cleanup status=success\n"*300)
        elif item["name"]=="worker-product-reality":
            payload=("worker=everward state=RUNNING heartbeat=ok\n"*600)+payload
        elif item["name"]=="structured-pr-evidence":
            base=json.loads(payload)
            base["checks"]=base["checks"]+[{"name":f"advisory-{i:03d}","status":"success","duration_ms":100+i} for i in range(400)]
            payload=json.dumps(base,separators=(",",":"))
        cases.append(EvidenceCase(item["name"],payload,tuple(item["essential_markers"])))
    return cases

def flatten_messages(messages):
    chunks=[]
    for msg in messages:
        content=msg.get("content","")
        if isinstance(content,str): chunks.append(content)
        elif isinstance(content,list):
            for part in content:
                if isinstance(part,dict) and isinstance(part.get("text"),str): chunks.append(part["text"])
    return "\n".join(chunks)

def main()->int:
    try:
        from headroom import compress
    except Exception as exc:
        print(json.dumps({"status":"SKIP","reason":f"headroom unavailable: {exc}"}))
        return 2
    native=[]
    def compressor(payload:str)->str:
        start=time.perf_counter()
        result=compress([{"role":"user","content":payload}],model="gpt-4o",compress_user_messages=True,protect_recent=0)
        native.append({
            "tokens_before":result.tokens_before,
            "tokens_after":result.tokens_after,
            "tokens_saved":result.tokens_saved,
            "headroom_reported_savings_ratio":result.compression_ratio,
            "transforms_applied":list(result.transforms_applied),
            "latency_ms":round((time.perf_counter()-start)*1000,3),
        })
        return flatten_messages(result.messages)
    report=benchmark_suite(load_cases(ROOT/"engineering"/"state"/"context-compression-corpus.json"),compressor)
    report["headroom_native"]=native
    report["decision"]=promotion_decision(report)
    report["status"]="PASS"
    out=ROOT/"engineering"/"state"/"headroom-benchmark-result.json"
    out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
