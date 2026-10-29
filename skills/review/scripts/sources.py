"""需求来源:需求单(ticket)、需求文档(doc)等链接的识别、记录和读取结果的登记。

配置(插件里的 config/defaults.conf 或 ~/.crosscheck/config,按工具的用途命名;值是给 agent 的一句话说明,原样交给 agent,脚本不解析):
  tools.requirement  = <说明>                 需求管理工具:读需求单(类别 ticket)
  tools.doc_reader   = <说明>                 文档阅读工具:读 PRD、技术方案等文档链接(类别 doc)
  intent.required    = ticket, doc            每次都要收集(可跳过)的类别;不写时只有 ticket
显示名固定为「需求单」「需求文档」;识别链接用的关键字取自说明里出现的网址的域名,没写网址就只能靠用户给的链接。
不在 intent.required 里的类别是「可选资料」:每个分支也问一次(SOURCES_OPTIONAL),可以跳过、不算缺,跳过后记录会记住。

记录:<项目记录目录>/sources/<源分支>.json,同一分支只问一次,90 天后过期。
  {"ticket": {"status": "given|skip|none", "url", "title", "type", "at"}, "doc": {...}, "intent": "...", "updated_at": "..."}

用法(审查之外由 agent 调用):
  python3 sources.py stage                    新建一个暂存目录,打印路径;读到的文档写在这里
  python3 sources.py show DIR                 打印这次审查(或记录)对应分支的来源记录
  python3 sources.py set DIR KEY VALUE        记下 intent、需求来源(ticket/doc=链接|skip|none:类型)
审查时由 collect-context.sh 调用 resolve()。
"""
import datetime
import json
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
import crlib

KINDS = ("ticket", "doc")
KIND_TOOL = {"ticket": "tools.requirement", "doc": "tools.doc_reader"}   # 类别 → 配置里放工具名的键
DEFAULT_LABEL = {"ticket": "需求单", "doc": "需求文档"}
EXPIRE_DAYS = 90
HOST = re.compile(r"https?://([A-Za-z0-9.-]+)")
URL = re.compile(r"https?://[^\s)>\]\"'`<,，。]+")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def settings(cfg=None):
    cfg = crlib.config() if cfg is None else cfg
    kinds = {}
    for k in KINDS:
        tool = cfg.get(KIND_TOOL[k], "").strip()
        # 说明是自然语言,识别链接只用其中出现的网址的域名;没写网址就只能靠用户给的链接
        match = [h.lower().rstrip(".") for h in HOST.findall(tool)]
        kinds[k] = {"tool": tool, "label": DEFAULT_LABEL[k], "match": list(dict.fromkeys(match))}
    required = [x.strip() for x in cfg.get("intent.required", "ticket").split(",") if x.strip()]
    for k in required:
        kinds.setdefault(k, {"tool": "", "label": DEFAULT_LABEL.get(k, k), "match": []})
    return {"kinds": kinds, "required": required}


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


def resolve(meta, review_dir, text, args, stage_dir, has_issue_ref=False, cfg=None, issue=None, has_doc_files=False):
    """合并参数、记录、暂存的文档和自动识别,写 sources.json,返回 (缺的必填类别, 文档列表, 配置)。
    不是必填的类别(如默认的需求文档)也问一次,可以跳过,不算缺:放在 conf["optional"] 里,跳过后记录会记住。
    用 --doc 给了本地文档的,需求文档算已经给了。"""
    conf = settings(cfg)
    path = store_path(meta)
    store = load_store(path)
    auto = detect(text, conf)
    docs = read_staged(stage_dir)
    out, missing, optional, changed = [], [], [], False
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
            entry = {"status": "given", "url": (issue or {}).get("url", ""), "title": (issue or {}).get("title", ""),
                     "source": "提交说明或描述里的需求编号"}
        kdocs = [x for x in docs if x["kind"] == k]
        if entry is None and k == "doc" and has_doc_files:
            local_files = [x.strip() for x in (crlib.read_text(os.path.join(review_dir, "doc-files.txt")) or "").splitlines() if x.strip()]
            if not local_files and meta.get("plan_file"):
                local_files = [meta["plan_file"]]
            entry = {"status": "given", "source": "本地文档", "local_files": local_files[:5]}
        if entry is None and kdocs:
            entry = {"status": "given", "url": kdocs[0]["url"], "source": "暂存文档"}
        if entry and entry.get("status") == "given" and kdocs and not entry.get("title"):
            entry["title"] = next((x["title"] for x in kdocs if x["title"]), "")
            if entry.get("title") and isinstance(store.get(k), dict) and store[k].get("url") == entry.get("url"):
                store[k]["title"] = entry["title"]
                changed = True
        if entry is None and k in conf["required"]:
            if os.environ.get("CR_NONINTERACTIVE") == "1":
                entry = {"status": "unasked", "source": "非交互运行"}   # 没人可问:不算缺,也不记进记录
            else:
                missing.append(k)
        elif entry is None and os.environ.get("CR_NONINTERACTIVE") != "1":
            optional.append(k)
        out.append(dict(entry or {"status": "missing"}, kind=k, label=c["label"], tool=c["tool"],
                        required=k in conf["required"], docs=[x["file"] for x in kdocs],
                        read_error=next((x["error"] for x in kdocs if x["error"]), "")))
    if changed:
        save_store(path, store)
    conf["optional"] = optional
    crlib.save_json(os.path.join(review_dir, "sources.json"),
                    {"items": out, "missing": missing, "optional": optional, "store": path})
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
        if len(argv) < 4 or argv[2] not in ("intent",) + tuple(kinds):
            sys.exit("sources: 用法 set DIR intent|%s VALUE" % "|".join(kinds))
        value = argv[3]
        if argv[2] in kinds:
            value = dict({k: v for k, v in parse_arg(value).items()}, at=_now())
        store[argv[2]] = value
        save_store(path, store)
        print("已记下 %s" % argv[2])
        return 0
    sys.exit("sources: 未知命令,见 --help")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
