# 1. 一级漏斗外层卡片数据契约（JSON 结构定义）

JSON

```
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "FirstFunnelCardData",
  "description": "Boss直聘推荐牛人列表外层卡片抓取数据契约（仅限一级漏斗硬筛）",
  "type": "object",
  "required": [
    "action_status",
    "name",
    "degree",
    "work_years",
    "expected_salary",
    "_dom_anchor"
  ],
  "properties": {
    "action_status": {
      "type": "object",
      "description": "按钮状态与沟通历史（核心前置判断）",
      "required": ["button_text", "is_contacted"],
      "properties": {
        "button_text": {
          "type": "string",
          "enum": ["打招呼", "继续沟通", "已沟通", "聊一聊"],
          "description": "右侧主按钮文案。若为'继续沟通'说明已聊过，直接跳过"
        },
        "is_contacted": {
          "type": "boolean",
          "description": "是否已有沟通历史（button_text !== '打招呼'）"
        }
      }
    },
    "basic_info": {
      "type": "object",
      "description": "候选人基础硬性指标（一票否决依据）",
      "required": ["name", "degree", "work_years", "expected_salary"],
      "properties": {
        "name": { "type": "string", "description": "候选人姓名" },
        "active_text": { "type": "string", "description": "在线状态，如：'刚刚活跃'、'在线'、'今日活跃'" },
        "age": { "type": ["string", "null"], "description": "年龄文本，如：'27岁'" },
        "degree": { "type": "string", "description": "学历，如：'本科'、'硕士'" },
        "work_years": { "type": "string", "description": "工作年限，如：'6年'、'应届生'" },
        "job_status": { "type": ["string", "null"], "description": "求职状态，如：'离职-随时到岗'、'在职-月内到岗'" },
        "expected_salary": { "type": "string", "description": "左侧橙色期望薪资标签，如：'10-14K'" },
        "expected_city": { "type": "string", "description": "期望工作城市，如：'北京'" },
        "expected_position": { "type": "string", "description": "期望职位名称，如：'数据开发'" }
      }
    },
    "preview_clues": {
      "type": "object",
      "description": "外层截断线索（用于排除纯不对口专业，或做关键词预加分）",
      "properties": {
        "advantage_snippet": {
          "type": "string",
          "description": "外层个人优势截断文本"
        },
        "highlight_tags": {
          "type": "array",
          "items": { "type": "string" },
          "description": "外层卡片底部的灰色胶囊标签列表"
        },
        "brief_timeline": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "time_span": { "type": "string", "description": "如：'2024.09 - 至今'" },
              "entity_name": { "type": "string", "description": "公司名称或院校名称" },
              "role_or_major": { "type": "string", "description": "职位名或专业名" }
            }
          },
          "description": "卡片右侧极简履历摘要列表（公司/学校、岗位/专业）"
        }
      }
    },
    "_dom_anchor": {
      "type": "object",
      "description": "页面回溯寻址锚点（供通过一级筛选后点开弹窗，或通过后回溯点击）",
      "required": ["dom_card_index"],
      "properties": {
        "dom_card_index": { "type": "integer", "description": "当前页面卡片列表索引（从0开始）" },
        "dom_data_lid": { "type": ["string", "null"], "description": "卡片DOM属性上的 lid/data-lid 唯一标识" },
        "security_id": { "type": ["string", "null"], "description": "卡片绑定的 securityId 参数（若可解析）" },
        "has_close_btn": { "type": "boolean", "description": "右上角是否存在'×(不合适)'按钮" }
      }
    }
  }
}
```

### 2. 真实抓取数据实例（基于图中第一张卡片“赵昊”）

对应截图顶部候选人“赵昊”的完整抽取结果：   

JSON

```
{
  "action_status": {
    "button_text": "打招呼",
    "is_contacted": false
  },
  "basic_info": {
    "name": "赵昊",
    "active_text": "刚刚活跃",
    "age": "27岁",
    "degree": "本科",
    "work_years": "6年",
    "job_status": "离职-随时到岗",
    "expected_salary": "10-14K",
    "expected_city": "北京",
    "expected_position": "数据开发"
  },
  "preview_clues": {
    "advantage_snippet": "六年数据分析 数据治理工作经验；SQL SER...",
    "highlight_tags": [
      "SQL",
      "数据采集",
      "ETL",
      "BI",
      "Kettle"
    ],
    "brief_timeline": [
      {
        "time_span": "2024.09 - 至今",
        "entity_name": "顺丰快递",
        "role_or_major": "财务分析"
      },
      {
        "time_span": "2021.06 - 2024.09",
        "entity_name": "中软国际/政府审计交付部",
        "role_or_major": "数据开发"
      },
      {
        "time_span": "2017 - 2021",
        "entity_name": "黑龙江外国语学院",
        "role_or_major": "计算机科学与..."
      }
    ]
  },
  "_dom_anchor": {
    "dom_card_index": 0,
    "dom_data_lid": "recommend_card_lid_001",
    "security_id": "sec_zhaohao_demo",
    "has_close_btn": true
  }
}
```

### 3. 一级漏斗字段的判定逻辑

抓取上述 JSON 后，代码在打开微简历弹窗前按以下顺序判定，任意一项未通过即触发 `REJECT`：

1. **去重与沟通状态校验**
   - **字段**：`action_status.button_text`   
   - **规则**：若文案等于 `“继续沟通”`、`“已沟通”`，说明已有聊天记录，**直接跳过**。
2. **硬性边界秒筛（Tier 0）**
   - **学历**：`basic_info.degree` $\ge$ `config.min_degree`（如卡“本科”，大专则一票否决）。   
   - **薪资**：解析 `basic_info.expected_salary` 下限（如 `"10-14K"` 的下限为 `10K`），若下限大于 `max_expected_salary_k`，**直接跳过**。   
   - **求职状态**：`basic_info.job_status` 若命中 `["在职-暂不考虑"]`，**直接跳过**。   
   - **活跃度**：`basic_info.active_text` 若为 `"半年前活跃"` 或 `"本月活跃"`（超出配置范围），**直接跳过**。   
3. **排除岗位/专业过滤**
   - **字段**：`basic_info.expected_position` + `preview_clues.brief_timeline`   
   - **规则**：若期望职位或过往经历全部命中 `exclude_position_keywords`（如纯销售、纯文员、纯行政、纯UI设计），**直接跳过**。
4. **通过判定与动作触发**
   - 只有上述 3 步校验全部为 `True` 的卡片，才使用 `_dom_anchor.dom_card_index` 模拟真人鼠标滚动至该卡片并点击打开微简历弹窗，转入二级漏斗进行深层提取与 AI 打分。



## 2.在线简历