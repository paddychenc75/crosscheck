---
# 不审查的路径
skip: []
# 条数上限:defects(缺陷)、intent(需求核对)、rules(规范)、simplify(可简化),或按 critical/major/minor
# limits: { minor: 3 }
# 高风险路径:改动命中时建议用 deep 深度
# risk_paths: ["src/payment/**"]
---
# Review 约定

## 严重程度

只有会导致行为错误、数据泄漏或无法回滚的问题才算重要及以上。风格、命名、重构建议最高为轻微。

## 每次都要检查

<!-- 每条规则一句能对照代码检查的话,加一句原因。从一两条开始,反复漏掉或反复误报时再加。 -->
- 日期格式化统一用 `src/utils/date.ts`,不要直接调用 dayjs。原因:时区统一在那里处理。
