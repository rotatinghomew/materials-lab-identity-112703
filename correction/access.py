"""纠正生效后的访问面投影。

纠正一旦生效，凭旧归并结果产生的访问和归属能力必须失效：登录按其来源
重新绑定到核清后的源身份，不属于该身份的业务面一律收回；未核清记录推出
的访问只隔离挂起；预约凭据须重新认证后才接续。
"""
from __future__ import annotations

from dataclasses import dataclass

from .case import CorrectionCase, RecordState
from .models import LoginSession

ACTIVE = "active"
SUSPENDED = "suspended"   # 已核清但凭据尚未重新认证
PENDING = "pending"       # 归属尚无定论，隔离等待复核
REVOKED = "revoked"       # 不属于该登录核清后的身份


@dataclass(frozen=True)
class AuthorityEffect:
    authority: str
    status: str
    note: str


@dataclass(frozen=True)
class SessionProjection:
    id: str
    subject: str
    bound_identity: tuple[str, str] | None
    effects: tuple[AuthorityEffect, ...]

    @property
    def usable(self) -> bool:
        return any(e.status in (ACTIVE, SUSPENDED, PENDING) for e in self.effects)

    @property
    def active_authorities(self) -> tuple[str, ...]:
        return tuple(e.authority for e in self.effects if e.status == ACTIVE)


def _records_for_authority(case: CorrectionCase, authority: str) -> list:
    # 会话上的业务面既可能对应证据持有平台（owner），也可能对应登记签发单位
    # （issuer，例如 membership 面承接 membership 签发的预约记录）。
    return [
        r
        for r in case.records.values()
        if r.owner == authority or r.issuer == authority
    ]


def _evaluate_authority(
    case: CorrectionCase, identity_key: tuple[str, str], authority: str
) -> AuthorityEffect:
    records = _records_for_authority(case, authority)
    if not records:
        return AuthorityEffect(authority, REVOKED, f"{authority} 无任何来源证据承接")
    holder_records = [r for r in records if case.holder_of(r.id) == identity_key]
    if not holder_records:
        undecided = [r.id for r in records if case.record_state(r.id) is RecordState.PENDING]
        isolated = [r.id for r in records if case.record_state(r.id) is RecordState.ISOLATED]
        note_parts = []
        if undecided:
            note_parts.append(f"记录 {','.join(undecided)} 归属待决")
        if isolated:
            note_parts.append(f"记录 {','.join(isolated)} 已隔离")
        if note_parts:
            # 该业务面尚有不能确认的归属：只隔离由其推出的访问，不做彻底收回，
            # 留给数据责任人复核后再定。
            return AuthorityEffect(authority, PENDING, "；".join(note_parts))
        return AuthorityEffect(authority, REVOKED, "该业务面归属于另一源身份")
    for record in holder_records:
        state = case.record_state(record.id)
        if state is RecordState.ISOLATED:
            return AuthorityEffect(
                authority, PENDING, f"记录 {record.id} 已隔离，待数据责任人复核"
            )
        if state is RecordState.PENDING:
            return AuthorityEffect(
                authority, PENDING, f"记录 {record.id} 归属尚无定论，访问隔离挂起"
            )
    # 预约业务面及其凭据须重新认证后才接续；培训、样品笔记等独立来源证据
    # 一经核清即可继续使用已核清部分，不设凭据门禁。
    needs_reauth = [
        r.credential
        for r in holder_records
        if r.owner == "reservation"
        and r.credential
        and r.credential not in case.reauthenticated
    ]
    if needs_reauth:
        return AuthorityEffect(
            authority,
            SUSPENDED,
            f"凭据 {','.join(sorted(set(needs_reauth)))} 待重新认证后接续",
        )
    return AuthorityEffect(authority, ACTIVE, "按核清关系接续")


def project_session(case: CorrectionCase, session: LoginSession) -> SessionProjection:
    identity_key = (session.origin, None)
    bound = next(
        (key for key in case.identities if key[0] == session.origin), None
    )
    if bound is None or not case.is_confirmed(bound):
        effects = tuple(
            AuthorityEffect(a, REVOKED, "登录所依来源身份尚未核清，旧归并访问全部失效")
            for a in sorted(session.authorities)
        )
        return SessionProjection(session.id, session.subject, None, effects)
    effects = tuple(
        _evaluate_authority(case, bound, authority)
        for authority in sorted(session.authorities)
    )
    return SessionProjection(session.id, session.subject, bound, effects)


def identity_scope(
    case: CorrectionCase, identity_key: tuple[str, str]
) -> dict[str, list[str]]:
    """某源身份当前已核清承接的业务面及其记录。"""
    scope: dict[str, list[str]] = {}
    for record in case.records.values():
        if case.holder_of(record.id) == identity_key:
            scope.setdefault(record.owner, []).append(record.id)
    return scope
