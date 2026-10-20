---
pack: platform/rpc
prefix: ORG-RPC
owner: "@rpc-team"
applies_to: ["**/*.go"]
description: 内部 RPC 框架的调用约定
---
- 调用内部 RPC 必须设置超时。原因:默认无超时,下游卡住会拖垮调用方。
  <!-- cr: id=ORG-RPC-001 severity=major src=manual added=2026-10 -->
