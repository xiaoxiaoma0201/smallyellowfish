"""Run full eval test."""
import urllib.request
import json
import time

print("=== Running Full Evaluation ===")
try:
    data = json.dumps({}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/eval/run",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    start = time.time()
    resp = urllib.request.urlopen(req, timeout=900).read().decode()
    elapsed = time.time() - start
    result = json.loads(resp)
    
    total = result.get("total", 0)
    passed = result.get("passed", 0)
    failed = result.get("failed", 0)
    print(f"EVAL DONE in {elapsed:.1f}s: total={total}, passed={passed}, failed={failed}")
    
    for r in result.get("results", []):
        case_id = r.get("case_id", "?")
        pass_fail = "PASS" if r.get("passed") else "FAIL"
        print(f"  {pass_fail}: {case_id}")
        if not r.get("passed"):
            signals = r.get("missing_signals", [])
            tools = r.get("missing_tools", [])
            if signals:
                print(f"    missing_signals: {signals}")
            if tools:
                print(f"    missing_tools: {tools}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")

print()
print("=== Eval Complete ===")
