"""entrypoints.py:从代码里认出路由、命令行、定时任务、事件、新增用例和项目测试命令;认不出就不输出。"""
import json
import os
import sys

import fixture

sys.path.insert(0, os.path.join(fixture.DESCRIBE_SCRIPTS))
import entrypoints as ep  # noqa: E402


def found(lang, text):
    return [(e["kind"], e["label"], e["line"], e["handler"]) for e in ep.scan(lang, text)]


def added(*texts):
    """把几行文本当作新增行:(标记, 文本, 旧行号, 新行号)。"""
    return [("+", t, None, i + 1) for i, t in enumerate(texts)]


class Scan(fixture.Base):
    def test_typescript(self):
        text = ("router.post('/orders', createOrder)\n"
                "app.get(\"/health\", health);\n"
                "@Controller('orders')\n"
                "export class OrdersController {\n"
                "  @Get(':id')\n"
                "  findOne(id: string) {\n"
                "  }\n"
                "}\n"
                "program.option('-c, --cursor <v>', 'cursor')\n"
                "program.command('sync')\n"
                "cron.schedule('*/5 * * * *', tick)\n"
                "@EventPattern('orders.created')\n"
                "  onCreated(data: Order) {\n")
        self.assertEqual(found("ts", text), [
            ("route", "POST /orders", 1, "createOrder"),
            ("route", "GET /health", 2, "health"),
            ("route", "GET /orders/:id", 5, "findOne"),
            ("cli_flag", "--cursor", 9, None),
            ("cli_command", "sync", 10, None),
            ("cron", "*/5 * * * *", 11, "tick"),
            ("event", "orders.created", 12, "onCreated"),
        ])

    def test_python(self):
        text = ("@app.route('/orders', methods=['POST'])\n"
                "def create_order():\n"
                "    pass\n"
                "@router.get(\"/orders/{id}\")\n"
                "def get_order(id):\n"
                "    pass\n"
                "urlpatterns = [path('orders/', views.list_orders)]\n"
                "parser.add_argument('--cursor', type=str)\n"
                "sub.add_parser('sync')\n"
                "@app.task(name='orders.sync')\n"
                "def sync():\n"
                "    pass\n"
                "'schedule': crontab(minute=0, hour=3)\n")
        self.assertEqual(found("python", text), [
            ("route", "POST /orders", 1, "create_order"),
            ("route", "GET /orders/{id}", 4, "get_order"),
            ("route", "/orders", 7, "list_orders"),
            ("cli_flag", "--cursor", 8, None),
            ("cli_command", "sync", 9, None),
            ("event", "orders.sync", 10, "sync"),
            ("cron", "crontab(minute=0, hour=3)", 13, None),
        ])

    def test_go(self):
        text = ('r.POST("/orders", h.CreateOrder)\n'
                'mux.HandleFunc("GET /health", health)\n'
                'flag.StringVar(&cursor, "cursor", "", "")\n'
                'cmd.Flags().String("limit", "", "")\n'
                'Use: "sync [flags]",\n'
                'c.AddFunc("@every 1m", tick)\n'
                'nc.Subscribe("orders.created", onCreated)\n')
        self.assertEqual(found("go", text), [
            ("route", "POST /orders", 1, "CreateOrder"),
            ("route", "GET /health", 2, "health"),
            ("cli_flag", "-cursor", 3, None),
            ("cli_flag", "--limit", 4, None),
            ("cli_command", "sync", 5, None),
            ("cron", "@every 1m", 6, "tick"),
            ("event", "orders.created", 7, "onCreated"),
        ])

    def test_java_and_kotlin(self):
        text = ('@RequestMapping("/api/orders")\n'
                "public class OrdersController {\n"
                '  @PostMapping("/{id}/cancel")\n'
                "  public void cancel() {}\n"
                '  @RequestMapping(value = "/list", method = RequestMethod.GET)\n'
                "  public void list() {}\n"
                '  @Scheduled(cron = "0 0 3 * * *")\n'
                "  public void nightly() {}\n"
                '  @KafkaListener(topics = "orders")\n'
                "  public void onOrder() {}\n"
                "}\n")
        for lang in ("java", "kotlin"):
            with self.subTest(lang=lang):
                self.assertEqual(found(lang, text), [
                    ("route", "POST /api/orders/{id}/cancel", 3, "cancel"),
                    ("route", "GET /api/orders/list", 5, "list"),
                    ("cron", "0 0 3 * * *", 7, "nightly"),
                    ("event", "orders", 9, "onOrder"),
                ])

    def test_yaml_cron(self):
        self.assertEqual(found("config", 'spec:\n  schedule: "*/10 * * * *"\n'), [("cron", "*/10 * * * *", 2, None)])

    def test_nothing_is_guessed(self):
        self.assertEqual(found("ts", "const total = items.length\nlog('hello')\nfoo.get(key)\n"), [])
        self.assertEqual(found("python", "x = d.get('/not-a-route')\nprint('a')\n"), [])
        self.assertEqual(found("config", "schedule: weekdays\n"), [])
        # 认不出的语言、空文本都不输出
        for lang, text in (("rust", 'app.get("/x", h)'), ("shell", "echo a"), (None, "app.get('/x', h)"), ("ts", ""), ("ts", None)):
            with self.subTest(lang=lang):
                self.assertEqual(found(lang, text), [])


