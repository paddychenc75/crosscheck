"""usage.sh 的实现,用法和说明见 usage.sh。"""
import datetime, glob, json, os, sys
sys.dont_write_bytecode = True
import crlib

args = sys.argv[1:]
mode = args[0]

def write_kv(path, data, append=False):
    with open(path, "a" if append else "w", encoding="utf-8") as f:
        for k, v in data.items():
            f.write("%s=%s\n" % (k, v))

def count_lines(path):
    n = 0
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return n

if mode == "start":
    state, transcript = args[1], args[2]
    if not os.path.isfile(transcript):
        sys.exit(0)
    os.makedirs(os.path.dirname(state), exist_ok=True)
    write_kv(state, {
        "transcript": transcript,
        "lines": count_lines(transcript),
        "started_iso": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    })
    sys.exit(0)


def claude_usage(lines):
    """按 message.id 去重(同一条回复会被分几次写入),每条取最后一次的数字。"""
    by_id = {}
    for d in lines:
        m = d.get("message")
        u = m.get("usage") if isinstance(m, dict) else None
        if isinstance(u, dict):
            by_id[m.get("id") or id(d)] = u
    tot = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
    for u in by_id.values():
        tot["input"] += int(u.get("input_tokens") or 0)
        tot["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        tot["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
        tot["output"] += int(u.get("output_tokens") or 0)
    return tot, len(by_id)

def load_lines(path, skip=0):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i < skip:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out

if mode == "finish":
    state, out = args[1], args[2]
    if not os.path.isfile(state):
        sys.exit(0)
    st = crlib.read_kv(state)
    transcript = st.get("transcript", "")
    if not os.path.isfile(transcript):
        sys.exit(0)
    lines = load_lines(transcript, int(st.get("lines", "0") or 0))
    result = None
    tot, calls = claude_usage(lines)
    sub_calls = 0
    subdir = transcript[:-len(".jsonl")] + "/subagents" if transcript.endswith(".jsonl") else ""
    started = st.get("started_iso", "")
    if subdir and os.path.isdir(subdir):
        for path in glob.glob(os.path.join(subdir, "*.jsonl")):
            try:
                if os.path.getmtime(path) < datetime.datetime.strptime(started, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp():
                    continue
            except Exception:
                pass
            sub = [d for d in load_lines(path) if str(d.get("timestamp", ""))[:19] >= started]
            t2, c2 = claude_usage(sub)
            for k in tot: tot[k] += t2[k]
            sub_calls += c2
    if calls + sub_calls:
        result = dict(tot, source="claude", subagents="included" if sub_calls else "none-seen", calls=calls + sub_calls)
    if not result:
        sys.exit(0)
    total = result["input"] + result["cache_read"] + result["cache_write"] + result["output"]
    data = {
        "usage_input_tokens": result["input"], "usage_cache_read_tokens": result["cache_read"],
        "usage_cache_write_tokens": result["cache_write"], "usage_output_tokens": result["output"],
        "usage_total_tokens": total, "usage_model_calls": result["calls"],
        "usage_source": result["source"], "usage_subagents": result["subagents"], "usage_scope": "review",
    }
    write_kv(out, data)
    sys.exit(0)
