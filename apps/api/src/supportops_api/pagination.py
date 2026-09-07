"""列表接口统一分页参数与响应元数据。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Query

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PaginationParams:
    """经过 FastAPI 校验的页码参数。"""

    page: int
    page_size: int

    @property
    def offset(self) -> int:
        """将页码转换为数据库查询使用的偏移量。"""

        return (self.page - 1) * self.page_size


@dataclass(frozen=True, slots=True)
class PaginationMetadata:
    """列表响应统一返回的分页元数据。"""

    total: int
    page: int
    page_size: int
    pages: int


def pagination_params(
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_SIZE, description="每页数量，最大 100"),
    ] = DEFAULT_PAGE_SIZE,
) -> PaginationParams:
    """构造所有普通列表接口共用的分页参数。"""

    return PaginationParams(page=page, page_size=page_size)


def pagination_metadata(total: int, pagination: PaginationParams) -> PaginationMetadata:
    """根据总数和查询参数计算标准分页响应字段。"""

    pages = (total + pagination.page_size - 1) // pagination.page_size
    return PaginationMetadata(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )
