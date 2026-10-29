#!/usr/bin/env python3
"""entrypoints.py — 从代码里认出改动从哪里触发、怎么验证,供 describe_facts.py 调用;不调用模型、不运行项目代码。

  scan(lang, text)         路由、命令行参数和子命令、定时任务、事件/队列名:[{kind, label, line, handler}]
  test_cases(lang, lines)  改动行里新增的测试用例名:[(名字, 行号)]
  assertions(lines)        改动行里新增的断言原文:[(原文, 行号)]
  test_command(read)       项目自己的测试命令:package.json 的 scripts.test 或 Makefile 的 test 目标,没有就是空串
只认 TS/JS、Go、Python、Java/Kotlin 的常见写法和 YAML 里的 cron 表达式,一行一行匹配;认不出就不输出,不猜。
lines 是 analysis_facts.parse_patch 里一个文件各 hunk 的行:(标记, 文本, 旧行号, 新行号)。
"""
import json
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "review", "scripts"))
import analysis_facts as af  # noqa: E402

KIND_NAME = {"route": "路由", "cli_flag": "命令行参数", "cli_command": "子命令", "cron": "定时任务", "event": "事件/队列"}
CRON_EXPR = re.compile(r"^(@(yearly|annually|monthly|weekly|daily|midnight|hourly)|@every\s+\S+|(\S+\s+){4,6}\S+)$")
HANDLER = re.compile(r",\s*([A-Za-z_$][\w$.]*)\s*\)\s*[;,]?\s*$")
QUOTED = re.compile(r"[\"'`]([^\"'`]*)[\"'`]")
S = r"[\"'`]"          # 引号
NS = r"[^\"'`]"        # 引号以外


def flags(args):
    """参数列表里以 - 开头的字面量,长选项优先:'-c, --cursor <v>' 或 '"-c", "--cursor"' 都得到 --cursor。"""
    found = re.findall(r"(?<![\w-])(--?[A-Za-z][\w-]*)", " ".join(QUOTED.findall(args)))
    longs = [f for f in found if f.startswith("--")]
    return (longs or found or [None])[0]


def path_join(*parts):
    return "/" + "/".join(p.strip("/") for p in parts if p and p.strip("/"))


