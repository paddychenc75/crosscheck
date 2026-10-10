#!/bin/sh
# guard.sh — 宿主的 hook:cr 审查进行期间,拦截会改变仓库状态的操作。
#
# 同一个脚本适配三个宿主,从 stdin 读取 hook 的 JSON,按字段判断是谁在调用:
#   Claude Code / Codex   hooks/hooks.json         PreToolUse、UserPromptSubmit
#   Cursor                hooks/cursor-hooks.json  beforeShellExecution、preToolUse、beforeSubmitPrompt
#
#   开始  真正执行 collect-diff.sh 时(查看、搜索、语法检查它不算),为当前会话记下"审查进行中"
#   结束  collect-diff.sh 报错或没有可审查的内容、运行 check-state.sh 或 save-review.sh、
#         用户发来新消息,或超过 CR_GUARD_TTL 秒(默认 3600)
#   期间  拒绝改变仓库状态的 git / gh / glab 命令,以及对仓库内文件的编辑;
#         拒绝可能把凭证显示出来的命令和读取(打印环境变量、查看 rc 文件和凭证文件等)
#
# 这是防止误操作的护栏,不是安全边界:它按空白切分命令来识别子命令,
# 包在 sh -c "..." 里的命令识别不到。没有 jq 和 python3 时不做任何拦截。

set -u

input=$(cat)

get() { # jq 风格的路径,如 .tool_input.command 或 .workspace_roots[0]
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r "$1 // empty" 2>/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$input" | python3 -c '
import json, re, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for k in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\[\d+\]", sys.argv[1]):
    if k.startswith("["):
        i = int(k[1:-1])
        d = d[i] if isinstance(d, list) and i < len(d) else None
    else:
        d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else d)' "$1" 2>/dev/null
  else
    return 1
  fi
}

event=$(get .hook_event_name) || exit 0
case "$event" in
  beforeShellExecution|afterShellExecution|beforeReadFile|beforeSubmitPrompt|preToolUse) host=cursor ;;
  *) if [ -n "$(get .cursor_version)" ]; then host=cursor; else host=claude; fi ;;
esac

# Cursor 要求权限类 hook 输出合法的 JSON;Claude Code 和 Codex 放行时不输出。
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

case "$event" in
  UserPromptSubmit|beforeSubmitPrompt) rm -f "$marker"; allow ;;
esac

tool=$(get .tool_name)
case "$event" in beforeShellExecution|afterShellExecution) tool=Shell ;; esac
[ "$event" = beforeReadFile ] && tool=Read
case "$tool" in
  Bash|Shell|shell) kind=shell; cmd=$(get .tool_input.command); [ -n "$cmd" ] || cmd=$(get .command) ;;
  apply_patch) kind=patch; cmd=$(get .tool_input.command) ;;
  *) kind=file; cmd="" ;;
esac

# 命令里是否真的执行了名为 $1 的脚本。cat、grep、编辑器、sh -n 这类只是提到它的不算,
# 这样开发或排查这个插件时不会被当成开始了一次审查。
runs() {
  printf '%s\n' "$cmd" | awk -v name="$1" '
  function base(s) { gsub(/["\047]/, "", s); sub(/^.*\//, "", s); return s }
  function check(a, b,   k, w) {
    while (a <= b && (t[a] == "" || t[a] ~ /^[A-Za-z_][A-Za-z0-9_]*=/)) a++
    if (a > b) return
    for (k = a; k <= b; k++) if (t[k] == "--help" || t[k] == "-h") return
    w = base(t[a])
    if (w == name) { found = 1; return }
    if (w !~ /^(sh|bash|zsh|dash|ksh)$/) return
    for (k = a + 1; k <= b; k++) {
      if (t[k] ~ /^-/) { if (t[k] ~ /^-[A-Za-z]*[nc]/) return; continue }
      if (base(t[k]) == name) { found = 1; return }
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

# 命令执行之后:collect-diff.sh 失败了,或者说没有可审查的内容,就不存在进行中的审查,立刻解除。
case "$event" in
  PostToolUse|PostToolUseFailure|afterShellExecution)
    if [ -f "$marker" ] && [ "$kind" = shell ] && runs collect-diff.sh; then
      result=$(get .tool_response); [ -n "$result" ] || result=$(get .output); [ -n "$result" ] || result=$(get .error)
      case "$event:$result" in
        PostToolUseFailure:*|*"EMPTY: "*|*"UNCHANGED: "*|*"collect-diff: "*) rm -f "$marker" ;;
      esac
    fi
    exit 0 ;;
esac

if [ "$kind" = shell ]; then
  case "$cmd" in
    *check-state.sh*--save*) ;;
    *check-state.sh*|*save-review.sh*) rm -f "$marker"; allow ;;
  esac
  if runs collect-diff.sh; then mkdir -p "$dir" && date +%s > "$marker"; allow; fi
fi

[ -f "$marker" ] || allow
started=$(cat "$marker" 2>/dev/null || echo 0)
case "$started" in ""|*[!0-9]*) started=0 ;; esac
if [ $(($(date +%s) - started)) -gt "${CR_GUARD_TTL:-3600}" ]; then
  rm -f "$marker"
  allow
fi

script_dir=$(cd "$(dirname "$0")" && pwd)
deny() {
  case "$1" in
    "!"*) msg="cr 审查进行中,已拦截: ${1#!}。这类操作可能把凭证显示出来。登录状态和远程地址由脚本自己判断:看 collect-diff.sh 输出的 NOTE 和 meta.txt 里的 repo_id,不要自己去查环境变量、shell 配置或凭证文件。" ;;
    *) msg="cr 审查进行中(只读),已拦截: $1。读其他版本的代码请用 git show / git diff / git log。如果审查已经结束,先运行 sh $script_dir/check-state.sh <review_dir> 结束只读阶段。" ;;
  esac
  esc=$(printf '%s' "$msg" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n\t' '  ')
  if [ "$host" = cursor ]; then
    printf '{"permission":"deny","user_message":"%s","agent_message":"%s"}\n' "$esc" "$esc"
  else
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$esc"
  fi
  exit 0
}

