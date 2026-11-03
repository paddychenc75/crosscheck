# gh / glab 读不到平台时怎么问用户

review、describe、improve 共用。下面这些情况按本文处理:

- 脚本输出「gh:没有登录」「glab:没有登录」「gh:没有安装」「glab:没有安装」;
- 脚本的 `NOTE:` 说登录变量写在某个 shell 配置文件里、当前终端没有;
- `mine-mrs.sh` 报「读不到这个项目的 MR 列表」「读不到这个仓库的 PR 列表」;
- 用户说「我终端里能用,这里却说没登录」。

## 先分清是哪一层

| 现象 | 多半是 | 怎么办 |
|---|---|---|
| 没有安装 | 机器上没有这个 CLI | 让用户安装,不替用户装 |
| 没有登录,用户也没在别处登录过 | 确实没登录 | 选项 A |
| 用户的终端里能用,这里不行;或者 `NOTE:` 指出变量在 shell 配置文件里 | 登录靠的是环境变量(如 `GITLAB_TOKEN`、`GH_TOKEN`),宿主没把它传给命令 | 选项 A 或 B |
| 已登录但读不到这个 MR / PR | 编号不对或没有权限 | 照原话转告,不涉及登录 |

判断只用脚本的输出、`gh auth status` / `glab auth status` 的退出状态和用户的说法。

## 不要做

- 不打印、不搜索 token,不读 shell 配置文件和凭证文件,不检查环境变量的值。
- 不自己改宿主的全局配置(如 `~/.codex/config.toml`、宿主的 settings)、shell 配置或权限设置,也不把 token 的值写进任何文件。
- 不换别的 CLI、MCP 或平台重试,不绕过 origin 绑定。
- 不要一次给出一串互相矛盾的方案,也不要把建议说成官方要求。

## 问用户

交互运行时一次问清,给出下面的选项,标出推荐项,等用户选。问的时候顺带提示一句:本插件只读平台,不写任何东西,新建 token 时只给读权限就够了,例如 GitLab 勾 `read_api`,GitHub 用细粒度 token、对目标仓库只给 Contents、Pull requests、Issues 的只读。这只是建议,用户已有的 token 能用就不必换。非交互运行不问:review、describe 照脚本的说明改用 git 拉代码并披露,improve 照原话报错停止。

**A. 让 gh / glab 自己保存登录(推荐)。** 和宿主无关,换宿主或重启都不受影响,也不用在配置里放行 token。请用户在自己的终端里运行(命令会交互提问,agent 不代为输入 token):

- GitHub:`gh auth login`,确认用 `gh auth status`。
- GitLab:`glab auth login --hostname <GitLab 地址>`,确认用 `glab auth status --hostname <GitLab 地址>`。

用户的终端里本来就设了 token 变量时,在这两条命令前加 `env -u GITLAB_TOKEN`(GitHub 是 `env -u GH_TOKEN -u GITHUB_TOKEN`)。否则 CLI 会直接用变量而不保存登录,确认时也只验证到了变量。登录方式的选择(Token、Web、Device)按用户所在平台的要求来,自建 GitLab 没配 OAuth 应用时只能选 Token。

**B. 让宿主把这一个变量传给命令。** 适合团队规定只能用环境变量认证的情况。按用户所在的宿主说明:

- Codex:命令能继承哪些环境变量由 `~/.codex/config.toml` 的 `[shell_environment_policy]` 决定,`inherit = "core"` 只带基础变量,token 不在其中。请用户按 Codex 官方配置说明只放行这一个变量名,不要改成全部继承,也不要把值写进配置。还有一个前提:Codex 自己启动时环境里就得有这个变量。
- Claude Code、Cursor:命令继承宿主启动时的环境。从 Dock 或启动台打开时,通常读不到只写在 `~/.zshrc` 里的变量。可以从已设好变量的终端启动宿主,或者把设置挪到 `~/.zshenv`。

改完要重启宿主,再回到对话说「继续」。用户明确要求 agent 代改配置时,先备份原文件,只加变量名,改完给用户看 diff。

**C. 这次先不读平台。**
- review、describe:改用 git 拉代码,标题、描述和讨论缺失会在报告里写明。
- improve:不能从平台沉淀,可以先检查或整理本地规则。

## 用户处理完之后

带原来的参数重跑同一个命令。还是失败时,照新的输出重新判断属于哪一层,不要沿用上一次的猜测。
