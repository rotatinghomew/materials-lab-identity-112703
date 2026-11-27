"""一次身份归属纠正案件的聚合逻辑。

案件围绕一个被误归并的旧目录主体展开：先由对来源有授权的核验员逐个
确认源身份，再按各系统保有的作者证据核清记录归属；无法核清的只做隔离，
留给数据责任人；已核清关系的改动必须凭新证据走可见的新决定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import (
    DecisionLocked,
    ReauthenticationFailed,
    RecordNotClaimable,
    SelfReviewConflict,
    UnauthorizedReviewer,
    UnknownSourceIdentity,
)
from .models import EvidenceRecord, Reviewer, SourceIdentity


class RecordState(str, Enum):
    PENDING = "pending"          # 归属尚无定论
    ATTRIBUTED = "attributed"    # 已核清到某个源身份
    ISOLATED = "isolated"        # 不能确认，隔离待数据责任人复核


@dataclass(frozen=True)
class Decision:
    """一条可见的归属决定（追加留痕，不覆盖历史）。"""

    at: int
    reviewer_id: str
    record_id: str
    target: tuple[str, str]
    source_record: str
    supersedes: bool             # 是否为改动既有核清结果的新决定


@dataclass
class HistoryEntry:
    at: int | None
    kind: str
    note: str


@dataclass
class CorrectionCase:
    subject: str
    opened_at: int | None = None
    identities: dict[tuple[str, str], SourceIdentity] = field(default_factory=dict)
    confirmations: dict[tuple[str, str], int] = field(default_factory=dict)
    confirmers: dict[tuple[str, str], str] = field(default_factory=dict)
    records: dict[str, EvidenceRecord] = field(default_factory=dict)
    decisions: dict[str, list[Decision]] = field(default_factory=dict)
    isolated: dict[str, int] = field(default_factory=dict)
    reauthenticated: dict[str, tuple[tuple[str, str], int]] = field(default_factory=dict)
    held_supplements: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    stale_submissions: list[tuple[int, str, str]] = field(default_factory=list)
    audit: list[HistoryEntry] = field(default_factory=list)
    history: dict[str, list[HistoryEntry]] = field(default_factory=dict)

    # ---- 立案与基础查询 -------------------------------------------------

    def open(self, at: int) -> None:
        if self.opened_at is None:
            self.opened_at = at
            self.audit.append(HistoryEntry(at, "复核申请", f"主体 {self.subject} 立案纠正"))

    def identity(self, issuer: str, local_id: str) -> SourceIdentity:
        key = (issuer, local_id)
        try:
            return self.identities[key]
        except KeyError as exc:
            raise UnknownSourceIdentity(f"{issuer}/{local_id} 不在本案源身份中") from exc

    def is_confirmed(self, key: tuple[str, str]) -> bool:
        return key in self.confirmations

    # ---- 核验员授权与回避 ----------------------------------------------

    def _check_reviewer(self, reviewer: Reviewer, source: str, person_key: tuple[str, str]) -> None:
        if reviewer.source != source:
            raise UnauthorizedReviewer(
                f"核验员 {reviewer.id} 无 {source} 来源业务授权"
            )
        # 本人与审核者同人：核验员实际身份就是被核的源编号，须另请核验员。
        if reviewer.person == person_key[1]:
            raise SelfReviewConflict(
                f"核验员 {reviewer.id} 与被核身份 {person_key[0]}/{person_key[1]} 同人，须另请核验员"
            )

    # ---- 源身份确认 -----------------------------------------------------

    def confirm_identity(
        self, reviewer: Reviewer, issuer: str, local_id: str, at: int
    ) -> Decision:
        identity = self.identity(issuer, local_id)
        self._check_reviewer(reviewer, issuer, identity.key)
        if identity.key not in self.confirmations:
            self.confirmations[identity.key] = at
            self.confirmers[identity.key] = reviewer.id
            self.audit.append(
                HistoryEntry(
                    at,
                    "目录复核",
                    f"源身份 {issuer}/{local_id}（{identity.label}）由 {reviewer.id} 确认",
                )
            )
        return Decision(at, reviewer.id, f"directory:{issuer}/{local_id}", identity.key, "", False)

    # ---- 来源材料补交 ---------------------------------------------------

    def submit_supplement(self, record_id: str, source_record: str, at: int) -> None:
        record = self.records[record_id]
        if source_record in record.supplements:
            self.stale_submissions.append((at, record_id, source_record))
            return
        if self.record_state(record_id) is RecordState.ATTRIBUTED:
            # 已核清记录的新材料只登记为待议，不能自行翻案；须另起可见新决定。
            self.held_supplements.setdefault(record_id, []).append((at, source_record))
            self.audit.append(
                HistoryEntry(
                    at, "来源补交", f"{record_id} 新材料 {source_record} 待新决定审议，不改动现归属"
                )
            )
            return
        record.supplements.append(source_record)
        self.history.setdefault(record_id, []).append(
            HistoryEntry(at, "来源补交", f"登记来源业务材料 {source_record}")
        )

    def resubmit_old_material(self, record_id: str, source_record: str, at: int) -> None:
        """旧材料重复提交：一律留痕但不产生任何归属效果。"""
        self.stale_submissions.append((at, record_id, source_record))
        self.audit.append(
            HistoryEntry(
                at, "旧资料再送", f"{record_id} 的旧材料 {source_record} 重复提交，驳回翻案"
            )
        )

    # ---- 记录归属决定 ---------------------------------------------------

    def decide_record(
        self,
        reviewer: Reviewer,
        record_id: str,
        issuer: str,
        local_id: str,
        at: int,
        source_record: str = "",
    ) -> Decision:
        record = self.records[record_id]
        target = (issuer, local_id)
        self.identity(issuer, local_id)
        self._check_reviewer(reviewer, record.owner, target)
        if target not in record.candidates:
            raise RecordNotClaimable(
                f"记录 {record_id} 的来源候选不含 {issuer}/{local_id}，不能向其确认归属"
            )
        if not self.is_confirmed(target):
            raise UnknownSourceIdentity(f"源身份 {issuer}/{local_id} 尚未确认，不能承接记录")
        if not source_record and record.supplements:
            # 未显式给出材料号时，引用该记录最新登记的来源材料作为决定依据。
            source_record = record.supplements[-1]
        previous = self.decisions.get(record_id, [])
        supersedes = bool(previous)
        if supersedes:
            # 改动已核清记录：必须有此前未登记过的新证据支撑，且须随可见新决定提出。
            offered = [sr for _, sr in self.held_supplements.get(record_id, [])]
            if source_record:
                offered.append(source_record)
            fresh = [sr for sr in offered if sr not in record.supplements]
            if not fresh:
                raise DecisionLocked(
                    f"记录 {record_id} 已核清，缺少新证据与可见新决定，禁止翻案"
                )
            record.supplements.extend(fresh)
            # 新决定作出后，已消费的待议材料出队，其余继续留待后续决定。
            self.held_supplements[record_id] = [
                (sat, sr)
                for sat, sr in self.held_supplements.get(record_id, [])
                if sr not in fresh
            ]
        decision = Decision(at, reviewer.id, record_id, target, source_record, supersedes)
        self.decisions.setdefault(record_id, []).append(decision)
        self.history.setdefault(record_id, []).append(
            HistoryEntry(
                at,
                "记录核对" if not supersedes else "归属改判",
                f"由 {reviewer.id} 核清至 {issuer}/{local_id}"
                + (f"，依据材料 {source_record}" if source_record else ""),
            )
        )
        self.audit.append(
            HistoryEntry(
                at,
                "记录核对",
                f"{record_id} 归属 {issuer}/{local_id}（核验员 {reviewer.id}）",
            )
        )
        return decision

    def isolate_record(self, record_id: str, at: int | None, reason: str) -> bool:
        if record_id in self.isolated:
            return False
        self.isolated[record_id] = at if at is not None else -1
        self.history.setdefault(record_id, []).append(
            HistoryEntry(at, "隔离", reason)
        )
        self.audit.append(HistoryEntry(at, "隔离", f"{record_id}：{reason}"))
        return True

    # ---- 重新认证 -------------------------------------------------------

    def reauthenticate(self, identity_key: tuple[str, str], credential: str, at: int) -> None:
        holder_records = [
            r for r in self.records.values()
            if r.credential == credential and self.holder_of(r.id) == identity_key
        ]
        if not holder_records:
            raise ReauthenticationFailed(
                f"凭据 {credential} 无法接续到 {identity_key[0]}/{identity_key[1]}"
            )
        self.reauthenticated[credential] = (identity_key, at)
        self.audit.append(
            HistoryEntry(
                at, "重新认证", f"凭据 {credential} 按核清关系接续至 {identity_key[0]}/{identity_key[1]}"
            )
        )

    # ---- 状态推导 -------------------------------------------------------

    def current_decision(self, record_id: str) -> Decision | None:
        decisions = self.decisions.get(record_id, [])
        return decisions[-1] if decisions else None

    def record_state(self, record_id: str) -> RecordState:
        if record_id in self.isolated:
            return RecordState.ISOLATED
        if self.current_decision(record_id) is not None:
            return RecordState.ATTRIBUTED
        record = self.records[record_id]
        if len(record.candidates) == 1 and self.is_confirmed(record.candidates[0]):
            return RecordState.ATTRIBUTED
        return RecordState.PENDING

    def holder_of(self, record_id: str) -> tuple[str, str] | None:
        state = self.record_state(record_id)
        if state is RecordState.ISOLATED or state is RecordState.PENDING:
            return None
        decision = self.current_decision(record_id)
        if decision is not None:
            return decision.target
        (only,) = self.records[record_id].candidates
        return only