def route(method, path):
    m = (method or "").upper()
    return ("%s %s" % (m, path) if m in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS") else path) if path else None


def cron(expr):
    expr = (expr or "").strip()
    return expr if CRON_EXPR.match(expr) else None


def py_route(m, ctx):
    """Flask 的 route、FastAPI 的 api_route 看 methods=[...] 的第一个;没写方法就只给路径。"""
    method = m.group(1)
    if method in ("route", "api_route"):
        ms = re.search(r"methods\s*=\s*\[([^\]]*)\]", m.group(3))
        method = (re.findall(r"[\"'](\w+)[\"']", ms.group(1)) or [""])[0] if ms else ""
    return route(method, m.group(2))


def scheduled(m, ctx):
    """@Scheduled(cron = "…") 给表达式,fixedRate / fixedDelay 给 名字=值。"""
    x = re.search(r"cron\s*=\s*\"([^\"]+)\"|(fixedRate|fixedDelay)\s*=\s*(\w+)", m.group(1))
    return (x.group(1) or "%s=%s" % (x.group(2), x.group(3))) if x else None


# (语言, 正则, 类别, 取名字的函数, 处理函数怎么找):处理函数 inline 是同一行最后一个标识符参数,group 是正则的第 2 组,
# next 是下面最近的定义
RULES = [
    # TS/JS:Express / Koa / Fastify、NestJS、commander / yargs、node-cron、Nest 定时任务和消息
    (("ts", "js"), r"\b[A-Za-z_$][\w$]*\.(get|post|put|patch|delete|head|options|all)\(\s*%s(/%s*)%s" % (S, NS, S),
     "route", lambda m, c: route(m.group(1), m.group(2)), "inline"),
    (("ts", "js"), r"@(Get|Post|Put|Patch|Delete|Head|Options|All)\(\s*(?:%s(%s*)%s)?\s*\)" % (S, NS, S),
     "route", lambda m, c: route(m.group(1), path_join(c.get("nest", ""), m.group(2) or "")), "next"),
    (("ts", "js"), r"\.(?:option|requiredOption)\(\s*%s(-%s+)%s" % (S, NS, S), "cli_flag", lambda m, c: flags(m.group(0)), None),
    (("ts", "js"), r"\.option\(\s*%s([A-Za-z][\w-]*)%s\s*,\s*\{" % (S, S), "cli_flag", lambda m, c: "--" + m.group(1), None),
    (("ts", "js"), r"\.command\(\s*%s([A-Za-z][\w:-]*)" % S, "cli_command", lambda m, c: m.group(1), None),
    (("ts", "js"), r"@Cron\(\s*(%s%s+%s|CronExpression\.\w+)" % (S, NS, S), "cron", lambda m, c: m.group(1).strip("\"'`"), "next"),
    (("ts", "js"), r"\b\w+\.schedule(?:Job)?\(\s*%s(%s+)%s" % (S, NS, S), "cron", lambda m, c: cron(m.group(1)), "inline"),
    (("ts", "js"), r"@(?:EventPattern|MessagePattern|OnEvent|Process|Processor|OnQueueEvent)\(\s*%s(%s+)%s" % (S, NS, S),
     "event", lambda m, c: m.group(1), "next"),
    (("ts", "js"), r"\.(?:subscribe|consume|publish)\(\s*%s([\w.:/-]+)%s" % (S, S), "event", lambda m, c: m.group(1), "inline"),
    (("ts", "js"), r"\btopics?:\s*\[?\s*%s([\w.:/-]+)%s" % (S, S), "event", lambda m, c: m.group(1), None),
    # Python:Flask / FastAPI、Django、argparse、click / typer、Celery / APScheduler、Kafka / RabbitMQ
    (("python",), r"@[A-Za-z_][\w.]*\.(get|post|put|patch|delete|head|options|route|api_route)\(\s*[\"'](/[^\"']*)[\"'](.*)",
     "route", py_route, "next"),
    (("python",), r"(?<![\w.])(?:path|re_path)\(\s*r?[\"']([^\"']*)[\"']\s*,\s*(?!include\b)([A-Za-z_][\w.]*)",
     "route", lambda m, c: path_join(m.group(1).lstrip("^").rstrip("$")) or "/", "group"),
    (("python",), r"\.add_argument\((.*)", "cli_flag", lambda m, c: flags(m.group(1)), None),
    (("python",), r"\.add_parser\(\s*[\"']([\w:-]+)[\"']", "cli_command", lambda m, c: m.group(1), None),
    (("python",), r"@[\w.]*\boption\((.*)", "cli_flag", lambda m, c: flags(m.group(1)), "next"),
    (("python",), r"@[\w.]+\.command\(\s*(?:name\s*=\s*)?[\"']([\w:-]+)[\"']", "cli_command", lambda m, c: m.group(1), "next"),
    (("python",), r"\bcrontab\(([^)]*)\)", "cron", lambda m, c: "crontab(%s)" % re.sub(r"\s+", " ", m.group(1).strip()), "inline"),
    (("python",), r"(?:@[\w.]+\.scheduled_job|\.add_job)\((?:[^)]*?,\s*)?[\"'](cron|interval)[\"']\s*,?\s*([^)]*)\)",
     "cron", lambda m, c: ("%s %s" % (m.group(1), re.sub(r"\s+", " ", m.group(2).strip()))).strip(), "next"),
    (("python",), r"\bKafkaConsumer\(\s*[\"']([^\"']+)[\"']", "event", lambda m, c: m.group(1), None),
    (("python",), r"\.(?:subscribe|publish|send_task)\(\s*\[?\s*[\"']([\w.:/-]+)[\"']", "event", lambda m, c: m.group(1), None),
    (("python",), r"@[\w.]*\b(?:task|shared_task)\([^)]*\bname\s*=\s*[\"']([^\"']+)[\"']", "event", lambda m, c: m.group(1), "next"),
    (("python",), r"\.(?:basic_consume|queue_declare)\([^)]*\bqueue\s*=\s*[\"']([\w.:-]+)[\"']", "event", lambda m, c: m.group(1), None),
    # Go:gin / echo / chi / fiber、net/http、flag、cobra、robfig/cron、NATS / AMQP / kafka-go
    (("go",), r"\b\w+\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Any|Get|Post|Put|Patch|Delete|Head|Options)\(\s*\"(/[^\"]*)\"",
     "route", lambda m, c: route(m.group(1), m.group(2)), "inline"),
    (("go",), r"\b\w+\.(?:HandleFunc|Handle)\(\s*\"(?:(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?(/[^\"]*)\"",
     "route", lambda m, c: route(m.group(1), m.group(2)), "inline"),
    (("go",), r"\bflag\.(?:String|Bool|Int|Int64|Uint|Uint64|Float64|Duration|Func)(?:Var)?\((?:\s*&?[\w.\[\]]+\s*,)?\s*\"([\w.-]+)\"",
     "cli_flag", lambda m, c: "-" + m.group(1), None),
    (("go",), r"\.(?:Persistent)?Flags\(\)\.\w+?\((?:\s*&?[\w.\[\]]+\s*,)?\s*\"([\w-]+)\"", "cli_flag", lambda m, c: "--" + m.group(1), None),
    (("go",), r"\bUse:\s*\"([\w:-]+)", "cli_command", lambda m, c: m.group(1), None),
    (("go",), r"\.Add(?:Func|Job)\(\s*\"([^\"]+)\"", "cron", lambda m, c: cron(m.group(1)), "inline"),
    (("go",), r"\.(?:Subscribe|QueueSubscribe|ChanSubscribe|Publish|Consume|QueueDeclare)\(\s*\"([\w.:/>*-]+)\"",
     "event", lambda m, c: m.group(1), "inline"),
    (("go",), r"\bTopic:\s*\"([\w.:/-]+)\"", "event", lambda m, c: m.group(1), None),
    # Java/Kotlin:Spring MVC、picocli、@Scheduled、Kafka / RabbitMQ / JMS / SQS 监听和发送
    (("java", "kotlin"), r"@(Get|Post|Put|Patch|Delete|Request)Mapping\b(?:\((.*)\))?", "route", None, "next"),
    (("java", "kotlin"), r"@Option\(\s*names\s*=\s*(.*)", "cli_flag", lambda m, c: flags(m.group(1)), "next"),
    (("java", "kotlin"), r"@Command\(\s*name\s*=\s*\"([\w:-]+)\"", "cli_command", lambda m, c: m.group(1), "next"),
    (("java", "kotlin"), r"@Scheduled\((.*)\)", "cron", scheduled, "next"),
    (("java", "kotlin"), r"@(?:KafkaListener|RabbitListener|JmsListener|SqsListener|StreamListener)\((.*)\)",
     "event", lambda m, c: (QUOTED.findall(m.group(1)) or [None])[0], "next"),
    (("java", "kotlin"), r"\b\w*[Tt]emplate\.(?:send|convertAndSend)\(\s*\"([^\"]+)\"", "event", lambda m, c: m.group(1), None),
    # 配置:Kubernetes CronJob、GitHub Actions 等 YAML 里的 cron 表达式
    (("config",), r"^\s*-?\s*(?:schedule|cron)\s*:\s*[\"']?([^\"'#]+?)[\"']?\s*(?:#.*)?$", "cron", lambda m, c: cron(m.group(1)), None),
]
RULES = [(langs, re.compile(rx), kind, label, how) for langs, rx, kind, label, how in RULES]


def next_def(lang, lines, i):
    """第 i 行(装饰器、注解)下面最近的定义:(名字, 类别);6 行内没有就是 None。"""
    for text in lines[i + 1:i + 7]:
        d = af.match_def(lang, text) if lang != "config" else None
        if d:
            return d[0], d[1]
    return None


def spring(m, lang, lines, i, ctx):
    """Spring 的 @XxxMapping:类上的 @RequestMapping 是前缀,方法上的才是一个路由。"""
    args = m.group(2) or ""
    p = re.search(r"(?:value|path)\s*=\s*\{?\s*\"([^\"]*)\"", args) or re.match(r"\s*\{?\s*\"([^\"]*)\"", args)
    path = p.group(1) if p else ""
    d = next_def(lang, lines, i)
    if d and d[1] == "class":
        ctx["spring"] = path
        return None
    method = m.group(1)
    if method == "Request":
        rm = re.search(r"RequestMethod\.(\w+)", args)
        method = rm.group(1) if rm else ""
    return route(method, path_join(ctx.get("spring", ""), path))


def scan(lang, text):
    """一个文件里认得出的入口;同一行同一个入口只算一次。"""
    if not text or lang not in ("ts", "js", "python", "go", "java", "kotlin", "config"):
        return []
    lines, out, ctx, seen = text.split("\n"), [], {}, set()
    for i, line in enumerate(lines):
        if lang in ("ts", "js") and (c := re.search(r"@Controller\(\s*(?:\{[^}]*path:\s*)?(?:%s(%s*)%s)?" % (S, NS, S), line)):
            ctx["nest"] = c.group(1) or ""
            continue
        for langs, rx, kind, label, how in RULES:
            if lang not in langs:
                continue
            m = rx.search(line)
            if not m:
                continue
            name = spring(m, lang, lines, i, ctx) if label is None else label(m, ctx)
            if not name or (kind, name, i) in seen:
                continue
            seen.add((kind, name, i))
            handler = None
            if how == "inline" and (h := HANDLER.search(line)):
                handler = h.group(1).rsplit(".", 1)[-1]
            elif how == "group":
                handler = m.group(2).rsplit(".", 1)[-1]
            elif how == "next" and (d := next_def(lang, lines, i)):
                handler = d[0]
            out.append({"kind": kind, "label": name, "line": i + 1, "handler": handler})
            break
    return out


# ---------- 测试用例和断言 ----------

CASES = {
    "ts": [re.compile(r"\b(?:it|test)(?:\.only|\.concurrent|\.each\(.*?\))?\(\s*(?P<q>[\"'`])(?P<name>.+?)(?P=q)")],
    "python": [re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>test\w*)\s*\(")],
    "go": [re.compile(r"^func\s+(?P<name>Test\w+)\s*\(\s*\w+\s+\*testing\.T\s*\)"), re.compile(r"\bt\.Run\(\s*\"(?P<name>[^\"]+)\"")],
}
CASES["js"] = CASES["ts"]
JAVA_TEST = re.compile(r"@(?:Test|ParameterizedTest|RepeatedTest)\b")
JAVA_CASE = re.compile(r"(?:\bvoid|\bfun)\s+(?:`(?P<tick>[^`]+)`|(?P<name>\w+))\s*\(")
ASSERT = re.compile(r"\b(?:expect\s*\(|assert(?:_\w+|[A-Z]\w*)?\s*\(|assert\s|self\.assert\w+\(|assertThat\(|require\.\w+\(|"
                    r"assert\.\w+\(|t\.(?:Errorf|Fatalf|Error|Fatal)\()")
MAX_ASSERT_LEN = 80


def _names(lang, lines):
    """每行认出的用例名:[(标记, 名字, 新行号)]。Java/Kotlin 要 @Test 之后最近的方法。"""
    out, pending = [], False
    for tag, text, _, new_no in lines:
        if lang in ("java", "kotlin"):
            if JAVA_TEST.search(text):
                pending = True
            m = JAVA_CASE.search(text) if pending else None
            if m:
                out.append((tag, m.group("tick") or m.group("name"), new_no))
                pending = False
            continue
        for rx in CASES.get(lang, []):
            for m in rx.finditer(text):
                out.append((tag, m.group("name"), new_no))
    return out


def test_cases(lang, lines):
    """改动后新出现的用例名(改动前同名的不算新增)。"""
    found = _names(lang, lines)
    old = {n for t, n, _ in found if t == "-"}
    out, seen = [], set()
    for t, n, line in found:
        if t == "+" and n not in old and n not in seen:
            seen.add(n)
            out.append((n, line))
    return out


def assertions(lines):
    out = []
    for tag, text, _, new_no in lines:
        s = text.strip()
        if tag == "+" and len(s) >= 8 and ASSERT.search(s):
            out.append((s if len(s) <= MAX_ASSERT_LEN else s[:MAX_ASSERT_LEN - 1] + "…", new_no))
    return out


# ---------- 项目测试命令 ----------

def test_command(read):
    """read(相对路径) 返回文件内容,没有这个文件返回 None。只认 package.json 的 scripts.test 和 Makefile 的 test 目标。"""
    try:
        scripts = json.loads(read("package.json") or "{}").get("scripts") or {}
        test = str(scripts.get("test") or "").strip() if isinstance(scripts, dict) else ""
    except (ValueError, AttributeError):
        test = ""
    if test and "no test specified" not in test:
        return "%s test" % ("pnpm" if read("pnpm-lock.yaml") is not None else "yarn" if read("yarn.lock") is not None else "npm")
    if re.search(r"^test\s*:(?!=)", read("Makefile") or "", re.M):
        return "make test"
    return ""
