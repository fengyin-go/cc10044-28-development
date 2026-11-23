"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import column_share, resource

# 受控列共享是固定路径集合，需放在动态 /api/{module} 资源路由之前注册。
ROUTERS = [column_share, resource]
