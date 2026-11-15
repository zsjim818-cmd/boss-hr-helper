"""Local BOSS-like recommendation fixture (iframe + list + detail panel).

Structurally mirrors the audited real page enough to exercise the read-only
adapter without ever contacting zhipin.com:

- top document hosts ``iframe#recommendFrame`` with the candidate list;
- cards carry a stable ``data-geek-id``;
- detail panel exposes ``data-geek-id``/name/resume JSON and a greet button;
- optional login / captcha / slider / partial-resume states.
"""
from __future__ import annotations

import html
import json


def _resume(identity: str, *, partial: bool, failed: bool) -> dict:
    value = {
        "name": f"BOSS candidate {identity}",
        "degree": "本科",
        "work_years": "5年",
        "job_status": "离职-随时到岗",
        "active_status": "今日活跃",
        "expectation": {"city": "北京", "salary": "15K/月", "position": "Java工程师"},
        "skills": "Java, SQL, Spring Boot",
        "work_experience": [{
            "company": "Example",
            "position": "Java工程师",
            "responsibility": "负责订单后台服务开发，设计SQL事务与幂等约束。",
            "keywords": [],
        }],
        "project_experience": [],
        "education": [{"school": "Example University", "degree": "本科"}],
        "certifications": [],
        "advantage": "",
        "_meta": {"candidate_key": f"job:{identity}",
                  "encrypt_geek_id": identity, "encrypt_job_id": "job"},
    }
    if partial:
        value.pop("skills", None)
        value["work_experience"] = []
    if failed:
        value["ok"] = False
        value["collection_errors"] = ["DETAIL_COLLECTION_FAILED"]
    return value


def build_boss_html(identities: list[str] | None = None, *, login: bool = False,
                    captcha: bool = False, slider: bool = False,
                    partial: bool = False, failed: bool = False,
                    panel_mismatch: bool = False,
                    greet_disabled: bool = False) -> str:
    identities = identities or ["geek-boss-1", "geek-boss-2"]
    cards = []
    for identity in identities:
        cards.append(f"""
        <div class="job-card-box" data-geek-id="{html.escape(identity)}"
             data-encrypt-job-id="boss-job-fixture" data-degree="本科" data-work-years="5">
          <span class="name">BOSS candidate {html.escape(identity)}</span>
          <span class="expect">Java工程师 · 北京</span>
          <button class="open-detail" type="button">详情</button>
        </div>""")
    resume_json = json.dumps(_resume(identities[0], partial=partial, failed=failed),
                             ensure_ascii=False).replace("</", "<\\/")
    body = f"""
<div id="login-guide" class="login-guide" style="display:{'block' if login else 'none'};">
  请扫码登录 BOSS 直聘（本页不会自动代填账号）
</div>
<div id="captcha-dialog" class="captcha-dialog" style="display:{'block' if captcha else 'none'};">
  请完成安全验证
</div>
<div id="slider-verify" class="slider-verify" style="display:{'block' if slider else 'none'};">
  拖动滑块完成验证
</div>
<div id="candidate-list">
{''.join(cards)}
</div>
<div id="detail-panel" data-geek-id="{html.escape(identities[0])}" style="display:none;">
  <h3 id="detail-name">BOSS candidate {html.escape(identities[0])}</h3>
  <pre id="detail-resume">{resume_json}</pre>
  <button class="btn-greet" type="button" {'disabled="disabled"' if greet_disabled else ''}>
    {'今日沟通次数已达上限' if greet_disabled else '打招呼'}
  </button>
</div>
<script>
(function () {{
  const mismatch = {str(panel_mismatch).lower()};
  document.querySelectorAll('.job-card-box').forEach(function (card) {{
    card.querySelector('.open-detail').addEventListener('click', function () {{
      const panel = document.getElementById('detail-panel');
      const identity = mismatch ? 'other-candidate' : card.dataset.geekId;
      panel.dataset.geekId = identity;
      document.getElementById('detail-name').textContent =
        'BOSS candidate ' + card.dataset.geekId;
      panel.style.display = 'block';
    }});
  }});
  document.querySelector('.btn-greet')?.addEventListener('click', function () {{
    // The read-only adapter never clicks this; the fixture makes that auditable.
    window.__BOSS_GREET_CLICKED__ = true;
  }});
}})();
</script>
"""
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>BOSS 推荐牛人（本地夹具）</title></head><body>
<h2>推荐牛人</h2>
<iframe id="recommendFrame" name="recommendFrame" title="recommend"
        style="width: 960px; height: 720px; border: 1px solid #ccc;"
        srcdoc="{html.escape(body, quote=True)}"></iframe>
</body></html>"""
