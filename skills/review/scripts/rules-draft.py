#!/usr/bin/env python3
"""统一维护草稿命令;实现见 rules_draft.py。"""
import sys
sys.dont_write_bytecode = True
import rules_draft

if __name__ == "__main__":
    sys.exit(rules_draft.main())