class Cases(fixture.Base):
    def names(self, lang, *texts):
        return ep.test_cases(lang, added(*texts))

    def test_names_per_language(self):
        self.assertEqual(self.names("ts", 'it("returns next cursor", () => {', "test('b', async () => {"), [("returns next cursor", 1), ("b", 2)])
        self.assertEqual(self.names("python", "def test_cursor(self):", "    def helper(self):"), [("test_cursor", 1)])
        self.assertEqual(self.names("go", "func TestCursor(t *testing.T) {", '\tt.Run("empty", func(t *testing.T) {'),
                         [("TestCursor", 1), ("empty", 2)])
        self.assertEqual(self.names("java", "@Test", "void returnsCursor() {"), [("returnsCursor", 2)])
        self.assertEqual(self.names("kotlin", "@Test", "fun `keeps page`() {"), [("keeps page", 2)])
        self.assertEqual(self.names("rust", "#[test]", "fn a() {}"), [])

    def test_a_case_that_was_already_there_is_not_new(self):
        lines = [("-", 'it("old", () => {', 1, None), ("+", 'it("old", async () => {', None, 1), ("+", 'it("fresh", () => {', None, 2)]
        self.assertEqual(ep.test_cases("ts", lines), [("fresh", 2)])

    def test_assertions(self):
        got = ep.assertions(added("expect(a).toBe(1)", "const x = 1", "self.assertEqual(a, b)", "assert x == 1", "ok()"))
        self.assertEqual([t for t, _ in got], ["expect(a).toBe(1)", "self.assertEqual(a, b)", "assert x == 1"])
        long = ep.assertions(added("expect(" + "a" * 120 + ").toBe(1)"))[0][0]
        self.assertTrue(len(long) <= ep.MAX_ASSERT_LEN and long.endswith("…"))


class Command(fixture.Base):
    def command(self, files):
        return ep.test_command(files.get)

    def test_package_scripts_and_lockfile(self):
        pkg = json.dumps({"scripts": {"test": "jest"}})
        self.assertEqual(self.command({"package.json": pkg}), "npm test")
        self.assertEqual(self.command({"package.json": pkg, "pnpm-lock.yaml": ""}), "pnpm test")
        self.assertEqual(self.command({"package.json": pkg, "yarn.lock": ""}), "yarn test")

    def test_makefile_target(self):
        self.assertEqual(self.command({"Makefile": "build:\n\tgo build\ntest:\n\tgo test ./...\n"}), "make test")

    def test_nothing_when_unrecognized(self):
        self.assertEqual(self.command({}), "")
        self.assertEqual(self.command({"package.json": json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})}), "")
        self.assertEqual(self.command({"package.json": "{bad"}), "")
        self.assertEqual(self.command({"Makefile": "build:\n\techo\n"}), "")


class InFacts(fixture.Base):
    """整条链路:改动里新增的路由、迁移、测试用例出现在 describe-facts.json 的 entrypoints 里。"""

    def test_route_cases_and_command(self):
        repo = self.make_repo("e2e", {
            "src/orders.ts": "export function listOrders(page: number) {\n  return page\n}\n",
            "src/routes.ts": "import { listOrders } from './orders'\nrouter.get('/orders', listOrders)\n",
            "package.json": json.dumps({"scripts": {"test": "jest"}}) + "\n",
        })
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {
            "src/orders.ts": "export function listOrders(opts: { cursor?: string }) {\n  return opts\n}\n"
                             "export function exportOrders() {\n  return 1\n}\n",
            "src/routes.ts": "import { listOrders, exportOrders } from './orders'\nrouter.get('/orders', listOrders)\n"
                             "router.post('/orders/export', exportOrders)\n",
            "src/orders.test.ts": 'it("accepts a cursor", () => {\n  expect(listOrders({ cursor: "a" })).toBeTruthy()\n})\n',
        }, "改")
        self.git(repo, "switch", "-q", "main")
        r = self.run_describe("collect.sh", "feature", "--non-interactive", cwd=repo)
        data = self.load(os.path.join(self.review_dir(r.stdout), "describe-facts.json"))["entrypoints"]
        self.assertEqual([(c["change"], c["label"], c["handler"], c["file"]) for c in data["changes"]],
                         [("added", "POST /orders/export", "exportOrders", "src/routes.ts")])
        labels = {t["label"] for t in data["triggers"]}
        self.assertEqual(labels, {"GET /orders", "POST /orders/export"})   # listOrders 签名变了,调用它的路由也是触发方式
        self.assertEqual([(c["name"], c["file"], c["line"]) for c in data["cases"]], [("accepts a cursor", "src/orders.test.ts", 1)])
        self.assertEqual([a["text"] for a in data["assertions"]], ['expect(listOrders({ cursor: "a" })).toBeTruthy()'])
        self.assertEqual(data["command"], "npm test")

    def test_unrecognized_language_emits_nothing(self):
        repo = self.make_repo("rs", {"src/lib.rs": "pub fn a() -> i32 {\n    1\n}\n"})
        self.git(repo, "switch", "-qc", "feature")
        self.commit(repo, {"src/lib.rs": 'pub fn a() -> i32 {\n    2\n}\nfn route() { app.get("/x", h); }\n'}, "改")
        self.git(repo, "switch", "-q", "main")
        r = self.run_describe("collect.sh", "feature", "--non-interactive", cwd=repo)
        data = self.load(os.path.join(self.review_dir(r.stdout), "describe-facts.json"))["entrypoints"]
        self.assertEqual((data["changes"], data["triggers"], data["cases"], data["assertions"], data["command"]), ([], [], [], [], ""))
