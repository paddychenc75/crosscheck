# 发现结构

每个角色都以单个 JSON 数组的形式返回发现,不附带任何其他内容。没有可报告的内容时返回 `[]` — 结果为空是正常的、好的结果。

```json
[
  {
    "file": "src/orders/paginate.ts",
    "line": 42,
    "category": "bug",
    "severity": "major",
    "summary": "total 恰好是 pageSize 的整数倍时,最后一页被丢掉",
    "failure_scenario": "total=40, pageSize=20 → pageCount 算出 1,第 21-40 条永远不会返回",
    "suggestion": "改用 Math.ceil(total / pageSize)"
  }
]
```

| 字段 | 规则 |
|---|---|
| `file` | 相对仓库根目录的路径,与 `files.txt` 中的写法完全一致。 |
| `line` | diff **新**侧的行号,且落在 `ranges.txt` 中该文件的某个范围内。指向出错的那一行,而不是函数的起始行。 |
| `category` | `bug`、`security`、`rules` 或 `simplify`。 |
| `severity` | `critical` — 数据丢失、安全漏洞、常见路径上崩溃。`major` — 用户或调用方会遇到的错误行为。`minor` — 真实存在但影响小。`simplify` 发现一律为 `minor`。 |
| `summary` | 用一句话陈述缺陷。不要用含糊的措辞("可能"、"或许会"、"建议考虑")。 |
| `failure_scenario` | 具体的输入或状态 → 错误的结果。对 `rules`,逐字引用规则原文并指出它来自哪个文件。对 `simplify`,指出让这段改动变得多余的已有代码,或可以合并的具体行。 |
| `suggestion` | 可选。能修复问题的最小改动。能确定时直接给出替换后的代码(可以原样替换掉 `line` 所在的那一行或那几行),并说明替换的是哪几行;只能确定方向时用一句话描述;不确定怎么修就省略。 |
| `since` | 由编排者填写,角色不用管。这条发现首次被报告的时间,用于增量审查时标出遗留问题。 |

`category`、`severity` 等枚举值保持英文原样。`summary`、`failure_scenario`、`suggestion` 用中文撰写;代码、标识符、路径和引用的规则原文保持原样。

## 硬性要求

- **没有场景,就没有发现。** 如果写不出具体的 `failure_scenario`,那只是怀疑,不是发现。继续调查,或者放弃。
- **只看改动的行。** 缺陷必须是这次 diff 引入或暴露的。未改动代码中原本就存在的问题,无论多真实,都不在范围内。
- **一个缺陷一条发现。** 如果同样的错误重复出现,报告第一处,其余的在 `summary` 中提及。

verifier 会给它保留的发现加上两个字段:`confidence`(0-100)和 `verdict`(用一句话解释分数)。候选里已有的其他字段(如 `since`)原样保留。
