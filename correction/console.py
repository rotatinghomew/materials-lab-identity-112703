"""身份归属纠正台门面。

从只读资料装载旧目录、来源证据、核验员名册与纠正前登录，并按时间线
回放各端提交的核验动作；对不合规动作拒绝并留痕，不中断回放。
"""
from __future__ import annotations

from dataclasses import dataclass

from .access import (
    ACTIVE,
    PENDING,
    SUSPENDED,
    AuthorityEffect,
    SessionProjection,
    identity_scope,
    project_session,
)
from .case import CorrectionCase, HistoryEntry, RecordState
from .errors import CorrectionError, UnknownReviewer
from .models import EvidenceRecord, LoginSession, Reviewer, SourceIdentity

REVIEW_APPLICATION = "复核申请"
DIRECTORY_REVIEW = "目录复核"
RECORD_REVIEW = "记录核对"
REAUTHENTICATION = "重新认证"
SOURCE_SUPPLEMENT = "来源补交"
STALE_RESUBMISSION = "旧资料再送"


@dataclass(frozen=True)
class RejectedAction:
    at: int
    kind: str
    ref: str
    reason: str


@dataclass(frozen=True)
class IdentityRow:
    key: tuple[str, str]
    label: str
    confirmed: bool
    confirmed_at: int | None
    scope: dict[str, list[str]]


@dataclass(frozen=True)
class RecordRow:
    record_id: str
    owner: str
    state: RecordState
    holder: tuple[str, str] | None
    credential: str
    credential_ready: bool


@dataclass(frozen=True)
class CredentialRow:
    credential: str
    holder: tuple[str, str]
    ready: bool
    ready_at: int | None


@dataclass(frozen=True)
class OperatorView:
    at: int
    case_subject: str
    identities: tuple[IdentityRow, ...]
    records: tuple[RecordRow, ...]
    credentials: tuple[CredentialRow, ...]
    isolated: tuple[RecordRow, ...]
    sessions: tuple[SessionProjection, ...]
    rejected: tuple[RejectedAction, ...]
    audit: tuple[HistoryEntry, ...]

    def usable_authorities(self) -> dict[str, tuple[AuthorityEffect, ...]]:
        return {s.id: tuple(e for e in s.effects if e.status == ACTIVE) for s in self.sessions}

    def undecided_records(self) -> tuple[RecordRow, ...]:
        return tuple(r for r in self.records if r.state is RecordState.PENDING)


