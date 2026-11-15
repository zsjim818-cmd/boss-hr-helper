# Stage 4 实施前执行兼容设计

旧算法审计见 [legacy_audit.md](legacy_audit.md)。本文件在新引擎实现前记录已确认的字段缺口及兼容方案，不修改已发布 JobScoringPolicy v1。

## 协议缺口与兼容

1. keyword_groups.requirement_ids 是组级关联，不是逐词到原子要求的证明。Interpreter保留declared_requirement_ids作为软信号范围；只有canonical/alias能在原子label中唯一定位（否则在source_text中唯一定位）才建立精确matched_requirement关联。模糊关联单独记录，不能把一次命中当整组能力满足。维度可以使用其已声明范围内的组级关键词软分，但不据此宣称具体要求已证实。
2. Policy没有证据来源权重、语境处理、饱和参数。采用独立版本化PrescreenExecutionProfile，沿用旧来源权重 work/project=1.2、thesis=1、work_tags=.35、skills=.5、advantage=.4。它不是岗位配置，不能声称这些值由Qwen或岗位Policy给出。按运行结果记录profile版本与哈希，不读取全局config.json。
3. ResumeData无工作授权、当前城市。保持旧Schema和历史内容哈希；新执行适配器可从同份raw snapshot的明确白名单字段构造独立ObjectiveFacts，缺失则UNKNOWN。城市默认仅解释为期望工作城市，记录city_basis，不能证明现居地。任何额外值必须定位到原输入，不能用国籍/姓名/优势文本推断。
4. Policy能力叶通常没有hard_filter。Tier0执行硬谓词投影：纯能力根/ALL无硬后代分支不参与客观过滤；含硬后代的ANY中，未可执行能力分支必须保留UNKNOWN，避免删枝误拒。能力缺少关键词不能成为硬FAIL。INFERENCE/needs_confirmation也不能成为硬FAIL。
5. 旧粗排是45分Tier1+55分BGE。新preliminary_score是独立0–100规则初筛，不声称等价于旧coarse_score。仅coarse_pass用于本阶段规则门槛；deep_review/deep_recommend原样传递，不拿初筛分数作深评结论。
6. Stage3旧冲突诊断按field全局比较上下界，会误阻止合法ANY的互斥分支；允许最小修正为仅对能同时成立的必需ALL上下文比较，不改变Schema或历史JSON。原全局必需冲突仍必须报错。
7. 旧EvaluationOutput/正式evaluations固定V2契约。本阶段新增独立严格初筛契约，不把新0–100初筛写入旧正式评分，不迁移缓存、不更改历史评分。

## 运行边界

Interpreter从job_candidate_id取得org/job/candidate/policy_id/version，再读取该精确不可变版本，绝不改读jobs.current。优先使用sealed input；未封存部分简历必须显式resume_id，只作为预览，保留原绑定版本。服务通过Repository读取，Interpreter不持久化结果或调用模型。

规则缓存以org/job/policy_id/policy_version隔离，同时校验内容哈希与执行profile哈希。缓存只保存不可变规则，不保存候选评分；授权每次读取时重新检查。

## 新规则公式（待固定测试集验证，不宣称已校准）

每个canonical全简历仅选最强有效来源，权重来自Policy，变体和重复出现不累加。NEGATED/NO_PRACTICE贡献0；LEARNING/MENTION最多使用旧work_tags=.35；普通技能标签最多skills=.5，不升格为工作实战。明确实战描述可保留work/project=1.2。语境不确定贡献0并记录核验原因。

能力图决定本维选中的要求范围：ALL保留全部成员；ANY按同一有界证据公式选择最佳分支，所有分支结果和选择理由保留，分支未知不能说已满足。合并范围内关键词后按canonical去重。组级软信号和精确要求命中分开输出。

对维度d：B为选中范围全部canonical的Policy权重和；M为有有效正向/辅助信号的canonical权重和；q为已命中词的权重加权证据质量（实际来源权重/1.2）。

`raw_score = sum(w_c * source_weight_c)`

`normalized_score = 100 * (1-exp(-M/B))/(1-exp(-1)) * q`

B=0时不凭空造词或分数；若该维仅有客观谓词，使用明确PASS要求的覆盖比例，否则0并列证据缺口。M=0则0。保留旧指数饱和机制，以Policy完整关键词预算作scale并按满预算归一，避免把0–100的新关键词weight直接喂进旧35/18/8造成饱和。权重同比缩放不改变归一分。只学会全部标签也不会因饱和抬成实战满分。

`preliminary_score = sum(dimension.weight * dimension.normalized_score)`

无额外PREFERRED/OPTIONAL倍数：它们按Policy的词权重和维度权重提供软分，优先级仅决定硬门控与逻辑，不变成拒绝条件。总分始终0–100，不能覆盖Tier0 FAIL。

最终状态依次区分采集/执行失败、硬条件失败、数据不完整、硬条件UNKNOWN、初筛低于coarse_pass、待后续深评。所有低分/失败只是初筛建议，保留HR最终判断；没有AUTO_GREET或正式深度分。
