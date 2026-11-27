# 共享平台的身份误归并纠正

夹具中的旧目录主体、来源证据和复核工作的本地资料以 JSON 保存，全部身份、系统与输入均为合成。资料服务只回放各参与方提供的事实。

需要 Python 3.11。运行 `python3 -m unittest -v` 核对项目资料和本地接入。运行 `python3 replay.py` 启动回放服务，默认监听 `127.0.0.1:8765`。访问 `/streams` 查看资料名称，`/streams/名称` 读取相应资料，`/timeline?through=60` 读取截至演练时刻 60 收到的事件。也可直接读取 `data/` 中的原始文件。

## 资料约定

directory 由登记源提供，旧目录 subject 可能带多个来源记录。issuer 与 local_id 合起来才识别源登记。evidence.owner 是有权提供原始记录的平台，各平台 id 只在自身内唯一。candidates 只记录来源声称的可能归属，空列表表示来源尚无可采信的关联。reviewers.source 表示可核的来源业务，person 为核验员实际身份。sessions 给出纠正前的会话引用和业务面。
timeline 列各端提交的核验动作及其到达顺序，facts.source_record 是提交来源的业务材料号。演练资料记录旧状态和收到的证据，未标注归属处理结论。