class CorrectionConsole:
    def __init__(
        self,
        directory,
        evidence,
        reviewers,
        sessions,
        correction_subjects: frozenset[str] | None = None,
    ):
        self.identities: dict[tuple[str, str], SourceIdentity] = {}
        self.subjects: dict[str, dict[tuple[str, str], SourceIdentity]] = {}
        for row in directory:
            identity = SourceIdentity(row["issuer"], row["local_id"], row["label"])
            self.identities[identity.key] = identity
            self.subjects.setdefault(row["subject"], {})[identity.key] = identity
        if correction_subjects is None:
            correction_subjects = frozenset(
                subject for subject, group in self.subjects.items() if len(group) > 1
            )
        self.correction_subjects = frozenset(correction_subjects)
        self.cases: dict[str, CorrectionCase] = {
            subject: CorrectionCase(subject=subject, identities=dict(group))
            for subject, group in self.subjects.items()
            if subject in self.correction_subjects
        }
        for case in self.cases.values():
            for row in evidence:
                candidates = [
                    (self._issuer_of_local(candidate), candidate)
                    for candidate in row["candidates"]
                ]
                touches_case = any(key in case.identities for key in candidates)
                # 空候选记录没有可采信关联：挂到在办纠正案件后隔离，
                # 留给其来源系统（owner）的数据责任人复核。多案件时按主体
                # 顺序确定性兜底，避免重复挂入多个案件。
                if (
                    not touches_case
                    and not candidates
                    and case.subject == sorted(self.cases)[0]
                ):
                    touches_case = True
                if touches_case:
                    case.records[row["id"]] = EvidenceRecord(
                        owner=row["owner"],
                        id=row["id"],
                        issuer=row["issuer"],
                        candidates=candidates,
                        credential=row["credential"],
                    )
        self.reviewers = {
            row["id"]: Reviewer(row["id"], row["source"], row["person"]) for row in reviewers
        }
        self.sessions = [
            LoginSession(
                id=row["id"],
                subject=row["subject"],
                origin=row["origin"],
                authorities=frozenset(row["authorities"]),
            )
            for row in sessions
        ]
        self.rejected: list[RejectedAction] = []
        self._replayed_markers: set[tuple[int, str, str]] = set()
        self.now = 0

    def _issuer_of_local(self, local_id: str) -> str:
        for issuer, lid in self.identities:
            if lid == local_id:
                return issuer
        raise KeyError(local_id)

    # ---- 案件定位 -------------------------------------------------------

    def _reviewer(self, reviewer_id: str):
        try:
            return self.reviewers[reviewer_id]
        except KeyError as exc:
            raise UnknownReviewer(f"核验员 {reviewer_id} 不在名册中") from exc

    def case_for_identity(self, issuer: str, local_id: str) -> CorrectionCase:
        key = (issuer, local_id)
        for case in self.cases.values():
            if key in case.identities:
                return case
        raise CorrectionError(f"{issuer}/{local_id} 不属于任何纠正案件")

    def case_for_record(self, record_id: str) -> CorrectionCase:
        for case in self.cases.values():
            if record_id in case.records:
                return case
        raise CorrectionError(f"记录 {record_id} 不属于任何纠正案件")

    # ---- 对外动作 -------------------------------------------------------

    def open_case(self, subject: str, at: int) -> None:
        self.cases[subject].open(at)
        self.now = max(self.now, at)

    def confirm_identity(
        self, reviewer_id: str, issuer: str, local_id: str, at: int
    ) -> None:
        case = self.case_for_identity(issuer, local_id)
        reviewer = self._reviewer(reviewer_id)
        case.confirm_identity(reviewer, issuer, local_id, at)
        self.now = max(self.now, at)

    def decide_record(
        self,
        reviewer_id: str,
        record_id: str,
        issuer: str,
        local_id: str,
        at: int,
        source_record: str = "",
    ) -> None:
        case = self.case_for_record(record_id)
        reviewer = self._reviewer(reviewer_id)
        case.decide_record(reviewer, record_id, issuer, local_id, at, source_record)
        self.now = max(self.now, at)

    def submit_supplement(self, record_id: str, source_record: str, at: int) -> None:
        case = self.case_for_record(record_id)
        case.submit_supplement(record_id, source_record, at)
        self.now = max(self.now, at)

    def resubmit_old_material(self, record_id: str, source_record: str, at: int) -> None:
        case = self.case_for_record(record_id)
        case.resubmit_old_material(record_id, source_record, at)
        self.now = max(self.now, at)

    def reauthenticate(self, issuer: str, local_id: str, credential: str, at: int) -> None:
        case = self.case_for_identity(issuer, local_id)
        case.reauthenticate((issuer, local_id), credential, at)
        self.now = max(self.now, at)

    def _isolate_unconfirmable(self, case: CorrectionCase) -> None:
        for record_id, record in case.records.items():
            if not record.candidates and case.record_state(record_id) is RecordState.PENDING:
                case.isolate_record(
                    record_id,
                    self.now,
                    f"来源 {record.owner} 无可采信归属关联，隔离待 {record.owner} 数据责任人复核",
                )

    # ---- 时间线回放 -----------------------------------------------------

    def replay(self, timeline, through: int | None = None) -> tuple[RejectedAction, ...]:
        for event in timeline:
            if through is not None and event["at"] > through:
                break
            marker = (event["at"], event["kind"], event["ref"])
            if marker in self._replayed_markers:
                continue
            self._replayed_markers.add(marker)
            self._dispatch(event)
        for case in self.cases.values():
            self._isolate_unconfirmable(case)
        return tuple(self.rejected)

    def _dispatch(self, event) -> None:
        at, kind, ref, facts = event["at"], event["kind"], event["ref"], event["facts"]
        try:
            if kind == REVIEW_APPLICATION:
                if ref not in self.cases:
                    raise CorrectionError(f"主体 {ref} 无纠正案件")
                self.open_case(ref, at)
            elif kind == DIRECTORY_REVIEW:
                self.confirm_identity(ref, facts["issuer"], facts["local_id"], at)
            elif kind == RECORD_REVIEW:
                self.decide_record(
                    ref,
                    facts["record"],
                    facts["issuer"],
                    facts["local_id"],
                    at,
                    facts.get("source_record", ""),
                )
            elif kind == REAUTHENTICATION:
                issuer, local_id = self._issuer_key_of_person(ref)
                self.reauthenticate(issuer, local_id, facts["credential"], at)
            elif kind == SOURCE_SUPPLEMENT:
                self.submit_supplement(ref, facts["source_record"], at)
            elif kind == STALE_RESUBMISSION:
                self.resubmit_old_material(ref, facts["source_record"], at)
            else:
                raise CorrectionError(f"未知事件类型 {kind}")
        except (CorrectionError, KeyError) as exc:
            self.rejected.append(RejectedAction(at, kind, ref, str(exc)))

    def _issuer_key_of_person(self, local_id: str) -> tuple[str, str]:
        for key in self.identities:
            if key[1] == local_id:
                return key
        raise CorrectionError(f"{local_id} 不是已登记的源编号")

    # ---- 操作者视图 -----------------------------------------------------

    def operator_view(self, at: int | None = None) -> OperatorView:
        if at is not None:
            self.now = max(self.now, at)
        for case in self.cases.values():
            self._isolate_unconfirmable(case)
        case = next(iter(self.cases.values()))
        identity_rows = tuple(
            IdentityRow(
                key=key,
                label=identity.label,
                confirmed=case.is_confirmed(key),
                confirmed_at=case.confirmations.get(key),
                scope=identity_scope(case, key) if case.is_confirmed(key) else {},
            )
            for key, identity in sorted(case.identities.items())
        )
        record_rows: tuple[RecordRow, ...] = ()
        for record_id, record in sorted(case.records.items()):
            if record.owner == "reservation" and record.credential:
                credential_ready = record.credential in case.reauthenticated
            else:
                credential_ready = True
            record_rows += (
                RecordRow(
                    record_id=record_id,
                    owner=record.owner,
                    state=case.record_state(record_id),
                    holder=case.holder_of(record_id),
                    credential=record.credential,
                    credential_ready=credential_ready,
                ),
            )
        credential_rows = tuple(
            CredentialRow(
                credential=credential,
                holder=holder,
                ready=True,
                ready_at=at_time,
            )
            for credential, (holder, at_time) in sorted(case.reauthenticated.items())
        )
        isolated_rows = tuple(row for row in record_rows if row.state is RecordState.ISOLATED)
        projections = tuple(
            project_session(case, session)
            for session in self.sessions
            if session.subject == case.subject
        )
        return OperatorView(
            at=self.now,
            case_subject=case.subject,
            identities=identity_rows,
            records=record_rows,
            credentials=credential_rows,
            isolated=isolated_rows,
            sessions=projections,
            rejected=tuple(self.rejected),
            audit=tuple(case.audit),
        )


def load_console(streams: dict) -> CorrectionConsole:
    return CorrectionConsole(
        directory=streams["directory"],
        evidence=streams["evidence"],
        reviewers=streams["reviewers"],
        sessions=streams["sessions"],
    )
