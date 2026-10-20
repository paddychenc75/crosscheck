---
# 引用的组织规则包(在组织规则仓库的 packs/ 下);没有组织规则仓库时删掉这一行
extends: []
# 关掉某条组织规则,必须写原因:
# disable:
#   - ORG-RPC-004: 本项目是网关,超时由上游统一设置
# 不审查的路径
skip: []
# 条数上限:defects(缺陷)、intent(需求核对)、rules(规范)、simplify(可简化),或按 critical/major/minor
# limits: { minor: 3 }
# 按规则编号指定严重程度
# severity: { PRJ-002: critical }
# 高风险路径:改动命中时建议用 deep 深度
# risk_paths: ["src/payment/**"]
---
# Review 约定

## 严重程度

只有会导致行为错误、数据泄漏或无法回滚的问题才算重要及以上。风格、命名、重构建议最高为轻微。

## 每次都要检查

<!-- 每条规则一句能对照代码检查的话,加一句原因;下一行是元数据。从一两条开始,反复漏掉或反复误报时再加。 -->
- 日期格式化统一用 `src/utils/date.ts`,不要直接调用 dayjs。原因:时区统一在那里处理。
  <!-- cr: id=PRJ-001 scope=src/** severity=minor src=manual added=2026-10 -->
