"""纠正台的领域模型。

源身份的识别规则（见资料约定）：issuer（登记单位）与 local_id（源编号）
合起来才唯一识别一次源登记；源编号只在同一登记单位内不重复。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceIdentity:
    """一个单位在自己系统内登记的原始身份。"""

    issuer: str
    local_id: str
    label: str

    @property
    def key(self) -> tuple[str, str]:
        return self.issuer, self.local_id


@dataclass
class DirectorySubject:
    """旧目录主体，可能归并了多个来源登记。"""

    subject: str
    identities: list[SourceIdentity] = field(default_factory=list)

    @property
    def merged(self) -> bool:
        return len(self.identities) > 1


@dataclass
class EvidenceRecord:
    """各来源系统保有的一条业务记录及其作者证据。

    candidates 为来源声称的可能归属（源身份键），空列表表示来源尚无可
    采信的关联；credential 为空表示该记录不挂凭据。
    """

    owner: str
    id: str
    issuer: str
    candidates: list[tuple[str, str]]
    credential: str
    supplements: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Reviewer:
    """核验员：source 为其可核的来源业务，person 为核验员实际身份。"""

    id: str
    source: str
    person: str


@dataclass(frozen=True)
class LoginSession:
    """纠正前已存在的登录，引用旧目录主体并带有若干业务面权限。"""

    id: str
    subject: str
    origin: str
    authorities: frozenset[str]
