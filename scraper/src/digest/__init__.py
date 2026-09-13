"""日报聚合：网站新闻聚成事件，每个事件下挂微博讨论。

不在这里做再导出：sender.py 依赖 digest.store，runner 又依赖 sender，
包级 import 会构成循环。使用方直接从子模块导入。
"""
