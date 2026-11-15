"""Local Mock recruitment page rendered with Playwright (no real BOSS access)."""
from __future__ import annotations

import json


def build_html(cards: list[dict], *, page_error: bool = False,
               login_required: bool = False, greeting_confirm: bool = True) -> str:
    """Render a static recommendation page for the given mock cards.

    Cards: {identity, name, position, job_id, resume(dict)}.
    """
    payload = json.dumps(cards, ensure_ascii=False)
    flags = json.dumps({"pageError": page_error, "loginRequired": login_required,
                        "greetingConfirm": greeting_confirm})
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Mock BOSS Recruiter</title>
<style>
 body {{ font-family: sans-serif; margin: 24px; }}
 .candidate-card {{ border: 1px solid #ccc; border-radius: 6px; padding: 10px; margin: 8px 0; }}
 #detail-panel, #login-required, #page-error, #confirm-dialog {{ display: none; }}
 #detail-panel {{ border: 1px solid #409eff; padding: 12px; margin-top: 16px; }}
 .name {{ font-weight: 600; }}
</style></head><body>
<h2>推荐牛人（Mock）</h2>
<div id="login-required">登录已失效，请人工重新登录（本页不提供账号密码）</div>
<div id="page-error">页面加载失败（Mock）</div>
<div id="candidate-list"></div>
<div id="detail-panel">
  <h3 id="detail-name"></h3>
  <pre id="detail-resume" style="white-space: pre-wrap;"></pre>
  <button id="greet-button">打招呼</button>
  <div id="confirm-dialog"><button id="confirm-send">确认发送</button>
    <button id="confirm-cancel">取消</button></div>
  <div id="greet-toast"></div>
</div>
<script>
window.__MOCK__ = {{ cards: {payload}, flags: {flags} }};
(function () {{
  const mock = window.__MOCK__;
  if (mock.flags.loginRequired) {{ document.getElementById('login-required').style.display = 'block'; }}
  if (mock.flags.pageError) {{ document.getElementById('page-error').style.display = 'block'; return; }}
  const list = document.getElementById('candidate-list');
  mock.cards.forEach(function (card, index) {{
    const el = document.createElement('div');
    el.className = 'candidate-card';
    el.dataset.geekId = card.identity;
    el.dataset.index = String(index);
    if (card.platform_job_id) el.dataset.encryptJobId = card.platform_job_id;
    if (card.degree) el.dataset.degree = card.degree;
    if (card.work_years) el.dataset.workYears = card.work_years;
    el.innerHTML = '<span class="name"></span> <span class="position"></span> ' +
      '<button class="open-detail">详情</button>';
    el.querySelector('.name').textContent = card.name;
    el.querySelector('.position').textContent = card.position || '';
    el.querySelector('.open-detail').addEventListener('click', function () {{
      document.getElementById('detail-name').textContent = card.name;
      document.getElementById('detail-resume').textContent = JSON.stringify(card.resume, null, 2);
      document.getElementById('detail-panel').style.display = 'block';
      document.getElementById('detail-panel').dataset.geekId = card.identity;
    }});
    list.appendChild(el);
  }});
  document.getElementById('greet-button').addEventListener('click', function () {{
    const dialog = document.getElementById('confirm-dialog');
    if (!mock.flags.greetingConfirm) {{
      document.getElementById('greet-toast').textContent = '页面状态不确定';
      return;
    }}
    dialog.style.display = 'block';
  }});
}})();
</script></body></html>"""
