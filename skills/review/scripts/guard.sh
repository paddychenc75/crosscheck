#!/bin/sh
# guard.sh — 宿主的 hook:记录 cr 审查的开始和结束,用来统计这次审查消耗的 token。
#
# 同一个脚本适配三个宿主,从 stdin 读取 hook 的 JSON,按字段判断是谁在调用:
#   Claude Code   hooks/hooks.json          PreToolUse、PostToolUse、PostToolUseFailure、UserPromptSubmit
#   Codex         hooks/codex-hooks.json    PreToolUse、PostToolUse、UserPromptSubmit
#   Cursor        hooks/cursor-hooks.json   beforeShellExecution、afterShellExecution、beforeSubmitPrompt
#
#   开始  真正执行 collect-diff.sh 时(查看、搜索、语法检查它不算),记下会话的"审查进行中"标记,并开始统计用量
#   结束  collect-diff.sh 失败或没有可审查的内容、save-review.sh 保存成功、用户发来新消息时清除标记
#   用量  保存前调用 usage.sh 把这次审查的 token 写进报告目录;Cursor 没有用量,报告里写明
#
# 这个脚本不拒绝任何命令。只读要求靠说明文字,审查结束后由 check-state.sh 核对仓库状态。
# 没有 python3 时读不到 hook 的字段,什么都不做。

set -u

input=$(cat)

get() { # 取 JSON 里的字段,路径写法如 .tool_input.command 或 .workspace_roots[0]
  printf '%s' "$input" | python3 -c '
import json, re, sys
d = json.load(sys.stdin)
for k in re.findall(r"\w+|\[\d+\]", sys.argv[1]):
    if isinstance(d, dict):
        d = d.get(k)
    elif isinstance(d, list) and k.startswith("[") and int(k[1:-1]) < len(d):
        d = d[int(k[1:-1])]
    else:
        d = None
print("" if d is None else d)' "$1" 2>/dev/null
}

event=$(get .hook_event_name) || exit 0
case "$event" in
  beforeShellExecution|afterShellExecution|beforeSubmitPrompt) host=cursor ;;
  *) if [ -n "$(get .cursor_version)" ]; then host=cursor; else host=claude; fi ;; # Codex 的输入格式与 Claude Code 相同
esac

# Cursor 要求权限类 hook 输出合法的 JSON;Claude Code 和 Codex 什么都不输出。
allow() {
  if [ "$host" = cursor ]; then
    if [ "$event" = beforeSubmitPrompt ]; then printf '{"continue":true}\n'; else printf '{"permission":"allow"}\n'; fi
  fi
  exit 0
}

sid=$(get .session_id)
[ -n "$sid" ] || sid=$(get .conversation_id)
sid=$(printf '%s' "$sid" | tr -cd 'A-Za-z0-9_-')
[ -n "$sid" ] || allow
dir="${TMPDIR:-/tmp}/cr-guard"
marker="$dir/$sid"
script_dir=$(cd "$(dirname "$0")" && pwd)

case "$event" in UserPromptSubmit|beforeSubmitPrompt) rm -f "$marker"; allow ;; esac

tool=$(get .tool_name)
case "$event" in beforeShellExecution|afterShellExecution) tool=Shell ;; esac
case "$tool" in Bash|Shell) ;; *) allow ;; esac
cmd=$(get .tool_input.command); [ -n "$cmd" ] || cmd=$(get .command)
[ -n "$cmd" ] || allow

