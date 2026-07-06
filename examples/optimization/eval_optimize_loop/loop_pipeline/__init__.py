# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""eval_optimize_loop 的六阶段闭环实现包。

包名叫 ``loop_pipeline`` 而不是 ``pipeline``：
``examples/optimization/multi_agent_pipeline`` 已经占用了顶层包名
``pipeline``，同进程 import 两个 example 时会在 ``sys.modules`` 里撞名。

模块分工（与 issue 的六个阶段一一对应）：

- :mod:`.evaluate`    阶段① / ④ 共用的评测执行与逐 case 记录提取
- :mod:`.attribution` 阶段② 失败归因（6 类失败类型聚类）
- :mod:`.optimize`    阶段③ AgentOptimizer 封装（场景 → 配置/数据集选择）
- :mod:`.regression`  阶段④ 候选换入/换出 + 逐 case delta 对比
- :mod:`.gates`       阶段⑤ 可配置接受策略（六道闸门）
- :mod:`.report`      阶段⑥ optimization_report.json / .md 渲染与校验
- :mod:`.config`      pipeline.json（闸门阈值 / seed）的读取
"""
