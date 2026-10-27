"""需求来源:需求单(ticket)、需求文档(prd)等链接的识别、记录和读取结果的登记。

配置(插件里的 crosscheck.conf 或 ~/.crosscheck/config,按用途命名,只写工具名,调用方式由 agent 决定):
  source.ticket   = <skill 或命令名>          读需求单用的
  source.prd      = <skill 或命令名>          读需求文档用的
  source.required = ticket, prd               每次都要收集(可跳过)的类别;不写时只有 ticket
  source.<kind>.label = <显示名>              MR 说明里显示的名字
  source.<kind>.match = <关键字>, ...         识别链接用的关键字;不写时从 label 和工具名推出

记录:<项目记录目录>/sources/<源分支>.json,同一分支只问一次,90 天后过期。
  {"ticket": {"status": "given|skip|none", "url", "title", "type", "at"}, "prd": {...},
   "tested": "...", "focus": [...], "page_url": "...", "updated_at": "..."}

用法(审查之外由 agent 调用):
  python3 sources.py stage                    新建一个暂存目录,打印路径;读到的文档写在这里
  python3 sources.py show DIR                 打印这次审查(或记录)对应分支的来源记录
  python3 sources.py set DIR KEY VALUE        记下 tested、intent、page_url、需求来源(ticket=链接|skip|none:类型);
                                              focus 用 JSON 数组,如 [{"where": "a.ts:42", "ask": "兼容期够不够"}]
审查时由 collect-context.sh 调用 resolve()。
"""
import datetime
import json
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crlib

KINDS = ("ticket", "prd")
DEFAULT_LABEL = {"ticket": "需求单", "prd": "需求文档"}
EXPIRE_DAYS = 90
GENERIC = {"toolkit", "tool", "tools", "cli", "mcp", "skill", "plugin", "docs", "doc", "fetch", "read", "reader", "get"}
URL = re.compile(r"https?://[^\s)>\]\"'`<,，。]+")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def settings(cfg=None):
    cfg = crlib.config() if cfg is None else cfg
    kinds = {}
    extra = sorted({m.group(1) for m in (re.match(r"source\.([a-z0-9_]+)$", x) for x in cfg) if m} - set(KINDS))
    for k in list(KINDS) + extra:
        if k == "required":
            continue
        tools = [t.strip() for t in cfg.get("source.%s" % k, "").split(",") if t.strip()]
        label = cfg.get("source.%s.label" % k, "") or DEFAULT_LABEL.get(k, k)
        match = [t.strip().lower() for t in cfg.get("source.%s.match" % k, "").split(",") if t.strip()]
        if not match:
            # 工具名只看冒号后面的部分:冒号前是插件或工具集的名字,多个工具共用
            names = [t.split(":")[-1] for t in tools] + [cfg.get("source.%s.label" % k, "")]
            for word in re.split(r"[\-_ ,/.]+", " ".join(names).lower()):
                if len(word) >= 3 and word not in GENERIC:
                    match.append(word)
        kinds[k] = {"tools": tools, "label": label, "match": list(dict.fromkeys(match))}
    req = cfg.get("source.required")
    required = [x.strip() for x in (req if req is not None else "ticket").split(",") if x.strip()]
    for k in required:
        kinds.setdefault(k, {"tools": [], "label": DEFAULT_LABEL.get(k, k), "match": []})
    return {"kinds": kinds, "required": required, "page": cfg.get("output.page", "")}


def store_path(meta):
    store = meta.get("store_dir", "")
    if not store:
        return ""
    branch = meta.get("source_branch") or meta.get("target_key") or "unknown"
    return os.path.join(os.path.dirname(store), "sources", re.sub(r"[^A-Za-z0-9._-]", "_", branch) + ".json")


def load_store(path):
    data = crlib.load_json(path, {}) if path else {}
    if not isinstance(data, dict):
        return {}
    limit = (datetime.datetime.now() - datetime.timedelta(days=EXPIRE_DAYS)).strftime("%Y-%m-%d %H:%M")
    for k in list(data):
        v = data[k]
        if isinstance(v, dict) and v.get("status") and str(v.get("at", "")) < limit:
            del data[k]
    return data


def save_store(path, data):
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data["updated_at"] = _now()
    crlib.save_json(path, data)


def parse_arg(value):
    """--source 的值:链接、skip、none:<类型>、unasked(非交互,不记)。"""
    v = value.strip()
    if v in ("skip", "跳过"):
        return {"status": "skip"}
    if v == "unasked":
        return {"status": "unasked"}
    if v.startswith("none"):
        return {"status": "none", "type": v.split(":", 1)[1].strip() if ":" in v else ""}
    return {"status": "given", "url": v}