# 命令里是否真的执行了名为 $1 的脚本。只是提到它(cat、grep、编辑器)、只做语法检查(sh -n)或带 --help 不算。
runs() {
  printf '%s\n' "$cmd" | awk -v name="$1" '
  function base(s) { gsub(/["\047]/, "", s); sub(/^.*\//, "", s); return s }
  function check(a, b,   k, w) { # 第 a 到 b 个词是一条简单命令;跳过变量赋值和只起分组作用的词
    while (a <= b && (t[a] == "" || t[a] ~ /^[A-Za-z_][A-Za-z0-9_]*=/ || t[a] ~ /^(\{|\}|!|then|do|else)$/)) a++
    if (a > b) return
    for (k = a; k <= b; k++) if (t[k] == "--help" || t[k] == "-h") return
    w = base(t[a])
    if (w == name) found = 1
    else if (w ~ /^(sh|bash|zsh|dash|ksh)$/)
      for (k = a + 1; k <= b && !found; k++) {
        if (t[k] ~ /^-[A-Za-z]*[nc]/) break
        if (t[k] !~ /^-/) { if (base(t[k]) == name) found = 1; break }
      }
  }
  {
    gsub(/[;|&()`]/, " ; ")
    n = split($0, t, /[ \t]+/)
    start = 1
    for (i = 1; i <= n + 1; i++)
      if (i == n + 1 || t[i] == ";") { check(start, i - 1); start = i + 1 }
  }
  END { exit found ? 0 : 1 }'
}

# 命令执行之后:collect-diff.sh 失败或没有可审查的内容就清除标记;成功则记下报告目录。
case "$event" in
  PostToolUse|PostToolUseFailure|afterShellExecution)
    for key in .tool_response.stdout .output .tool_response .error; do
      result=$(get "$key"); [ -n "$result" ] && break
    done
    # 只有 Claude Code 有单独的失败事件;Codex 和 Cursor 靠脚本自己的报错前缀识别失败。
    failed=no; [ "$event" = PostToolUseFailure ] && failed=yes
    if [ -f "$marker" ]; then
      if runs collect-diff.sh; then
        case "$failed:$result" in
          yes:*|*"EMPTY: "*|*"UNCHANGED: "*|*"collect-diff: "*) rm -f "$marker" "$dir/$sid.review" ;;
          *)
            rd=$(printf '%s\n' "$result" | awk 'NF { last = $0 } END { print last }' | sed 's/^[" ]*//; s/[" ]*$//')
            if [ -n "$rd" ] && [ -f "$rd/meta.txt" ]; then
              printf '%s\n' "$rd" > "$dir/$sid.review"
            fi ;;
        esac
      elif [ "$failed" = no ] && runs save-review.sh && printf '%s\n' "$result" | grep -q '^report='; then
        rm -f "$marker" "$dir/$sid.review"
      fi
    fi
    exit 0 ;;
esac

# 保存之前统计这次审查的 token。review_dir 优先取 collect-diff 执行完时记下的目录;
# 命令里常写成 $D 这样的变量,按文字解析会拿到字面量,所以只在没有记录时才解析命令文字。
if runs save-review.sh; then
  review_dir=$(cat "$dir/$sid.review" 2>/dev/null)
  [ -n "$review_dir" ] || review_dir=$(printf '%s\n' "$cmd" | awk '{
    gsub(/["\047]/, "")
    for (i = 1; i < NF; i++) if ($i ~ /(^|\/)save-review\.sh$/) { print $(i + 1); exit }
  }')
  if [ -n "$review_dir" ] && [ -f "$review_dir/meta.txt" ]; then
    sh "$script_dir/usage.sh" finish "$dir/$sid.usage" "$review_dir/usage.txt" >/dev/null 2>&1 || true
    # 统计不到时写明原因,让报告能区分"hook 没运行"和"hook 运行了但读不到用量"
    if [ ! -s "$review_dir/usage.txt" ]; then
      if [ "$host" = cursor ]; then reason=unsupported
      elif [ -f "$dir/$sid.usage" ]; then reason=no_records
      else reason=no_start; fi
      printf 'usage_status=%s\n' "$reason" > "$review_dir/usage.txt" 2>/dev/null || true
    fi
  fi
  rm -f "$dir/$sid.usage"
fi

if runs collect-diff.sh; then
  mkdir -p "$dir" && : > "$marker"
  rm -f "$dir/$sid.review" "$dir/$sid.usage"
  transcript=$(get .transcript_path)
  [ -n "$transcript" ] && sh "$script_dir/usage.sh" start "$dir/$sid.usage" "$transcript" >/dev/null 2>&1 || true
fi

allow
