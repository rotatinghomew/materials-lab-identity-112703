"""身份归属纠正台的领域规则测试。"""
import json
import unittest
from pathlib import Path

from correction import (
    ACTIVE,
    PENDING,
    REVOKED,
    SUSPENDED,
    DecisionLocked,
    RecordNotClaimable,
    RecordState,
    SelfReviewConflict,
    UnauthorizedReviewer,
    UnknownReviewer,
    UnknownSourceIdentity,
    load_console,
)
from correction.errors import ReauthenticationFailed

ROOT = Path(__file__).resolve().parent.parent
TRAINING = ("training", "tr-009")
MEMBER = ("membership", "mem-414")


def load_data():
    return {p.stem: json.loads(p.read_bytes()) for p in (ROOT / "data").glob("*.json")}


def console_replayed(through=None):
    data = load_data()
    console = load_console(data)
    console.replay(data["timeline"], through=through)
    return console, data


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.console, _ = console_replayed()

    def test_source_identity_needs_issuer_and_local_id(self):
        # 源编号只在登记单位内唯一：同一 local_id 换单位即视为不同身份。
        case = self.console.cases["old-81"]
        self.assertEqual(set(case.identities), {TRAINING, MEMBER})
        self.assertEqual(case.identity(*TRAINING).label, "顾一")
        self.assertEqual(case.identity(*MEMBER).label, "顾乙")
        with self.assertRaises(UnknownSourceIdentity):
            case.identity("notebook", "tr-009")

    def test_unrelated_subject_not_touching_case(self):
        # clean-28 未被误归并，不立案纠正，其证据与登录保持原样。
        self.assertNotIn("clean-28", self.console.cases)
        case = self.console.cases["old-81"]
        self.assertNotIn("train-17", case.records)
        view = self.console.operator_view()
        self.assertEqual({s.id for s in view.sessions}, {"sid-8", "sid-9"})


class ReviewerAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.console, _ = console_replayed()
        self.case = self.console.cases["old-81"]

    def test_only_authorized_reviewer_confirms_identity(self):
        fresh, _ = console_replayed(through=15)
        with self.assertRaises(UnauthorizedReviewer):
            # rv-2 只有 membership 授权，不能核 training 身份。
            fresh.confirm_identity("rv-2", *TRAINING, at=18)
        fresh.confirm_identity("rv-1", *TRAINING, at=18)
        self.assertTrue(fresh.cases["old-81"].is_confirmed(TRAINING))

    def test_self_review_must_be_reassigned(self):
        # rv-4 的实际身份就是 mem-414：at=30 的自审被拒并留痕。
        rejected = [(r.at, r.reason) for r in self.console.rejected if r.at == 30]
        self.assertEqual(len(rejected), 1)
        self.assertIn("同人", rejected[0][1])
        with self.assertRaises(SelfReviewConflict):
            self.case.decide_record(
                self.console.reviewers["rv-4"],
                "note-11",
                *MEMBER,
                at=31,
                source_record="nb-25",
            )

    def test_record_decision_requires_ownership_authorization(self):
        # note-11 属 notebook 业务，membership 核验员即便对该身份有授权也不能核这条记录。
        with self.assertRaises(UnauthorizedReviewer):
            self.case.decide_record(
                self.console.reviewers["rv-2"],
                "note-11",
                *MEMBER,
                at=33,
                source_record="nb-25",
            )

    def test_unknown_reviewer_rejected(self):
        fresh, _ = console_replayed(through=15)
        with self.assertRaises(UnknownReviewer):
            fresh.confirm_identity("rv-999", *TRAINING, at=18)