def read_staged(stage_dir):
    """agent 读到的文档:<kind>[-n].md,头部 url:/title:/tool: 几行,然后一行 ---,其余是正文。"""
    docs = []
    if not stage_dir or not os.path.isdir(stage_dir):
        return docs
    for name in sorted(os.listdir(stage_dir)):
        m = re.fullmatch(r"([a-z0-9_]+)(?:-\d+)?\.(md|txt)", name)
        if not m:
            continue
        try:
            text = open(os.path.join(stage_dir, name), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        head, sep, body = text.partition("\n---\n")
        if not sep:
            head, body = "", text
        info = {}
        for line in head.split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip().lower()] = v.strip()
        docs.append({"kind": m.group(1), "url": info.get("url", ""), "title": info.get("title", ""),
                     "tool": info.get("tool", ""), "error": info.get("error", ""), "text": body.strip(), "file": name})
    return docs


def detect(text, conf):
    found = {}
    for url in URL.findall(text or ""):
        low = url.lower()
        # 一个链接只归给一类:命中的关键字最长的那类
        best = max(((max((len(w) for w in c["match"] if w in low), default=0), k) for k, c in conf["kinds"].items()
                    if k not in found), default=(0, None))
        if best[0]:
            found[best[1]] = url
    return found


def resolve(meta, review_dir, text, args, stage_dir, has_issue_ref=False, cfg=None):
    """合并参数、记录、暂存的文档和自动识别,写 sources.json,返回 (缺的类别, 文档列表)。"""
    conf = settings(cfg)
    path = store_path(meta)
    store = load_store(path)
    auto = detect(text, conf)
    docs = read_staged(stage_dir)
    out, missing, changed = [], [], False
    for k, c in conf["kinds"].items():
        entry = None
        if k in args:
            entry = dict(args[k], source="参数")
            if entry["status"] != "unasked":
                store[k] = dict({x: entry.get(x, "") for x in ("status", "url", "type")}, at=_now())
                changed = True
        elif isinstance(store.get(k), dict):
            entry = dict(store[k], source="记录")
        elif k in auto:
            entry = {"status": "given", "url": auto[k], "source": "自动识别"}
        elif k == "ticket" and has_issue_ref:
            entry = {"status": "given", "url": "", "source": "提交说明或描述里的需求编号"}
        kdocs = [x for x in docs if x["kind"] == k]
        if entry and entry.get("status") == "given" and kdocs and not entry.get("title"):
            entry["title"] = next((x["title"] for x in kdocs if x["title"]), "")
            if entry.get("title") and isinstance(store.get(k), dict) and store[k].get("url") == entry.get("url"):
                store[k]["title"] = entry["title"]
                changed = True
        if entry is None and k in conf["required"]:
            missing.append(k)
        out.append(dict(entry or {"status": "missing"}, kind=k, label=c["label"], tools=c["tools"],
                        required=k in conf["required"], docs=[x["file"] for x in kdocs],
                        read_error=next((x["error"] for x in kdocs if x["error"]), "")))
    if changed:
        save_store(path, store)
    crlib.save_json(os.path.join(review_dir, "sources.json"),
                    {"items": out, "missing": missing, "store": path, "page": conf["page"],
                     "tested": store.get("tested", ""), "focus": store.get("focus", []), "page_url": store.get("page_url", "")})
    return missing, docs, conf


def _meta_of(d):
    meta = crlib.read_kv(os.path.join(d, "meta.txt"))
    if not meta:
        sys.exit("sources: %s 里没有 meta.txt" % d)
    return meta


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "stage":
        print(tempfile.mkdtemp(prefix="cr-src.", dir=os.environ.get("TMPDIR") or None))
        return 0
    if cmd in ("show", "set") and len(argv) >= 2:
        meta = _meta_of(argv[1])
        path = store_path(meta)
        store = load_store(path)
        if cmd == "show":
            print(json.dumps(store, ensure_ascii=False, indent=2))
            return 0
        kinds = settings()["kinds"]
        if len(argv) < 4 or argv[2] not in ("tested", "page_url", "focus", "intent") + tuple(kinds):
            sys.exit("sources: 用法 set DIR tested|page_url|focus|intent|%s VALUE" % "|".join(kinds))
        value = argv[3]
        if argv[2] in kinds:
            value = dict({k: v for k, v in parse_arg(value).items()}, at=_now())
        if argv[2] == "focus":
            try:
                value = json.loads(value)
                assert isinstance(value, list)
            except (ValueError, AssertionError):
                sys.exit("sources: focus 要写成 JSON 数组,如 [{\"where\":\"a.ts:42\",\"ask\":\"并发安全吗\"}]")
            value = value[:3]
        store[argv[2]] = value
        save_store(path, store)
        print("已记下 %s" % argv[2])
        return 0
    sys.exit("sources: 未知命令,见 --help")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
