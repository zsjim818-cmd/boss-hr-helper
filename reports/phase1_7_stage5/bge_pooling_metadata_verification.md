# BGE Pooling 元数据核验报告（Stage 5 / 接管 Phase 3）

- 项目根目录：`D:\Boss-project\boss-qhr-helper-dev`
- 核验对象：本地 pinned BGE 快照 vs `config/semantic_profile.json` 的 Embedding Profile 声明
- 核验方式：静态配置读取 + 真实模型运行时交叉验证（不只依赖文件名或网上默认值）
- 结论：**模型实际使用 CLS pooling + L2 Normalize，与 Profile 声明完全一致**。交接资料中「Profile 声称 MEAN、模型实际 CLS」的冲突在当前代码与配置中**不存在**；真实缺口是**缺少运行时一致性守卫**，已在本阶段补齐。

---

## 1. 本地模型快照标识

| 项 | 值 |
| --- | --- |
| 模型目录 | `D:\model\bge-large-zh-v1.5` |
| HF 快照 | `hub\models--BAAI--bge-large-zh-v1.5\snapshots\79e7739b6ab944e86d6171e44d24c997fc1e0116` |
| 快照 revision | `79e7739b6ab944e86d6171e44d24c997fc1e0116` |
| 加载入口 | `engine/hybrid_engine.py` → `SentenceTransformer(<EMBEDDING_MODEL_PATH>)` |

`reports/local_bge_fast.log` 亦记录实际加载路径为该 `79e7739b…` 快照，与 Profile 的 `model_revision` 一致。

## 2. 静态配置证据

### 2.1 `modules.json`（模型流水线组成）

```
0 → sentence_transformers.models.Transformer
1 → sentence_transformers.models.Pooling   (path: 1_Pooling)
2 → sentence_transformers.models.Normalize (path: 2_Normalize)
```

- 存在 `Pooling` 模块，其后紧跟 `Normalize` 模块 → 模型内部已执行归一化。

### 2.2 `1_Pooling/config.json`（pooling 声明）

```json
{
  "word_embedding_dimension": 1024,
  "pooling_mode_cls_token": true,
  "pooling_mode_mean_tokens": false,
  "pooling_mode_max_tokens": false,
  "pooling_mode_mean_sqrt_len_tokens": false
}
```

- 唯一启用项为 `pooling_mode_cls_token` → **CLS**。

### 2.3 `sentence_bert_config.json`

```json
{ "max_seq_length": 512, "do_lower_case": true }
```

## 3. 真实模型运行时交叉验证

新建 `tests/test_semantic_verification.py`，其中 `RealModelConsistencyTests` 直接加载本地 pinned 快照并断言：

| 断言 | 结果 |
| --- | --- |
| `detect_pooling_strategy(model) == profile.pooling_strategy` | ✅ 均为 `CLS` |
| `detect_embedding_dimension(model) == profile.embedding_dimension` | ✅ 均为 `1024` |
| `assert_embedding_model_matches(model, profile)["ok"]` | ✅ `True` |
| 真实编码 `["Python 后端开发工程师"]`：shape `(1,1024)`、全部有限 | ✅ |
| 真实向量 L2 范数 | ✅ `≈ 1.0` |

`detect_pooling_strategy` 同时兼容两种 sentence-transformers 属性风格：
- 新版字符串属性 `pooling_mode='cls'`；
- 旧版布尔标志 `pooling_mode_cls_token/mean_tokens/lasttoken`。

运行命令与结果：

```
.\.venv\Scripts\python.exe -m unittest tests.test_semantic_verification -v
→ Ran 12 tests … OK
```

（含 3 项真实 BGE 测试；模型加载 + 编码总耗时约 12–31s。）

## 4. Profile 声明 vs 实际运行行为对照

| 契约项 | Profile 声明 (`config/semantic_profile.json`) | 实际运行 | 一致 |
| --- | --- | --- | --- |
| Pooling | `CLS` | CLS（运行时模块） | ✅ |
| 维度 | `1024` | 1024 | ✅ |
| max_length | `512` | 512（`max_seq_length`） | ✅ |
| L2 Normalize | `true` | 模型内置 `Normalize` 模块，输出范数 ≈ 1.0 | ✅ |
| Tokenizer | `tokenizer.json`（PINNED revision） | 同一快照 | ✅ |
| input_prefix | 无（`""`） | 项目 `encoded_text` 未加前缀 | ✅ |
| dtype / byte_order | `float32` / `little` | 运行时 float32，落库 `<f4` | ✅ |

## 5. 发现的真实缺口与修复

### 缺口
`pooling_strategy` 参与不可变 `EmbeddingProfile` 的 canonical hash（`embedding_profile_hash`），但流水线中**没有任何环节读取实际加载模型的 pooling 模块**。即：hash 只是「声明」，无法证明「声明 == 实际」。

### 修复（新增，不改动既有逻辑）
新增 `engine/semantic_verification.py`：

- `detect_pooling_strategy(model)`：读取模型真实 Pooling 模块，返回 `CLS/MEAN/LAST_TOKEN`；多种模式并存或无可用模块时**报错而非猜测**。
- `detect_embedding_dimension(model)` / `detect_max_seq_length(model)`：读取真实维度与最大长度。
- `verify_embedding_model(model, profile)`：比对 `pooling / dimension / max_length`，返回结构化 `{ok, checks}`（max_length 仅在模型确实上报时比较）。
- `assert_embedding_model_matches(model, profile)`：不一致时 `raise ValueError`，供 `embedding-check` CLI、Stage 5 服务层与回归测试复用。

### Profile Hash 未受影响
Profile 未被修改（原本即为 `CLS`），因此 **profile hash 保持不变**，既有缓存无需失效。新增的是「运行时守卫」，属于纯增量能力。

## 6. 验证测试清单（对应任务第 5 点）

| 要求 | 覆盖测试 |
| --- | --- |
| Profile 声明的 pooling 与运行时模型一致 | `test_runtime_pooling_matches_declared_profile` |
| Profile Hash 随关键编码参数变化 | `test_effective_encoding_parameters_change_the_hash_but_labels_do_not`（pooling/dimension/max_length/l2 变化 → hash 变；仅改 label → hash 不变） |
| 模型维度正确 | `test_runtime_dimension_and_length_match_declared_profile`、`test_dimension_falls_back_to_module_attribute` |
| 实际输出为有限数值 | `test_real_vector_is_finite_normalized_and_correctly_sized` |
| 向量范数符合配置要求 | 同上（范数 ≈ 1.0）；`tests/test_semantic_vectors.py` 校验落库向量归一化 |
| 旧 Profile 缓存不会被错误复用 | Profile 未变，hash 不变；缓存键含 `embedding_profile_hash`/`encoded_text_hash`，参数变化即换键（见 `test_semantic_profiles.py` / `tests/test_semantic_vectors.py`） |
| 数据库模型元数据可重新验证 | `embedding_profiles` 仅追加不可变；`ensure_profile` 在写入时比对 `profile_hash`（`engine/embedding_service.py`） |

## 7. 结论

1. 实际运行参数 = **CLS + L2 Normalize + dim 1024 + max_len 512 + 无前缀 + float32/little**。
2. `config/semantic_profile.json` 声明与之逐项一致，**Profile 无需修正，hash 不变，缓存无需失效**。
3. 缺失的「运行时一致性守卫」已补齐并有真实模型回归测试覆盖。
4. 未修改 `engine/embedding_vectors.py`、`engine/policy_semantic_ranker.py`、Stage 1–4 逻辑、001–007 迁移与任何已发布 Policy。
