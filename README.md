# 共享平台的身份误归并纠正

夹具中的旧目录主体、来源证据和复核工作的本地资料以 JSON 保存，全部身份、系统与输入均为合成。资料服务只回放各参与方提供的事实。

需要 Python 3.11。运行 `python3 -m unittest discover -s tests -v` 核对项目资料和本地接入。运行 `python3 replay.py` 启动回放服务，默认监听 `127.0.0.1:8765`。访问 `/streams` 查看资料名称，`/streams/名称` 读取相应资料，`/timeline?through=60` 读取截至演练时刻 60 收到的事件。也可直接读取 `data/` 中的原始文件。

## 资料约定

directory 由登记源提供，旧目录 subject 可能带多个来源记录。issuer 与 local_id 合起来才识别源登记。evidence.owner 是有权提供原始记录的平台，各平台 id 只在自身内唯一。candidates 只记录来源声称的可能归属，空列表表示来源尚无可采信的关联。reviewers.source 表示可核的来源业务，person 为核验员实际身份。sessions 给出纠正前的会话引用和业务面。
timeline 列各端提交的核验动作及其到达顺序，facts.source_record 是提交来源的业务材料号。演练资料记录旧状态和收到的证据，未标注归属处理结论。

## 身份归属纠正台

`correction` 包消费上述只读资料，实现误归并主体的归属纠正：

- `correction.models`：源身份以 `(issuer, local_id)` 双键识别；源编号只在登记单位内不重复。
- `correction.case`：纠正案件聚合。核验员须对涉及来源有授权，本人即被核身份时拒绝并要求另请核验员；记录归属只能在其来源候选内确认，共同引用的老记录不默认克隆；无候选记录仅隔离并指向 `owner` 数据责任人；已核清记录的改动必须凭未登记过的新证据走可见新决定，旧材料重复提交只留痕不翻案；决定链与逐笔历史追加保存。
- `correction.access`：纠正生效后的访问投影。登录按来源重新绑定源身份，不属于该身份的业务面收回，待决/隔离记录推出的访问挂起，预约凭据重新认证后才接续，培训等独立证据核清即可继续使用。
- `correction.console`：门面与时间线回放。不合规动作拒绝并记入 `rejected`，不中断回放；`operator_view()` 同时给出当前可用面、凭据接续状态、隔离区与完整审计。

`tests/test_correction.py` 以演练夹具逐规则核对（授权与回避、候选约束、隔离、重新认证、翻案锁定、幂等回放等）。运行 `python3 -m unittest discover -s tests -v` 一并执行资料测试与纠正台测试。
