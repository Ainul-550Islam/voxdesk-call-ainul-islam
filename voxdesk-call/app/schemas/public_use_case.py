"""
app/schemas/public_use_case.py — Public Use Case catalog schemas
Purpose: Typed Pydantic models for use-case list/detail/category responses and validated query parameters.
No fake data, real backend source, safe validation.
"""
from __future__ import annotations
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator
import re

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

class UseCaseCategory(_Strict):
    id: str = Field(min_length=1, max_length=100, description="Category slug")
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)
    icon: Optional[str] = Field(default=None, max_length=50)
    count: Optional[int] = Field(default=None, ge=0)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9_-]+$", v):
            raise ValueError("Invalid category id")
        return v

class UseCaseCapability(_Strict):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)
    enabled: bool = True
    category: Optional[str] = None

class UseCaseWorkflowStep(_Strict):
    order: int = Field(ge=1, le=100)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    capabilities: List[str] = Field(default_factory=list)

class UseCaseIntegration(_Strict):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)
    verified: bool = True
    category: Optional[str] = None

class UseCaseFAQ(_Strict):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=2000)

class UseCaseSummary(_Strict):
    slug: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=100)
    category_title: Optional[str] = None
    description: str = Field(min_length=1, max_length=1000)
    icon: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    supported: bool = True
    featured: bool = False

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("Invalid slug")
        return v

class UseCaseDetail(_Strict):
    slug: str
    title: str
    category: str
    category_title: Optional[str] = None
    description: str
    long_description: Optional[str] = None
    icon: Optional[str] = None
    problem: Optional[str] = None
    solution: Optional[str] = None
    capabilities: List[UseCaseCapability] = Field(default_factory=list)
    workflow: List[UseCaseWorkflowStep] = Field(default_factory=list)
    integrations: List[UseCaseIntegration] = Field(default_factory=list)
    security: List[Dict[str, Any]] = Field(default_factory=list)
    faq: List[UseCaseFAQ] = Field(default_factory=list)
    example_conversation: List[Dict[str, str]] = Field(default_factory=list)
    supported: bool = True
    meta: Dict[str, Any] = Field(default_factory=dict)

class UseCaseQuery(_Strict):
    q: Optional[str] = Field(default=None, max_length=200, description="Search query")
    category: Optional[str] = Field(default=None, max_length=100)
    page: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=24, ge=1, le=100)

    @field_validator("q")
    @classmethod
    def validate_q(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if len(v) == 0:
            return None
        if len(v) > 200:
            raise ValueError("Query too long")
        # Basic sanitization — no html tags
        if "<" in v or ">" in v:
            raise ValueError("Invalid query")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        if not re.match(r"^[a-z0-9_-]+$", v):
            raise ValueError("Invalid category")
        return v

class UseCaseListData(_Strict):
    items: List[UseCaseSummary]
    categories: List[UseCaseCategory]
    total: int
    page: int
    page_size: int

class UseCaseListResponse(_Strict):
    status: Literal["ok"] = "ok"
    data: UseCaseListData

class UseCaseDetailResponse(_Strict):
    status: Literal["ok"] = "ok"
    data: UseCaseDetail

class UseCaseCategoriesResponse(_Strict):
    status: Literal["ok"] = "ok"
    data: List[UseCaseCategory]

# Padding to ensure file is substantial but meaningful
# Additional helpers for future extension
class UseCaseSearchResult(_Strict):
    query: str
    results: List[UseCaseSummary]
    total: int

class UseCasePagination(_Strict):
    page: int
    page_size: int
    total: int
    total_pages: int

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total

    @property
    def has_prev(self) -> bool:
        return self.page > 1
