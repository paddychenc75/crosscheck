"""collect-context.sh 的实现:意图(含需求单和需求来源)、人工讨论、测试削弱,依次收集。用法见 collect-context.sh。"""
import json
import os
import sys

sys.dont_write_bytecode = True
import crlib
import discussions
import intent
import weakening
from platform_api import Platform


def main(review_dir):
    meta = crlib.read_kv(os.path.join(review_dir, "meta.txt"))
    api = Platform(meta)

    it, (missing, conf) = intent.collect(api, meta, review_dir)
    found, name = discussions.fetch(api, meta, review_dir)
    if found:
        it.add("%s 上的人工讨论(%d 条)" % (name, len(found)), discussions.summary(found), "%s 讨论 %d 条" % (name, len(found)))
    it.write(review_dir)

    weak = weakening.scan(review_dir)
    weakening.write(review_dir, weak)

    extra = {
        "sources_missing": ",".join(missing),
        "sources_optional": ",".join(conf.get("optional", [])),
        "intent_sources": "、".join(dict.fromkeys(it.sources)) or "无",
        "intent_acceptance": len(it.acceptance),
        "discussions": len(found),
        "weakening": len(weak),
    }
    with open(os.path.join(review_dir, "meta.txt"), "a", encoding="utf-8") as f:
        for k, v in extra.items():
            f.write("%s=%s\n" % (k, v))

    if weak:
        print("WEAKENING: 发现 %d 处测试或检查被削弱,报告里单独列出(weakening.txt)" % len(weak))
    for n in it.notes:
        print("NOTE: " + n)
    for item in crlib.load_json(os.path.join(review_dir, "sources.json"), {}).get("items", []):
        if item.get("read_error"):
            print("NOTE: %s %s 没有读到(%s);审查照常进行" % (item["label"], item.get("url", ""), item["read_error"]))
    if missing:
        labels = [conf["kinds"][k]["label"] for k in missing]
        print("SOURCES_MISSING: %s 还没有提供(%s)。" % ("、".join(labels), ",".join(missing)))
    if conf.get("optional"):
        labels = [conf["kinds"][k]["label"] for k in conf["optional"]]
        print("SOURCES_OPTIONAL: 可选的%s还没有提供(%s);可以跳过,不算缺,跳过后这个分支不再问。" % ("、".join(labels), ",".join(conf["optional"])))


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1]))