cwd=$(get .cwd)
[ -n "$cwd" ] || cwd=$(get '.workspace_roots[0]')
in_repo() { # 路径是否在当前仓库内;相对路径按 cwd 解析
  [ -n "$cwd" ] || return 1
  root=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || return 1
  case "$1" in
    /*) p=$1 ;;
    *) p="$cwd/$1" ;;
  esac
  case "$p" in "$root"/*) return 0 ;; esac
  return 1
}

# 只认用户主目录下的 shell 配置和凭证文件,以及 gh/glab 自己的配置。
# 仓库里同名的文件(比如 dotfiles 仓库里的 .zshrc、项目里的 .env.example)是正常的审查对象,不拦。
secret_file() {
  h=${HOME:-/nonexistent}
  case "$1" in
    "$h"/.zshrc|"$h"/.zshenv|"$h"/.zprofile|"$h"/.zlogin|"$h"/.bashrc|"$h"/.bash_profile|"$h"/.bash_login|"$h"/.profile|"$h"/.netrc|"$h"/.git-credentials) return 0 ;;
    "~"/.zshrc|"~"/.zshenv|"~"/.zprofile|"~"/.zlogin|"~"/.bashrc|"~"/.bash_profile|"~"/.bash_login|"~"/.profile|"~"/.netrc|"~"/.git-credentials) return 0 ;;
    */glab-cli/config.yml|*/glab-cli/hosts.yml|*/gh/config.yml|*/gh/hosts.yml) return 0 ;;
  esac
  return 1
}

if [ "$kind" = file ]; then
  case "$tool" in
    Read|read|ReadFile|read_file)
      path=$(get .tool_input.file_path); [ -n "$path" ] || path=$(get .tool_input.path); [ -n "$path" ] || path=$(get .file_path)
      [ -n "$path" ] && secret_file "$path" && deny "!读取 ${path##*/}"
      allow ;;
  esac
  case "$tool" in
    Edit|Write|MultiEdit|NotebookEdit|Delete|StrReplace|edit|write|delete) ;;
    *) allow ;;
  esac
  for key in .tool_input.file_path .tool_input.notebook_path .tool_input.path .tool_input.target_file; do
    path=$(get "$key")
    [ -n "$path" ] && break
  done
  [ -n "$path" ] && in_repo "$path" && deny "编辑仓库内的文件 $path"
  allow
fi

if [ "$kind" = patch ]; then
  printf '%s\n' "$cmd" | sed -nE 's/^\*\*\* (Add|Update|Delete) File: //p' > "$dir/$sid.paths" 2>/dev/null || allow
  while IFS= read -r path; do
    if in_repo "$path"; then rm -f "$dir/$sid.paths"; deny "编辑仓库内的文件 $path"; fi
  done < "$dir/$sid.paths"
  rm -f "$dir/$sid.paths"
  allow
fi

