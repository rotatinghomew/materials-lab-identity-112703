"""身份归属纠正台的领域错误。"""


class CorrectionError(Exception):
    """纠正流程内所有可预期违规的基类。"""


class CaseNotOpen(CorrectionError):
    """复核申请尚未立案，不能受理核验动作。"""


class UnknownReviewer(CorrectionError):
    """提交动作的核验员不在名册中。"""


class UnauthorizedReviewer(CorrectionError):
    """核验员对涉及的来源业务没有授权。"""


class SelfReviewConflict(CorrectionError):
    """核验员本人就是被核身份，须另请核验员。"""


class UnknownSourceIdentity(CorrectionError):
    """登记单位与源编号合起来找不到对应的源身份。"""


class RecordNotClaimable(CorrectionError):
    """记录的来源候选中不含该身份，不能向其确认归属。"""


class DecisionLocked(CorrectionError):
    """记录已核清，没有新证据与可见的新决定不得改动。"""


class ReauthenticationFailed(CorrectionError):
    """凭据无法与核清后的持有人接续。"""
