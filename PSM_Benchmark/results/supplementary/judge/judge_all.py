"""Semantic judge (METHOD.md section 5): one Opus 4.6 call per protocol, temperature 0,
matches counted at confidence >= 0.6, applied to our exported FSMs.
Outputs: judge_out/<P>_ours.json and judge_out/summary.json. Resumable (skips existing).
Reads ANTHROPIC_API_KEY from the environment."""
import json, os, sys, re, time, glob, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
REPO = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent / "judge_out"; OUT.mkdir(exist_ok=True)
import litellm
litellm.set_verbose = False
MODEL = "anthropic/claude-opus-4-6"; CONF = 0.6; WORKERS = int(os.environ.get("JUDGE_WORKERS", "5"))
PROTOS = ["TCP","DCCP","BGP","PPP","DHCP","PPTP","IMAP","POP3","SIP","RTSP","MQTT","SMTP","NNTP","FTP"]
JUDGE = open(Path(__file__).resolve().parent / "judge_prompt.txt").read()
lock = threading.Lock()

def load(proto, system):
    gt = json.load(open(next((REPO / "RFC_PSM_Benchmark" / proto).glob("*_state_machine.json"))))
    if system == "ours":
        k = [d for d in (REPO / "PSM_Benchmark" / "protocols" / proto / "output").iterdir() if d.is_dir()][0]
        c = json.load(open(k / "fsm" / f"{proto}_a2a-pipeline_final_fsm.json"))
    return gt, c

def judge_pair(proto, system):
    out = OUT / f"{proto}_{system.replace(':','-')}.json"
    if out.exists():
        return json.load(open(out))
    gt, c = load(proto, system)
    user = ("GROUND TRUTH:\n" + json.dumps(gt, indent=1) + "\n\nCANDIDATE:\n" + json.dumps(c, indent=1)
            + "\n\nIndices refer to the position in each transitions list (0-based).")
    for attempt in range(3):
        try:
            t0 = time.time()
            r = litellm.completion(model=MODEL, max_tokens=16000, temperature=0,
                                   messages=[{"role": "system", "content": JUDGE}, {"role": "user", "content": user}])
            txt = r.choices[0].message.content
            data = json.loads(re.search(r"\{.*\}", txt, re.S).group(0)); break
        except Exception as e:
            err = str(e); time.sleep(20 * (attempt + 1))
    else:
        return {"proto": proto, "system": system, "error": err}
    sm = [x for x in data["state_matches"] if x.get("candidate") and (x.get("confidence") or 0) >= CONF]
    tm = [x for x in data["transition_matches"] if x.get("candidate_index") is not None and (x.get("confidence") or 0) >= CONF]
    nS, nT, cS, cT = len(gt["states"]), len(gt["transitions"]), len(c["states"]), len(c["transitions"])
    f = lambda p, r: 2*p*r/(p+r) if p+r else 0.0
    sp, sr, tp, tr = (len(sm)/cS if cS else 0), len(sm)/nS, (len(tm)/cT if cT else 0), len(tm)/nT
    cls = {}
    for x in data.get("unmatched_candidate_transitions", []): cls[x.get("class")] = cls.get(x.get("class"), 0) + 1
    res = {"proto": proto, "system": system, "gt_states": nS, "gt_transitions": nT, "cand_states": cS, "cand_transitions": cT,
           "states_matched": len(sm), "states_P": round(sp,4), "states_R": round(sr,4), "states_F1": round(f(sp,sr),4),
           "trans_matched": len(tm), "trans_P": round(tp,4), "trans_R": round(tr,4), "trans_F1": round(f(tp,tr),4),
           "unmatched_classes": cls, "tokens_in": r.usage.prompt_tokens, "tokens_out": r.usage.completion_tokens,
           "cost": getattr(r, "_hidden_params", {}).get("response_cost"), "seconds": round(time.time()-t0,1), "raw": data}
    json.dump(res, open(out, "w"), indent=1)
    with lock: print(f"{proto:5s} {system:28s} S {len(sm)}/{nS} of {cS}  T {len(tm)}/{nT} of {cT}  F1 {res['trans_F1']:.3f}  ${res['cost']}", flush=True)
    return res

systems = ["ours"]
jobs = [(p, s) for p in PROTOS for s in systems]
results = []
with ThreadPoolExecutor(WORKERS) as ex:
    futs = {ex.submit(judge_pair, p, s): (p, s) for p, s in jobs}
    for fu in as_completed(futs):
        results.append(fu.result())
json.dump(results, open(OUT / "summary.json", "w"), indent=1)
print("DONE", len(results), "pairs; total cost $", round(sum((r.get("cost") or 0) for r in results), 2))