hit=$(printf '%s\n' "$cmd" | awk -v home="${HOME:-/nonexistent}" '
function bad(s) { print s; found = 1; exit }
function secret(full,   np, pre, i2, rest) { # 主目录下的 shell 配置和凭证文件,或 gh/glab 的配置
  gsub(/["\047]/, "", full)
  if (full ~ /\/(glab-cli|gh)\/(config|hosts)\.yml$/) return 1
  pre[1] = "~/"; pre[2] = "$HOME/"; pre[3] = "${HOME}/"; pre[4] = home "/"
  for (i2 = 1; i2 <= 4; i2++)
    if (index(full, pre[i2]) == 1) {
      rest = substr(full, length(pre[i2]) + 1)
      if (rest ~ /^\.(zshrc|zshenv|zprofile|zlogin|bashrc|bash_profile|bash_login|profile|netrc|git-credentials)$/) return 1
    }
  return 0
}
function api(tool, k,   m, w, gq, mut) {
  m = ""; w = 0; gq = 0; mut = 0
  for (; k <= n && t[k] != ";"; k++) {
    if (t[k] == "graphql") gq = 1
    if (t[k] ~ /mutation/) mut = 1
    if (t[k] == "--method" || t[k] == "-X") m = toupper(t[k + 1])
    else if (t[k] ~ /^--method=/) m = toupper(substr(t[k], 10))
    else if (t[k] ~ /^(-f|-F|--field|--raw-field|--input)$/) w = 1
  }
  if (gq && !mut) return   # GraphQL 的查询也是 POST 加字段,不含 mutation 时只是读取
  if ((m != "" && m != "GET") || (m == "" && w)) bad(tool " api 写请求")
}
{
  gsub(/[;|&()`]/, " ; ")
  n = split($0, t, /[ \t]+/)
  for (i = 1; i <= n; i++) {
    w = t[i]; sub(/^.*\//, "", w)
    if (secret(t[i])) { q = t[i]; gsub(/["\047]/, "", q); sub(/^.*\//, "", q); bad("!访问 " q) }
    if (w == "git") {
      j = i + 1
      while (j <= n && t[j] ~ /^-/) {
        if (t[j] == "-C" || t[j] == "-c" || t[j] == "--git-dir" || t[j] == "--work-tree" || t[j] == "--namespace") j++
        j++
      }
      s = t[j]; a = t[j + 1]
      if (s ~ /^(checkout|switch|restore|reset|clean|add|rm|mv|commit|merge|rebase|cherry-pick|revert|pull|push|apply|am)$/) bad("git " s)
      if (s == "stash" && a !~ /^(list|show)$/) bad("git stash")
      if (s == "remote" && a ~ /^(-v|--verbose|get-url|show)$/) bad("!git remote " a)
      if (s == "config")
        for (k = j + 1; k <= n && t[k] != ";"; k++)
          if (t[k] ~ /(remote\..*url|credential|^-l$|^--list$|--get-regexp)/) bad("!git config " t[k])
      if (s == "credential" || s == "credential-osxkeychain") bad("!git " s)
      if (s == "worktree" && a ~ /^(add|remove|move|prune)$/) bad("git worktree " a)
      if (s == "branch")
        for (k = j + 1; k <= n && t[k] != ";"; k++)
          if (t[k] ~ /^(-d|-D|-m|-M|-f|--delete|--move|--force)$/) bad("git branch " t[k])
    } else if (w == "printenv" || (w == "env" && (i == n || t[i + 1] == ";" || t[i + 1] == "")) || (w == "export" && t[i + 1] == "-p")) {
      bad("!" w "(打印环境变量)")
    } else if (w == "echo" || w == "printf") {
      for (k = i + 1; k <= n && t[k] != ";"; k++)
        if (t[k] ~ /\$\{?[A-Za-z_]*(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY)/) bad("!输出凭证类环境变量")
    } else if (w == "gh") {
      s = t[i + 1]; a = t[i + 2]
      if (s == "auth" && a == "token") bad("!gh auth token")
      if (s == "auth" && a == "status") for (k = i + 3; k <= n && t[k] != ";"; k++) if (t[k] ~ /^(-t|--show-token)$/) bad("!gh auth status " t[k])
      if (s == "pr" && a ~ /^(checkout|merge|close|reopen|review|comment|edit|create|ready)$/) bad("gh pr " a)
      if (s == "api") api("gh", i + 2)
    } else if (w == "glab") {
      s = t[i + 1]; a = t[i + 2]
      if (s == "auth" && a == "status") for (k = i + 3; k <= n && t[k] != ";"; k++) if (t[k] ~ /^(-t|--show-token)$/) bad("!glab auth status " t[k])
      if (s == "config" && a == "get" && t[i + 3] ~ /token/) bad("!glab config get token")
      if (s == "mr" && a ~ /^(checkout|merge|approve|revoke|close|reopen|note|update|create|rebase|delete)$/) bad("glab mr " a)
      if (s == "api") api("glab", i + 2)
    }
  }
}
')
[ -n "$hit" ] && deny "$hit"
allow
