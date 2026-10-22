# 旧 Git 规则来源样例

此模板仅说明兼容的 Git 格式，不是使用 Crosscheck 的前提。组织规则由外部维护，插件只读消费；评测目录可独立配置，插件不再向本仓库创建规则或评测 MR。

团队共用的审查规则。每个规则包是 `packs/` 下的一个目录,由 `CODEOWNERS` 里写的负责人维护。

项目在自己的 `REVIEW.md` 头部引用需要的规则包:

```markdown
---
extends: [platform/rpc]
disable:
  - ORG-RPC-004: 本项目是 RPC 网关,超时由上游统一设置
---
```

各人在 `~/.crosscheck/config` 里配置 `rules_repo` 和 `rules_ref`(建议跟踪 tag),然后运行 `sync-rules.sh`。

## 目录

| 路径 | 内容 |
|---|---|
| `packs/<包>/rules.md` | 规则正文,头部写包名、编号前缀、负责人、适用路径 |
| `packs/<包>/examples/` | 每条规则一对正反例:`<编号>.bad.<扩展名>` 应该被报出,`<编号>.good.<扩展名>` 不应该 |
| `evals/cases/*.json` | 评测用例 |
| `evals/baseline.json` | 当前的评测基线 |
| `retired-ids` | 删除过的规则编号,不再分配 |
| `CHANGELOG.md` | 每次发版改了哪些规则 |

## 规则包

见 [CONTRIBUTING.md](CONTRIBUTING.md)。
