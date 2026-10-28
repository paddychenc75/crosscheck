"""token 用量统计(usage.sh):Claude Code 和 Codex 的会话记录。"""
import json
import os

import fixture

OLD, NEW = "2000-01-01T00:00:00.000Z", "2999-01-01T00:00:00.000Z"


def codex_record(resp, inp, cached, out, reasoning=0, ts=NEW):
    u = {"input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
         "output_tokens": out, "reasoning_output_tokens": reasoning, "total_tokens": inp + out}
    return {"timestamp": ts, "type": "token_usage_record",
            "payload": {"thread_id": "t", "response_id": resp, "usage": u, "turn_token_usage": u}}


def session_meta(sid, parent=None, nested=True):
    p = {"id": sid, "source": "cli"}
    if parent and nested:
        p["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1}}}
    elif parent:
        p.update(source={"subagent": {"other": "guardian"}}, parent_thread_id=parent)
    return {"timestamp": OLD, "type": "session_meta", "payload": p}


class CodexUsage(fixture.Base):
    def write(self, path, rows, mode="w"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_main_session_and_subagents_after_start(self):
        day = os.path.join(self.tmp, "codex", "sessions", "2026", "10", "27")
        main = os.path.join(day, "rollout-main.jsonl")
        state, out = os.path.join(self.tmp, "state"), os.path.join(self.tmp, "usage.txt")
        # 审查开始前的调用不算
        self.write(main, [session_meta("A"), codex_record("r0", 5000, 4000, 100)])
        self.run_script("usage.sh", "start", state, main)
        self.write(main, [codex_record("r1", 1000, 600, 50, 10),
                          codex_record("r1", 1000, 600, 50, 10),          # 同一次调用写了两次
                          codex_record("r2", 2000, 1500, 70)], mode="a")
        # 子代理、子代理的子代理、guardian 都算;别的会话的子代理不算;子代理里开始前的记录不算
        self.write(os.path.join(day, "rollout-child.jsonl"),
                   [session_meta("B", "A"), codex_record("c0", 900, 0, 9, ts=OLD), codex_record("c1", 300, 100, 30)])
        self.write(os.path.join(day, "rollout-grandchild.jsonl"), [session_meta("C", "B"), codex_record("g1", 200, 0, 20)])
        self.write(os.path.join(day, "rollout-guardian.jsonl"), [session_meta("G", "A", nested=False), codex_record("q1", 100, 0, 10)])
        self.write(os.path.join(day, "rollout-other.jsonl"), [session_meta("X", "Z"), codex_record("x1", 99999, 0, 999)])
        self.run_script("usage.sh", "finish", state, out)
        kv = self.kv(out)
        self.assertEqual(kv["usage_source"], "codex")
        self.assertEqual(kv["usage_subagents"], "included")
        self.assertEqual(kv["usage_model_calls"], "5")
        # input 不含缓存:(1000-600)+(2000-1500)+(300-100)+200+100
        self.assertEqual(kv["usage_input_tokens"], str(400 + 500 + 200 + 200 + 100))
        self.assertEqual(kv["usage_cache_read_tokens"], str(600 + 1500 + 100))
        self.assertEqual(kv["usage_output_tokens"], str(50 + 70 + 30 + 20 + 10))
        self.assertEqual(kv["usage_reasoning_tokens"], "10")
        total = int(kv["usage_input_tokens"]) + int(kv["usage_cache_read_tokens"]) + int(kv["usage_output_tokens"])
        self.assertEqual(kv["usage_total_tokens"], str(total))

    def test_no_records_writes_nothing(self):
        main = os.path.join(self.tmp, "codex", "sessions", "rollout-main.jsonl")
        state, out = os.path.join(self.tmp, "state"), os.path.join(self.tmp, "usage.txt")
        self.write(main, [session_meta("A")])
        self.run_script("usage.sh", "start", state, main)
        self.run_script("usage.sh", "finish", state, out)
        self.assertFalse(os.path.exists(out))

    def test_claude_still_counted(self):
        main = os.path.join(self.tmp, "claude", "s.jsonl")
        state, out = os.path.join(self.tmp, "state"), os.path.join(self.tmp, "usage.txt")
        self.write(main, [{"type": "user"}])
        self.run_script("usage.sh", "start", state, main)
        msg = {"id": "m1", "usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 5, "output_tokens": 7}}
        self.write(main, [{"type": "assistant", "message": msg}, {"type": "assistant", "message": msg}], mode="a")
        self.run_script("usage.sh", "finish", state, out)
        kv = self.kv(out)
        self.assertEqual((kv["usage_source"], kv["usage_total_tokens"], kv["usage_model_calls"]), ("claude", "122", "1"))

    def test_actual_models_from_main_and_subagents(self):
        main = os.path.join(self.tmp, "claude", "s.jsonl")
        sub = os.path.join(self.tmp, "claude", "s", "subagents", "a.jsonl")
        state, out = os.path.join(self.tmp, "state"), os.path.join(self.tmp, "usage.txt")
        self.write(main, [{"type": "user"}])
        self.run_script("usage.sh", "start", state, main)

        def turn(mid, model, n):
            return {"type": "assistant", "timestamp": NEW, "message": {"id": mid, "model": model, "usage": {"input_tokens": n, "output_tokens": 1}}}
        self.write(main, [turn("m1", "claude-haiku-5-5", 10), turn("m2", "<synthetic>", 0)], mode="a")
        self.write(sub, [turn("s1", "claude-sonnet-5-5", 20)])
        self.run_script("usage.sh", "finish", state, out)
        kv = self.kv(out)
        # 只记模型名;<synthetic> 不是模型,不记
        self.assertEqual(kv["usage_models"], "claude-haiku-5-5,claude-sonnet-5-5")
        self.assertEqual(kv["usage_total_tokens"], str(10 + 20 + 3))   # 输入 30,输出 3 条各 1

    def test_codex_model_comes_from_turn_context_before_start(self):
        main = os.path.join(self.tmp, "codex", "sessions", "rollout-main.jsonl")
        state, out = os.path.join(self.tmp, "state"), os.path.join(self.tmp, "usage.txt")
        # 模型名在回合开始时写的,早于审查开始
        self.write(main, [session_meta("A"), {"timestamp": OLD, "type": "turn_context", "payload": {"model": "gpt-5.1-codex"}}])
        self.run_script("usage.sh", "start", state, main)
        self.write(main, [codex_record("r1", 1000, 600, 50)], mode="a")
        self.run_script("usage.sh", "finish", state, out)
        self.assertEqual(self.kv(out)["usage_models"], "gpt-5.1-codex")