class RecordAttributionTests(unittest.TestCase):
    def setUp(self):
        self.console, _ = console_replayed()
        self.case = self.console.cases["old-81"]

    def test_single_evidence_records_split_to_each_person(self):
        # 培训记录回到只在培训站登记的人；机时预约回到正式申请人。
        self.assertEqual(self.case.holder_of("train-2"), TRAINING)
        self.assertEqual(self.case.holder_of("book-81"), MEMBER)

    def test_shared_record_is_not_cloned_to_both(self):
        # note-11 双候选：不默认克隆，最终只核给一个实际持有人。
        holder = self.case.holder_of("note-11")
        self.assertEqual(holder, MEMBER)
        decision = self.case.current_decision("note-11")
        self.assertEqual(decision.reviewer_id, "rv-3")
        self.assertEqual(decision.source_record, "nb-25")
        self.assertFalse(decision.supersedes)

    def test_claim_must_be_among_candidates(self):
        from correction.models import Reviewer

        fresh_console, _ = console_replayed(through=24)  # 身份已确认、note-11 尚未核清
        fresh_case = fresh_console.cases["old-81"]
        self.assertEqual(fresh_case.record_state("note-11"), RecordState.PENDING)
        # book-81 的来源候选只有 mem-414：不能凭旧归并把它核给 tr-009。
        reservation_reviewer = Reviewer("rv-x", "reservation", "rv-person-x")
        with self.assertRaises(RecordNotClaimable):
            fresh_case.decide_record(
                reservation_reviewer,
                "book-81",
                *TRAINING,
                at=35,
            )

    def test_no_candidate_record_is_isolated_only(self):
        # note-10 无候选：隔离，并指向 notebook 数据责任人，不派给任何人。
        self.assertEqual(self.case.record_state("note-10"), RecordState.ISOLATED)
        self.assertIsNone(self.case.holder_of("note-10"))
        isolation_note = self.case.history["note-10"][0].note
        self.assertIn("notebook", isolation_note)
        view = self.console.operator_view()
        self.assertEqual([r.record_id for r in view.isolated], ["note-10"])

    def test_pending_record_blocks_only_its_derived_access(self):
        # 在 note-11 核清之前（through=30），notebook 面挂起；其他已核清面照常。
        console, _ = console_replayed(through=30)
        case = console.cases["old-81"]
        self.assertEqual(case.record_state("note-11"), RecordState.PENDING)
        sid9 = next(s for s in console.operator_view().sessions if s.id == "sid-9")
        by_auth = {e.authority: e.status for e in sid9.effects}
        self.assertEqual(by_auth["notebook"], PENDING)
        sid8 = next(s for s in console.operator_view().sessions if s.id == "sid-8")
        self.assertIn("training", sid8.active_authorities)


class AccessRevocationTests(unittest.TestCase):
    def setUp(self):
        self.console, _ = console_replayed()
        self.view = self.console.operator_view()

    def _session(self, sid):
        return next(s for s in self.view.sessions if s.id == sid)

    def test_merged_login_cannot_act_for_the_other_person(self):
        sid8 = self._session("sid-8")
        self.assertEqual(sid8.bound_identity, TRAINING)
        self.assertEqual(
            next(e for e in sid8.effects if e.authority == "membership").status,
            REVOKED,
        )
        sid9 = self._session("sid-9")
        self.assertEqual(sid9.bound_identity, MEMBER)
        self.assertEqual(set(sid9.active_authorities), {"membership", "notebook"})

    def test_reservation_credential_gates_until_reauthentication(self):
        console, _ = console_replayed(through=30)
        view = console.operator_view()
        sid9 = next(s for s in view.sessions if s.id == "sid-9")
        membership_effect = next(e for e in sid9.effects if e.authority == "membership")
        self.assertEqual(membership_effect.status, SUSPENDED)
        self.assertIn("cred-21", membership_effect.note)
        # at=40 重新认证后续订，全量视图恢复 active。
        self.assertEqual(
            next(e for e in self._session("sid-9").effects if e.authority == "membership").status,
            ACTIVE,
        )

    def test_training_credential_needs_no_reauthentication(self):
        # 培训记录的自带凭据不属预约门禁：核清即可继续使用。
        sid8 = self._session("sid-8")
        self.assertEqual(
            next(e for e in sid8.effects if e.authority == "training").status, ACTIVE
        )

    def test_reauthentication_rejects_wrong_holder(self):
        case = self.console.cases["old-81"]
        with self.assertRaises(ReauthenticationFailed):
            case.reauthenticate(TRAINING, "cred-21", at=41)

    def test_unconfirmed_identity_login_loses_all_authority(self):
        data = load_data()
        console = load_console(data)
        # 只回放目录复核之前的事件：两个身份均未确认。
        console.replay(data["timeline"], through=15)
        for session in console.operator_view().sessions:
            self.assertTrue(all(e.status == REVOKED for e in session.effects))


class DecisionIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.console, _ = console_replayed()
        self.case = self.console.cases["old-81"]

    def test_stale_resubmission_does_not_revert_attribution(self):
        # at=70 旧资料再送：归属不变、决定链不增长，只登记一条驳回留痕。
        decisions_before = len(self.case.decisions["note-11"])
        self.assertEqual(self.case.holder_of("note-11"), MEMBER)
        self.assertIn((70, "note-11", "nb-4"), self.case.stale_submissions)
        self.assertEqual(len(self.case.decisions["note-11"]), decisions_before)

    def test_changing_cleared_record_needs_new_evidence_and_new_decision(self):
        reviewer = self.console.reviewers["rv-3"]
        # 无新证据直接改判 → 锁定拒绝。
        with self.assertRaises(DecisionLocked):
            self.case.decide_record(reviewer, "note-11", *MEMBER, at=80)
        # 新证据先行登记，再由授权核验员作出可见新决定方可改判。
        self.case.submit_supplement("note-11", "nb-99", at=78)
        new_decision = self.case.decide_record(reviewer, "note-11", *MEMBER, at=82)
        self.assertTrue(new_decision.supersedes)
        self.assertIn("nb-99", self.case.records["note-11"].supplements)
        self.assertEqual(len(self.case.decisions["note-11"]), 2)
        # 已消费的待议材料出队，决定链上两个决定均保留可追溯。
        self.assertEqual(self.case.held_supplements.get("note-11", []), [])
        self.assertEqual(
            [d.at for d in self.case.decisions["note-11"]], [52, 82]
        )


class HistoryAndViewTests(unittest.TestCase):
    def test_completed_actions_keep_original_registration_and_correction_notes(self):
        console, _ = console_replayed()
        case = console.cases["old-81"]
        kinds = [entry.kind for entry in case.audit]
        # 从立案、源身份确认、重新认证、记录核清到旧材料驳回，全程留痕。
        self.assertIn("复核申请", kinds)
        self.assertIn("目录复核", kinds)
        self.assertIn("重新认证", kinds)
        self.assertIn("记录核对", kinds)
        self.assertIn("旧资料再送", kinds)
        history_kinds = [entry.kind for entry in case.history["note-11"]]
        self.assertEqual(history_kinds, ["来源补交", "记录核对"])

    def test_operator_view_distinguishes_usable_and_undecided(self):
        console, _ = console_replayed(through=60)
        view = console.operator_view(at=60)
        # 历史归属尚无定论的就是 note-10 这一笔，已隔离并指向 notebook 责任人。
        isolated = {(r.record_id, r.owner) for r in view.isolated}
        self.assertEqual(isolated, {("note-10", "notebook")})
        self.assertEqual(view.undecided_records(), ())
        for session in view.sessions:
            self.assertTrue(session.usable)
        holders = {r.record_id: r.holder for r in view.records if r.state is RecordState.ATTRIBUTED}
        self.assertEqual(holders["train-2"], TRAINING)
        self.assertEqual(holders["book-81"], MEMBER)
        self.assertEqual(holders["note-11"], MEMBER)

    def test_replay_is_idempotent(self):
        data = load_data()
        console = load_console(data)
        console.replay(data["timeline"])
        audit_count = len(console.cases["old-81"].audit)
        rejected_count = len(console.rejected)
        console.replay(data["timeline"])
        self.assertEqual(len(console.cases["old-81"].audit), audit_count)
        self.assertEqual(len(console.rejected), rejected_count)


if __name__ == "__main__":
    unittest.main()
