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

def first_meta(path):
    """Codex 会话文件第一行是 session_meta:取会话 id 和父会话 id(子代理才有)。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            d = json.loads(f.readline())
    except Exception:
        return None, None
    if d.get("type") != "session_meta" or not isinstance(d.get("payload"), dict):
        return None, None
    p = d["payload"]
    src = p.get("source") if isinstance(p.get("source"), dict) else {}
    spawn = (src.get("subagent") or {}).get("thread_spawn") if isinstance(src.get("subagent"), dict) else None
    parent = (spawn or {}).get("parent_thread_id") or p.get("parent_thread_id")
    return p.get("id"), parent


def codex_usage(lines, started=""):
    """Codex 每次模型调用写一条 token_usage_record;按 response_id 去重。input_tokens 含缓存部分,拆开计。"""
    by_id = {}
    for d in lines:
        if d.get("type") != "token_usage_record" or not isinstance(d.get("payload"), dict):
            continue
        if started and str(d.get("timestamp", ""))[:19] < started:
            continue
        p = d["payload"]
        by_id[p.get("response_id") or id(d)] = p.get("usage") or {}
    tot = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "reasoning": 0}
    for u in by_id.values():
        cached = int(u.get("cached_input_tokens") or 0)
        cw = int(u.get("cache_write_input_tokens") or 0)
        tot["input"] += max(0, int(u.get("input_tokens") or 0) - cached - cw)
        tot["cache_read"] += cached
        tot["cache_write"] += cw
        tot["output"] += int(u.get("output_tokens") or 0)
        tot["reasoning"] += int(u.get("reasoning_output_tokens") or 0)
    return tot, len(by_id)


def codex_children(transcript, started_ts):
    """子代理各有自己的会话文件,session_meta 里记着父会话 id。只看同一个 sessions 目录下、审查开始后改过的文件。"""
    root, sid = transcript, first_meta(transcript)[0]
    while root and os.path.basename(root) != "sessions":
        parent = os.path.dirname(root)
        if parent == root:
            return []
        root = parent
    if not sid or not root:
        return []
    metas = {}
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(path) < started_ts or os.path.samefile(path, transcript):
                continue
        except OSError:
            continue
        cid, parent = first_meta(path)
        if cid and parent:
            metas[path] = (cid, parent)
    found, wanted, changed = [], {sid}, True
    while changed:   # 子代理还可能再派子代理
        changed = False
        for path, (cid, parent) in list(metas.items()):
            if parent in wanted:
                found.append(path)
                wanted.add(cid)
                del metas[path]
                changed = True
    return found


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
    started = st.get("started_iso", "")
    try:
        started_ts = datetime.datetime.strptime(started, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        started_ts = 0
    if any(d.get("type") == "token_usage_record" for d in lines):
        tot, calls = codex_usage(lines)
        sub_calls = 0
        for path in codex_children(transcript, started_ts):
            t2, c2 = codex_usage(load_lines(path), started)
            for k in tot: tot[k] += t2[k]
            sub_calls += c2
        result = dict(tot, source="codex", subagents="included" if sub_calls else "none-seen", calls=calls + sub_calls)
        lines = []
    tot, calls = claude_usage(lines)
    sub_calls = 0
    subdir = transcript[:-len(".jsonl")] + "/subagents" if transcript.endswith(".jsonl") else ""
    if not result and subdir and os.path.isdir(subdir):
        for path in glob.glob(os.path.join(subdir, "*.jsonl")):
            if os.path.getmtime(path) < started_ts:
                continue
            sub = [d for d in load_lines(path) if str(d.get("timestamp", ""))[:19] >= started]
            t2, c2 = claude_usage(sub)
            for k in tot: tot[k] += t2[k]
            sub_calls += c2
    if not result and calls + sub_calls:
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
    if "reasoning" in result:
        data["usage_reasoning_tokens"] = result["reasoning"]
    write_kv(out, data)
    sys.exit(0)
