---
name: calculator_add_bug
description: The add function in calculator.py is buggy.
type: project_fact
---

In /home/cjs/agent/dataset/fixtures/fix_add_multiturn_compact/calculator.py, add(left, right) returns left - right instead of left + right. The test expects add(2, 3) == 5.
