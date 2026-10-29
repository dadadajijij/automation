from __future__ import annotations

import json
import textwrap


def _html_escape(value: object) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def render_login_page(
    *, username_default: str, password_configured: bool, public_base_path: str = ""
) -> str:
    payload = json.dumps(
        {
            "usernameDefault": username_default,
            "passwordConfigured": password_configured,
            "publicBasePath": public_base_path,
        },
        ensure_ascii=False,
    )
    return textwrap.dedent(
        f"""\
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width,initial-scale=1" />
          <title>Hermes 管理登录</title>
          <style>
            :root {{
              --bg: #0f1115;
              --panel: #171b22;
              --line: #273041;
              --text: #e8edf4;
              --muted: #93a4bb;
              --accent: #78a6ff;
              --danger: #ff7d7d;
            }}
            * {{ box-sizing: border-box; }}
            body {{
              margin: 0;
              min-height: 100vh;
              display: grid;
              place-items: center;
              background: var(--bg);
              color: var(--text);
              font: 14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
            }}
            .panel {{
              width: min(420px, calc(100vw - 32px));
              border: 1px solid var(--line);
              background: var(--panel);
              border-radius: 8px;
              padding: 20px;
            }}
            h1 {{
              margin: 0 0 8px;
              font-size: 20px;
            }}
            .note {{
              color: var(--muted);
              margin-bottom: 16px;
              font-size: 13px;
            }}
            .field {{
              display: grid;
              gap: 6px;
              margin-bottom: 12px;
            }}
            label {{
              color: var(--muted);
              font-size: 12px;
            }}
            input, button {{
              border: 1px solid var(--line);
              background: #10141a;
              color: var(--text);
              border-radius: 6px;
              padding: 11px 12px;
              font: inherit;
            }}
            button {{
              width: 100%;
              cursor: pointer;
              background: var(--accent);
              color: #0d1420;
              font-weight: 700;
            }}
            button:disabled {{ opacity: .6; cursor: not-allowed; }}
            .error {{
              margin-top: 12px;
              color: var(--danger);
              min-height: 1.2em;
              font-size: 13px;
            }}
          </style>
        </head>
        <body>
          <div class="panel">
            <h1>Hermes 管理登录</h1>
            <div class="note">输入管理员用户名和密码后进入后台。</div>
            <div class="field">
              <label for="username">用户名</label>
              <input id="username" value="{_html_escape(username_default)}" autocomplete="username" />
            </div>
            <div class="field">
              <label for="password">密码</label>
              <input id="password" type="password" autocomplete="current-password" />
            </div>
            <button id="loginBtn">登录</button>
            <div class="error" id="error"></div>
          </div>

          <script>
            window.__HERMES_LOGIN__ = {payload};
            const errorBox = document.getElementById('error');
            const loginBtn = document.getElementById('loginBtn');
            const usernameInput = document.getElementById('username');
            const passwordInput = document.getElementById('password');

            function showError(text) {{
              errorBox.textContent = text || '';
            }}

            if (!window.__HERMES_LOGIN__.passwordConfigured) {{
              showError('请先在环境变量里设置 ADMIN_PASSWORD');
              loginBtn.disabled = true;
            }}

            async function login() {{
              showError('');
              loginBtn.disabled = true;
              try {{
                const resp = await fetch(`${{window.__HERMES_LOGIN__.publicBasePath}}/admin/login`, {{
                  method: 'POST',
                  headers: {{ 'content-type': 'application/json' }},
                  body: JSON.stringify({{
                    username: usernameInput.value.trim(),
                    password: passwordInput.value,
                  }}),
                }});
                const data = await resp.json().catch(() => ({{}}));
                if (!resp.ok) {{
                  throw new Error(data.detail || data.message || '登录失败');
                }}
                location.href = data.redirect || `${{window.__HERMES_LOGIN__.publicBasePath}}/admin`;
              }} catch (err) {{
                showError(err.message || '登录失败');
                loginBtn.disabled = false;
              }}
            }}

            loginBtn.addEventListener('click', login);
            passwordInput.addEventListener('keydown', (event) => {{
              if (event.key === 'Enter') login();
            }});
          </script>
        </body>
        </html>
        """
    )


def render_chat_page(*, username: str, csrf_token: str, public_base_path: str = "") -> str:
    payload = json.dumps(
        {
            "username": username,
            "csrfToken": csrf_token,
            "publicBasePath": public_base_path,
        },
        ensure_ascii=False,
    )
    return textwrap.dedent(
        f"""\
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width,initial-scale=1" />
          <title>Hermes 在线对话</title>
          <style>
            :root {{
              --bg: #0f1115;
              --panel: #171b22;
              --panel-2: #10141a;
              --line: #273041;
              --text: #e8edf4;
              --muted: #93a4bb;
              --accent: #78a6ff;
              --danger: #ff7d7d;
            }}
            * {{ box-sizing: border-box; }}
            body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
            header {{ padding: 16px 20px; border-bottom: 1px solid var(--line); background: #12161c; }}
            .topbar {{ max-width: 1080px; margin: 0 auto; display: flex; gap: 12px; align-items: center; justify-content: space-between; }}
            h1 {{ margin: 0; font-size: 18px; }}
            .muted {{ color: var(--muted); font-size: 12px; }}
            .nav {{ color: var(--text); text-decoration: none; border: 1px solid var(--line); padding: 8px 10px; border-radius: 6px; }}
            main {{ max-width: 1080px; margin: 0 auto; padding: 20px; display: grid; grid-template-columns: minmax(0, .8fr) minmax(0, 1.2fr); gap: 16px; }}
            section {{ border: 1px solid var(--line); background: var(--panel); border-radius: 6px; padding: 16px; }}
            h2 {{ margin: 0 0 14px; font-size: 15px; }}
            .field {{ display: grid; gap: 6px; margin-bottom: 12px; }}
            label {{ color: var(--muted); font-size: 12px; }}
            input, textarea, button {{ width: 100%; border: 1px solid var(--line); background: var(--panel-2); color: var(--text); border-radius: 6px; padding: 10px 12px; font: inherit; }}
            textarea {{ min-height: 180px; resize: vertical; }}
            input[type="file"] {{ padding: 8px; }}
            .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
            button {{ cursor: pointer; background: var(--accent); color: #0d1420; font-weight: 700; }}
            button.secondary {{ background: #1c2330; color: var(--text); }}
            button:disabled {{ opacity: .6; cursor: not-allowed; }}
            .actions {{ display: grid; grid-template-columns: 1fr auto; gap: 8px; }}
            .attachments {{ margin: 8px 0 0; padding: 0; list-style: none; display: grid; gap: 6px; }}
            .attachments li {{ padding: 8px 10px; border: 1px solid var(--line); border-radius: 5px; color: var(--muted); overflow-wrap: anywhere; }}
            pre {{ margin: 0; min-height: 380px; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.55 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; }}
            .status {{ min-height: 1.4em; margin: 0 0 10px; color: var(--muted); }}
            .status.error {{ color: var(--danger); }}
            @media (max-width: 800px) {{ main {{ grid-template-columns: 1fr; padding: 12px; }} .row {{ grid-template-columns: 1fr; }} }}
          </style>
        </head>
        <body>
          <header>
            <div class="topbar">
              <div><h1>Hermes 在线对话</h1><div class="muted">管理员：{_html_escape(username)}</div></div>
              <div style="display:flex; gap:8px; align-items:center;">
                <a class="nav" id="dashboardLink">返回管理面板</a>
                <a class="nav" id="latencyLink">耗时统计</a>
              </div>
            </div>
          </header>
          <main>
            <section>
              <h2>请求</h2>
              <div class="row">
                <div class="field"><label for="userId">用户 ID</label><input id="userId" value="admin-playground" maxlength="120" /></div>
                <div class="field"><label for="sessionId">会话 ID</label><input id="sessionId" maxlength="120" placeholder="留空创建新会话" /></div>
              </div>
              <div class="field"><label for="question">问题</label><textarea id="question" maxlength="20000" placeholder="输入问题，或只上传附件"></textarea></div>
              <div class="field"><label for="files">图片或文件</label><input id="files" type="file" multiple /></div>
              <ul class="attachments" id="attachments"></ul>
              <div class="actions"><button id="sendBtn">发送</button><button class="secondary" id="newSessionBtn" type="button">新会话</button></div>
            </section>
            <section>
              <h2>回答</h2>
              <div class="status" id="status"></div>
              <pre id="answer">等待请求。</pre>
            </section>
          </main>
          <script>
            window.__HERMES_ADMIN_CHAT__ = {payload};
            const config = window.__HERMES_ADMIN_CHAT__;
            const route = (path) => {{
              const base = String(config.publicBasePath || '').replace(/\\/+$/, '');
              const suffix = String(path || '').startsWith('/') ? String(path) : `/${{path}}`;
              return (base.startsWith('/') ? base : `/${{base}}`) + suffix;
            }};
              const adminPath = (suffix) => route('/' + 'admin' + '/v1' + suffix);
            const els = {{
              userId: document.getElementById('userId'), sessionId: document.getElementById('sessionId'),
              question: document.getElementById('question'),
              files: document.getElementById('files'), attachments: document.getElementById('attachments'),
              sendBtn: document.getElementById('sendBtn'), newSessionBtn: document.getElementById('newSessionBtn'),
              status: document.getElementById('status'), answer: document.getElementById('answer'),
            }};
            // Keep the admin path split so the reverse proxy's legacy URL rewrite
            // cannot rewrite an already prefixed path a second time.
            document.getElementById('dashboardLink').href = route('/' + 'admin');
            document.getElementById('latencyLink').href = route('/' + 'admin' + '/latency');

            function formatBytes(value) {{
              if (value < 1024) return `${{value}} B`;
              if (value < 1024 * 1024) return `${{(value / 1024).toFixed(1)}} KB`;
              return `${{(value / 1024 / 1024).toFixed(1)}} MB`;
            }}
            function renderFiles() {{
              const items = Array.from(els.files.files || []);
              els.attachments.replaceChildren(...items.map((file) => {{
                const item = document.createElement('li');
                item.textContent = `${{file.name}} (${{formatBytes(file.size)}})${{file.type ? ` - ${{file.type}}` : ''}}`;
                return item;
              }}));
            }}
            function setStatus(value, error = false) {{
              els.status.textContent = value || '';
              els.status.classList.toggle('error', error);
            }}
            async function send() {{
              const text = els.question.value.trim();
              if (!text && !(els.files.files || []).length) {{
                setStatus('请输入问题或选择附件。', true);
                return;
              }}
              const form = new FormData();
              form.set('text', text);
              form.set('user_id', els.userId.value.trim() || 'admin-playground');
              form.set('session_id', els.sessionId.value.trim());
              Array.from(els.files.files || []).forEach((file) => form.append('files', file, file.name));
              els.sendBtn.disabled = true;
              setStatus('Hermes 正在处理附件和请求...');
              els.answer.textContent = '';
              try {{
                const resp = await fetch(adminPath('/chat'), {{
                  method: 'POST', credentials: 'same-origin',
                  headers: {{ 'x-csrf-token': config.csrfToken }}, body: form,
                }});
                const data = await resp.json().catch(() => ({{}}));
                if (resp.status === 401) {{ location.href = route('/admin/login'); return; }}
                if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.message || resp.statusText);
                els.sessionId.value = data.session_id || els.sessionId.value;
                els.answer.textContent = data.answer || '';
                els.files.value = '';
                renderFiles();
                setStatus(`完成：${{data.session_id || ''}}`);
              }} catch (err) {{
                setStatus(err.message || '请求失败', true);
                els.answer.textContent = '';
              }} finally {{
                els.sendBtn.disabled = false;
              }}
            }}
            els.files.addEventListener('change', renderFiles);
            els.sendBtn.addEventListener('click', send);
            els.newSessionBtn.addEventListener('click', () => {{ els.sessionId.value = ''; els.answer.textContent = '等待请求。'; setStatus(''); }});
          </script>
        </body>
        </html>
        """
    )


def render_dashboard_page(
    *, username: str, csrf_token: str, public_base_path: str = ""
) -> str:
    payload = json.dumps(
        {"username": username, "csrfToken": csrf_token, "publicBasePath": public_base_path},
        ensure_ascii=False,
    )


    return textwrap.dedent(
        f"""\
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width,initial-scale=1" />
          <title>Hermes 管理面板</title>
          <style>
            :root {{
              --bg: #0f1115;
              --panel: #171b22;
              --panel-2: #10141a;
              --line: #273041;
              --text: #e8edf4;
              --muted: #93a4bb;
              --accent: #78a6ff;
              --danger: #ff7d7d;
              --ok: #5dd39e;
            }}
            * {{ box-sizing: border-box; }}
            body {{
              margin: 0;
              background: var(--bg);
              color: var(--text);
              font: 14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
            }}
            header {{
              position: sticky;
              top: 0;
              z-index: 5;
              padding: 16px 20px;
              background: rgba(18,22,28,.95);
              border-bottom: 1px solid var(--line);
            }}
            .topbar {{
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 12px;
            }}
            .account-actions {{
              display: flex;
              align-items: center;
              gap: 8px;
            }}
            h1 {{ margin: 0; font-size: 18px; }}
            .toolbar {{
              margin-top: 12px;
              display: flex;
              align-items: center;
              flex-wrap: wrap;
              gap: 8px;
            }}
            input, button {{
              border: 1px solid var(--line);
              background: var(--panel-2);
              color: var(--text);
              border-radius: 6px;
              padding: 10px 12px;
              font: inherit;
            }}
            .toolbar input {{ padding: 8px 10px; min-width: 0; }}
            #userFilter {{ width: 220px; }}
            #searchInput {{ width: 300px; }}
            mark {{ background: #ffe08a; color: #251b00; border-radius: 2px; }}
            button:disabled {{ cursor: default; opacity: .45; }}
            #conversationDetail {{ overflow-wrap: anywhere; line-height: 1.8; }}
            .toolbar-nav {{ display: flex; gap: 8px; margin-left: auto; }}
            button {{
              cursor: pointer;
              background: #1c2330;
            }}
            .nav-link {{
              display: inline-flex;
              align-items: center;
              justify-content: center;
              border: 1px solid var(--line);
              background: #1c2330;
              color: var(--text);
              border-radius: 6px;
              padding: 10px 12px;
              text-decoration: none;
            }}
            button.primary {{ background: var(--accent); color: #0d1420; font-weight: 700; border-color: transparent; }}
            main {{
              padding: 16px 20px 24px;
              display: grid;
              gap: 16px;
            }}
            .summary {{
              display: grid;
              grid-template-columns: repeat(4, minmax(0, 1fr));
              gap: 12px;
            }}
            .stat {{
              border: 1px solid var(--line);
              background: var(--panel);
              border-radius: 6px;
              padding: 12px;
            }}
            .label {{ color: var(--muted); font-size: 12px; }}
            .value {{ margin-top: 4px; font-size: 22px; font-weight: 700; }}
            .layout {{
              display: grid;
              grid-template-columns: minmax(0, .72fr) minmax(0, 1fr) minmax(0, 1.9fr);
              gap: 16px;
              align-items: start;
            }}
            section {{
              border: 1px solid var(--line);
              background: var(--panel);
              border-radius: 6px;
              overflow: hidden;
              min-height: 360px;
            }}
            .section-head {{
              padding: 12px 14px;
              border-bottom: 1px solid var(--line);
              display: flex;
              justify-content: space-between;
              gap: 10px;
            }}
            .section-head h2 {{
              margin: 0;
              font-size: 14px;
            }}
            .section-body {{ padding: 12px 14px; }}
            .subtoolbar {{ display: flex; gap: 8px; margin-bottom: 10px; }}
            .subtoolbar input {{ flex: 1; }}
            table {{
              width: 100%;
              border-collapse: collapse;
              font-size: 13px;
            }}
            .sessions-table {{
              table-layout: fixed;
            }}
            .sessions-table th:nth-child(1),
            .sessions-table td:nth-child(1) {{
              width: 68%;
              overflow-wrap: anywhere;
            }}
            .sessions-table th:nth-child(2),
            .sessions-table td:nth-child(2) {{
              width: 18%;
            }}
            .sessions-table th:nth-child(3),
            .sessions-table td:nth-child(3) {{
              width: 14%;
            }}
            th, td {{
              border-bottom: 1px solid var(--line);
              padding: 8px 6px;
              text-align: left;
              vertical-align: top;
            }}
            th {{ color: var(--muted); font-size: 12px; }}
            tr.clickable {{ cursor: pointer; }}
            tr.clickable:hover {{ background: rgba(120,166,255,.08); }}
            tr.selected {{ background: rgba(120,166,255,.14); }}
            .meta {{ color: var(--muted); font-size: 12px; }}
            .pill {{
              display: inline-block;
              border: 1px solid var(--line);
              border-radius: 999px;
              padding: 2px 8px;
              font-size: 11px;
              color: var(--muted);
            }}
            .pill.ok {{ color: var(--ok); }}
            .pill.bad {{ color: var(--danger); }}
            .detail-grid {{ display: grid; gap: 12px; }}
            .detail-box {{
              border: 1px solid var(--line);
              border-radius: 6px;
              padding: 10px;
              background: #11151b;
            }}
            .detail-box h3 {{ margin: 0 0 8px; font-size: 13px; }}
            pre {{
              margin: 0;
              white-space: pre-wrap;
              word-break: break-word;
              font: 12px/1.5 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
              color: #d7e2f0;
            }}
            .messages {{ display: grid; gap: 8px; }}
            .request-body {{ margin-top: 10px; }}
            .request-body summary {{ cursor: pointer; color: var(--muted); font-size: 12px; }}
            .request-body pre {{ margin-top: 8px; max-height: 360px; overflow: auto; }}
            .request-stages {{ margin-top: 10px; }}
            .request-stages summary {{ cursor: pointer; color: var(--muted); font-size: 12px; }}
            .request-stage {{
              display: grid;
              grid-template-columns: minmax(0, 1fr) auto;
              gap: 8px;
              margin-top: 6px;
              color: var(--muted);
              font-size: 12px;
            }}
            .request-stage-detail {{ grid-column: 1 / -1; overflow-wrap: anywhere; }}
            .runtime-audit {{ margin-top: 8px; color: var(--muted); font-size: 12px; line-height: 1.55; }}
            .runtime-audit strong {{ color: var(--text); font-weight: 600; }}
            .runtime-audit-details {{ margin-top: 8px; }}
            .runtime-audit-details summary {{ cursor: pointer; color: var(--muted); font-size: 12px; }}
            .runtime-audit-event {{ margin-top: 6px; overflow-wrap: anywhere; }}
            .msg {{
              border: 1px solid var(--line);
              border-radius: 6px;
              padding: 10px;
              background: #10141a;
            }}
            .msg-head {{
              display: flex;
              justify-content: space-between;
              gap: 10px;
              margin-bottom: 6px;
              color: var(--muted);
              font-size: 12px;
            }}
            .notice {{ color: var(--muted); font-size: 12px; }}
            .skill-bindings {{ margin-top: 14px; border-top: 1px solid var(--line); padding-top: 12px; }}
            .skill-binding-list {{ display: grid; gap: 8px; margin-top: 10px; }}
            .skill-binding-groups {{ display: grid; gap: 8px; margin-top: 10px; }}
            .skill-binding-group {{ border: 1px solid var(--line); }}
            .skill-binding-group summary {{
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 10px;
              padding: 9px 10px;
              cursor: pointer;
              color: var(--text);
            }}
            .skill-binding-group[open] summary {{ border-bottom: 1px solid var(--line); }}
            .skill-binding-group .skill-binding-list {{ margin: 0; padding: 10px; }}
            .skill-binding {{ display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px; align-items: start; }}
            .skill-binding input {{ width: auto; margin-top: 3px; }}
            .skill-binding-name {{ overflow-wrap: anywhere; }}
            .skill-binding-actions {{ margin-top: 10px; display: flex; justify-content: flex-end; }}
            .foot {{ color: var(--muted); font-size: 12px; }}
            @media (max-width: 1200px) {{
              .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
              .layout {{ grid-template-columns: 1fr; }}
            }}
          </style>
        </head>
        <body>
          <header>
            <div class="topbar">
              <h1>Hermes 管理面板</h1>
              <div class="account-actions">
                <button id="logoutBtn">退出</button>
              </div>
            </div>
            <div class="toolbar">
              <input id="userFilter" type="text" placeholder="搜索用户" autocomplete="off" />
              <input id="searchInput" type="text" placeholder="搜索会话信息" autocomplete="off" />
              <span class="meta">按回车应用搜索条件</span><button id="refreshBtn">刷新</button><button id="resetBtn">重置全部</button>
              <button id="clearUserBtn">清除用户条件</button><button id="clearSearchBtn">清除会话条件</button>
              <div class="toolbar-nav">
              <a class="nav-link" id="chatLink">在线对话</a>
              <a class="nav-link" id="latencyLink">耗时统计</a>
              </div>
            </div>
          </header>

          <main>
            <div class="summary">
              <div class="stat"><div class="label">全局用户</div><div class="value" id="usersCount">-</div></div>
              <div class="stat"><div class="label">全局会话</div><div class="value" id="sessionsCount">-</div></div>
              <div class="stat"><div class="label">全局请求</div><div class="value" id="requestsCount">-</div></div>
              <div class="stat"><div class="label">全局消息</div><div class="value" id="messagesCount">-</div></div>
            </div>

            <div class="layout">
              <section>
                <div class="section-head">
                  <h2>用户</h2>
                  <span class="meta" id="usersMeta">全部</span>
                </div>
                <div class="section-body">
                  <table>
                    <thead>
                      <tr><th>用户</th><th>会话</th><th>消息</th></tr>
                    </thead>
                    <tbody id="usersTable"></tbody>
                  </table>
                  <div class="skill-bindings">
                    <div class="section-head" style="padding:0 0 10px; border:0;">
                      <h2 style="font-size:13px;">Skills</h2>
                      <span class="meta" id="skillsMeta">未选择用户</span>
                    </div>
                    <div class="skill-binding-list" id="skillsList"><div class="notice">选择用户后管理绑定</div></div>
                    <div class="skill-binding-actions"><button class="primary" id="saveSkillsBtn" disabled>保存绑定</button></div>
                  </div>
                </div>
              </section>

              <section>
                <div class="section-head">
                  <h2>会话</h2>
                  <span class="meta" id="sessionsMeta">全部</span>
                </div>
                <div class="section-body">
                  <table class="sessions-table">
                    <thead>
                      <tr><th>会话</th><th>状态</th><th>消息</th></tr>
                    </thead>
                    <tbody id="sessionsTable"></tbody>
                  </table>
                  <div style="display:flex;gap:8px;margin-top:10px"><button id="allUsersBtn">全部匹配用户</button><button id="prevPageBtn">上一页</button><button id="nextPageBtn">下一页</button></div>
                </div>
              </section>

              <section>
                <div class="section-head">
                  <h2>详情 / 消息</h2>
                  <span class="meta" id="detailMeta">未选择</span>
                </div>
                <div class="section-body detail-grid">
                  <div class="detail-box">
                    <h3>会话信息</h3>
                    <div id="conversationDetail">请选择一个会话</div>
                  </div>
                  <div class="detail-box">
                    <h3>请求 / 消息时间线</h3>
                    <div class="messages" id="messagesList">
                      <div class="notice">请选择一个会话查看消息</div>
                    </div>
                  </div>
                  <div class="detail-box">
                    <h3>当前会话命中导航</h3><button id="prevMatchBtn">上一处</button> <button id="nextMatchBtn">下一处</button> <label><input type="checkbox" id="onlyMatches" style="width:auto" />仅看命中</label>
                    <div class="messages" id="searchResults">
                      <div class="notice">输入会话信息后按回车筛选</div>
                    </div>
                  </div>
                </div>
              </section>
            </div>

            <div class="foot" id="footerNote">管理员登录后可查看所有用户、会话和消息。</div>
          </main>

          <script>
            window.__HERMES_ADMIN__ = {payload};
            const basePath = window.__HERMES_ADMIN__.publicBasePath || '';
            const route = (path) => `${{basePath}}${{path}}`;
            const adminPath = (suffix) => route('/' + 'admin' + '/v1' + suffix);
            document.getElementById('chatLink').href = route('/admin/chat');
            document.getElementById('latencyLink').href = route('/admin/latency');
            const state = {{
              userFilterText: '',
              selectedUser: '',
              selectedSession: '',
              boundSkillIds: new Set(),
              skillsUser: '',
              keyword: '', scopeUser: '', users: [], conversations: [], offset: 0,
              generation: 0, timeline: [], matchIndex: -1,
            }};
            const els = {{
              searchInput: document.getElementById('searchInput'),
              userFilter: document.getElementById('userFilter'),
              refreshBtn: document.getElementById('refreshBtn'),
              logoutBtn: document.getElementById('logoutBtn'),
              usersCount: document.getElementById('usersCount'),
              sessionsCount: document.getElementById('sessionsCount'),
              requestsCount: document.getElementById('requestsCount'),
              messagesCount: document.getElementById('messagesCount'),
              usersMeta: document.getElementById('usersMeta'),
              sessionsMeta: document.getElementById('sessionsMeta'),
              detailMeta: document.getElementById('detailMeta'),
              usersTable: document.getElementById('usersTable'),
              sessionsTable: document.getElementById('sessionsTable'),
              conversationDetail: document.getElementById('conversationDetail'),
              messagesList: document.getElementById('messagesList'),
              searchResults: document.getElementById('searchResults'),
              skillsMeta: document.getElementById('skillsMeta'),
              skillsList: document.getElementById('skillsList'),
              saveSkillsBtn: document.getElementById('saveSkillsBtn'),
            }};

            function esc(v) {{
              return String(v ?? '')
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#39;');
            }}

            function formatDuration(value) {{
              const ms = Number(value) || 0;
              return ms < 1000 ? `${{Math.round(ms)}} ms` : `${{(ms / 1000).toFixed(1)}} s`;
            }}

            function runtimeEvents(audit) {{
              return Array.isArray(audit?.events) ? audit.events : [];
            }}

            function runtimeLabel(event) {{
              const route = [event.model, event.provider].filter(Boolean).join(' • ');
              if (event.kind === 'main_fallback') {{
                const previous = [event.from_model, event.from_provider].filter(Boolean).join(' • ');
                return `主模型回退：${{previous || '-'}} -> ${{route || '-'}}${{event.reason ? `（${{event.reason}}）` : ''}}`;
              }}
              if (event.kind === 'image_input') {{
                return `图片输入：${{event.input_mode || '-'}} (${{route || '-'}})`;
              }}
              if (event.kind === 'auxiliary') {{
                return `${{event.task || '辅助任务'}}：${{route || '-'}}`;
              }}
              return route || event.kind || '-';
            }}

            function renderRuntimeAudit(audit) {{
              if (!audit) return '';
              const events = runtimeEvents(audit);
              const skillAudit = audit.skill_audit || {{}};
              const skillResults = Array.isArray(skillAudit.skills) ? skillAudit.skills : [];
              const main = audit.main || {{}};
              const finalRoute = [main.model, main.provider].filter(Boolean).join(' • ');
              const image = [...events].reverse().find((event) => event.kind === 'image_input');
              const vision = [...events].reverse().find((event) => event.kind === 'auxiliary' && event.task === 'vision');
              const fallback = events.some((event) => event.kind === 'main_fallback');
              const skills = Array.isArray(audit.skills) ? audit.skills : [];
              const summary = [
                finalRoute ? `实际主模型：${{finalRoute}}${{fallback ? '（已回退）' : ''}}` : '',
                image ? `图片：${{image.input_mode || '-'}}` : '',
                vision ? `视觉：${{[vision.model, vision.provider].filter(Boolean).join(' • ') || '-'}}` : '',
                skills.length ? `Skills：${{skills.map((skill) => skill.id).filter(Boolean).join(', ')}}` : '',
                skillResults.length ? `Skill 状态：${{skillResults.map((skill) => `${{skill.id || '-'}}=${{skill.status || 'unknown'}}`).join(', ')}}` : '',
                audit.context_window ? `上下文：${{audit.context_window}}` : '',
              ].filter(Boolean);
              if (!summary.length && !events.length && !skillResults.length) return '';
              const skillDetails = skillResults.length ? `<details class="runtime-audit-details"><summary>展开 Skill 执行详情</summary>${{skillResults.map((skill) => `<div class="runtime-audit-event">${{esc(skill.id || '-')}}：${{esc(skill.status || 'unknown')}}${{skill.reason ? `（${{esc(skill.reason)}}）` : ''}}${{skill.duration_ms != null ? ` • ${{formatDuration(skill.duration_ms)}}` : ''}}</div>`).join('')}}</details>` : '';
              return `<div class="runtime-audit">${{summary.map((line) => `<div>${{esc(line)}}</div>`).join('')}}${{skillDetails}}${{events.length ? `<details class="runtime-audit-details"><summary>展开模型执行详情</summary>${{events.map((event) => `<div class="runtime-audit-event">${{esc(runtimeLabel(event))}}</div>`).join('')}}</details>` : ''}}</div>`;
            }}

            async function api(path, options = {{}}) {{
              const resp = await fetch(path, {{
                ...options,
                credentials: 'same-origin',
                headers: {{ 'content-type': 'application/json', ...(options.headers || {{}}) }},
              }});
              const data = await resp.json().catch(() => ({{}}));
              if (resp.status === 401) {{
                location.href = route('/admin/login');
                return data;
              }}
              if (!resp.ok) {{
                throw new Error(data.detail || data.message || resp.statusText);
              }}
              return data;
            }}

            function renderUsers(items) {{
              els.usersMeta.textContent = `${{items.length}} 个匹配用户${{state.userFilterText ? ' · ' + state.userFilterText : ''}}`;
              els.usersTable.innerHTML = items.length ? items.map((row) => `
                <tr class="clickable ${{row.user_id === state.selectedUser ? 'selected' : ''}}" data-user="${{esc(row.user_id)}}">
                  <td><strong>${{esc(row.user_id)}}</strong><div class="meta">${{esc(row.default_hermes_profile || '-')}}</div></td>
                  <td>${{row.conversation_count}}</td><td>${{row.message_count}}</td>
                </tr>`).join('') : '<tr><td colspan="3" class="meta">没有匹配用户</td></tr>';
              els.usersTable.querySelectorAll('[data-user]').forEach((row) => {{
                row.addEventListener('click', () => {{
                  state.scopeUser = row.getAttribute('data-user') || '';
                  state.selectedSession = ''; state.offset = 0;
                  refreshAll();
                }});
              }});
            }}

            function renderSkills(data) {{
              if (!data) {{
                state.boundSkillIds = new Set();
                state.skillsUser = '';
                els.skillsMeta.textContent = '未选择用户';
                els.skillsList.innerHTML = '<div class="notice">选择用户后管理绑定</div>';
                els.saveSkillsBtn.disabled = true;
                return;
              }}
              const bound = new Set(data.bound_skill_ids || []);
              state.boundSkillIds = new Set(bound);
              state.skillsUser = data.user_id;
              const unavailable = new Set(data.unavailable_skill_ids || []);
              els.skillsMeta.textContent = data.user_id;
              const skills = data.available_skills || [];
              const skillGroups = skills.reduce((groups, skill) => {{
                const source = skill.category || '未分类';
                (groups[source] ||= []).push(skill);
                return groups;
              }}, {{}});
              els.skillsList.innerHTML = skills.length ? `<div class="skill-binding-groups">${{Object.entries(skillGroups).map(([source, groupSkills]) => {{
                const selectedCount = groupSkills.filter((skill) => bound.has(skill.id)).length;
                return `
                  <details class="skill-binding-group">
                    <summary>
                      <strong>${{esc(source)}}</strong>
                      <span class="meta">${{selectedCount}} / ${{groupSkills.length}}</span>
                    </summary>
                    <div class="skill-binding-list">
                      ${{groupSkills.map((skill) => `
                        <label class="skill-binding">
                          <input type="checkbox" data-skill="${{esc(skill.id)}}" ${{bound.has(skill.id) ? 'checked' : ''}} ${{skill.enabled || bound.has(skill.id) ? '' : 'disabled'}} />
                          <span class="skill-binding-name"><strong>${{esc(skill.name)}}</strong></span>
                        </label>
                      `).join('')}}
                    </div>
                  </details>`;
              }}).join('')}}</div>` : '<div class="notice">当前 default profile 没有可用 skills</div>';
              if (unavailable.size) {{
                const note = document.createElement('div');
                note.className = 'notice';
                note.innerHTML = `失效绑定：${{Array.from(unavailable).map((id) => `<label class="skill-binding"><input type="checkbox" data-skill="${{esc(id)}}" checked /><span>${{esc(id)}}</span></label>`).join('')}}`;
                els.skillsList.appendChild(note);
              }}
              els.saveSkillsBtn.disabled = false;
            }}

            function renderConversations(items) {{
              els.sessionsTable.innerHTML = items.length ? items.map((row) => `
                <tr class="clickable ${{row.external_session_id === state.selectedSession ? 'selected' : ''}}" data-session="${{esc(row.external_session_id)}}" data-user="${{esc(row.user_id)}}">
                  <td><strong>${{highlight(row.title || row.external_session_id)}}</strong><div class="meta">${{esc(row.external_session_id)}} · ${{esc(row.user_id)}}</div>${{state.keyword ? `<div class="meta">命中 ${{row.match_count}} 条消息${{!row.match_count ? '（会话标题 / ID 匹配）' : ''}}</div>${{row.match_preview ? `<div class="meta">…${{highlight(row.match_preview)}}…</div>` : ''}}` : ''}}</td>
                  <td><span class="pill ${{row.status === 'error' ? 'bad' : 'ok'}}">${{esc(row.status)}}</span></td><td>${{row.message_count}}</td>
                </tr>`).join('') : '<tr><td colspan="3" class="meta">当前条件下暂无会话</td></tr>';
              els.sessionsTable.querySelectorAll('[data-session]').forEach((row) => {{
                row.addEventListener('click', async () => {{
                  const generation = ++state.generation;
                  state.selectedSession = row.getAttribute('data-session') || '';
                  state.selectedUser = row.getAttribute('data-user') || '';
                  renderUsers(state.users); renderConversations(state.conversations);
                  renderConversation(null); renderSkills(null);
                  await Promise.all([loadConversation(generation), loadUserDetails().catch(error => {{
                    if (generation === state.generation) els.skillsMeta.textContent = `Skills 加载失败：${{error.message}}`;
                  }})]).catch(error => {{ if (generation === state.generation) showError(error); }});
                }});
              }});
            }}

            function renderConversation(data) {{
              if (!data) {{
                els.detailMeta.textContent = '未选择';
                els.conversationDetail.textContent = '当前没有选中的会话';
                els.messagesList.innerHTML = '<div class="notice">当前没有会话消息</div>';
                state.timeline = []; state.matchIndex = -1;
                document.getElementById('prevMatchBtn').disabled = true;
                document.getElementById('nextMatchBtn').disabled = true;
                els.searchResults.textContent = '当前没有命中内容';
                return;
              }}
              els.detailMeta.textContent = `${{data.user_id}} / ${{data.external_session_id}}`;
              els.conversationDetail.innerHTML = [
                ['用户', data.user_id], ['会话 ID', data.external_session_id], ['标题', data.title],
                ['状态', data.status], ['创建时间', data.created_at], ['更新时间', data.updated_at],
              ].map(([label, value]) => `<div><strong>${{label}}：</strong>${{esc(value || '-')}}</div>`).join('') +
                `<details><summary>展开原始 JSON</summary><pre>${{esc(JSON.stringify(data, null, 2))}}</pre></details>`;
            }}

            function highlight(value) {{
              const text = String(value ?? '');
              if (!state.keyword) return esc(text);
              const lower = text.toLowerCase(), keyword = state.keyword.toLowerCase();
              let out = '', pos = 0, index;
              while ((index = lower.indexOf(keyword, pos)) !== -1) {{
                out += esc(text.slice(pos, index)) + '<mark>' + esc(text.slice(index, index + keyword.length)) + '</mark>';
                pos = index + keyword.length;
              }}
              return out + esc(text.slice(pos));
            }}
            function isMatch(row) {{
              if (!state.keyword) return false;
              return [row.prompt, row.content, row.role, row.kind].some(value =>
                String(value || '').toLowerCase().includes(state.keyword.toLowerCase()));
            }}
            function navigateMatch(delta = 0) {{
              const matches = Array.from(els.messagesList.querySelectorAll('[data-hit]'));
              if (!matches.length) return;
              state.matchIndex = (Math.max(0, state.matchIndex) + delta + matches.length) % matches.length;
              matches.forEach((node, index) => {{ node.style.outline = index === state.matchIndex ? '2px solid var(--accent)' : ''; }});
              matches[state.matchIndex].scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
              els.searchResults.textContent = `当前会话命中 ${{matches.length}} 条请求 / 消息 · 第 ${{state.matchIndex + 1}} 条`;
            }}

            function renderMessages(items) {{
              const requestKeys = new Set(
                items
                  .filter((row) => row.event_type === 'request')
                  .map((row) => JSON.stringify([row.user_id, row.prompt]))
              );
              state.timeline = items;
              const visibleItems = items.filter((row) => !(
                row.event_type === 'message' &&
                row.role === 'user' &&
                requestKeys.has(JSON.stringify([row.user_id, row.content]))
              ));
              const shownItems = document.getElementById('onlyMatches').checked ? visibleItems.filter(isMatch) : visibleItems;
              els.messagesList.innerHTML = shownItems.length ? shownItems.map((row) => `
                <div class="msg" ${{isMatch(row) ? 'data-hit="true"' : ''}}>
                  <div class="msg-head">
                    <span><strong>${{row.event_type === 'request' ? '用户请求' : esc(row.role)}}</strong> / ${{row.event_type === 'request' ? esc(row.status) : esc(row.kind)}}</span>
                    <span>${{esc(row.created_at)}}</span>
                  </div>
                  <div class="meta">${{esc(row.model || '-')}} • ${{esc(row.provider || '-')}}</div>
                  ${{row.event_type === 'request' ? `
                    <div style="margin-top:8px; white-space:pre-wrap; word-break:break-word;">${{highlight(row.prompt)}}</div>
                    ${{renderRuntimeAudit(row.runtime_audit)}}
                    ${{row.request_body ? `<details class="request-body"><summary>展开原始 Body</summary><pre>${{esc(row.request_body)}}</pre></details>` : ''}}
                    ${{row.stages && row.stages.length ? `<details class="request-stages"><summary>展开阶段耗时</summary>${{row.stages.map((stage) => `<div class="request-stage"><span>${{esc(stage.stage)}}</span><span>${{formatDuration(stage.duration_ms)}}</span>${{stage.detail ? `<span class="request-stage-detail">${{esc(stage.detail)}}</span>` : ''}}</div>`).join('')}}</details>` : ''}}
                    <div class="meta" style="margin-top:8px;">${{row.status === 'completed' ? 'Hermes 已成功返回' : row.status === 'superseded' ? '已被同一会话的后续请求中断' : row.status === 'failed' ? 'Hermes 请求失败' : 'Hermes 正在处理' }}${{row.http_status ? ` • HTTP ${{esc(row.http_status)}}` : ''}}</div>
                    ${{row.error_detail ? `<div style="margin-top:8px; white-space:pre-wrap; word-break:break-word; color:var(--danger);">${{esc(row.error_detail)}}</div>` : ''}}
                  ` : `<div style="margin-top:8px; white-space:pre-wrap; word-break:break-word;">${{highlight(row.content)}}</div>`}}
                </div>
              `).join('') : '<div class="notice">没有可展示的请求或消息</div>';
              const hits = visibleItems.filter(isMatch).length;
              els.searchResults.textContent = state.keyword ? (hits ? `当前会话命中 ${{hits}} 条请求 / 消息` : '会话标题 / ID 匹配，或消息元信息匹配；时间线中没有正文命中') : '输入会话信息后按回车筛选';
              document.getElementById('prevMatchBtn').disabled = !hits;
              document.getElementById('nextMatchBtn').disabled = !hits;
              if (hits) navigateMatch();
            }}

            function showError(error) {{
              document.getElementById('footerNote').textContent = `加载失败：${{error.message || '未知错误'}}，请刷新重试`;
            }}
            async function loadDashboard(generation) {{
              const data = await api(adminPath('/dashboard?user_limit=1'));
              if (generation !== state.generation) return;
              const summary = data.summary || {{}};
              els.usersCount.textContent = summary.users ?? 0;
              els.sessionsCount.textContent = summary.conversations ?? 0;
              els.requestsCount.textContent = summary.requests ?? 0;
              els.messagesCount.textContent = summary.messages ?? 0;
            }}

            async function loadUserDetails() {{
              const userId = state.selectedUser;
              const generation = state.generation;
              renderSkills(null);
              if (!state.selectedUser) {{
                return;
              }}
              const [userData, capabilitiesData] = await Promise.all([
                api(adminPath(`/users/${{encodeURIComponent(state.selectedUser)}}`)),
                api(route('/admin/v1/capabilities')),
              ]);
              if (state.selectedUser !== userId || generation !== state.generation) return;
              const bindings = userData.skills || {{}};
              const skills = capabilitiesData.skills || [];
              const availableIds = new Set(skills.map((skill) => skill.id));
              renderSkills({{
                ...bindings,
                available_skills: skills,
                unavailable_skill_ids: (bindings.bound_skill_ids || []).filter((id) => !availableIds.has(id)),
              }});
            }}

            async function saveSkills() {{
              if (!state.selectedUser || state.skillsUser !== state.selectedUser) return;
              const userId = state.selectedUser;
              const skillIds = Array.from(els.skillsList.querySelectorAll('[data-skill]:checked'))
                .map((input) => input.getAttribute('data-skill'))
                .filter(Boolean);
              const previous = state.boundSkillIds || new Set();
              const selected = new Set(skillIds);
              const displayed = new Set(Array.from(els.skillsList.querySelectorAll('[data-skill]'))
                .map((input) => input.getAttribute('data-skill')));
              const add = skillIds.filter((id) => !previous.has(id));
              const remove = Array.from(previous).filter((id) => displayed.has(id) && !selected.has(id));
              els.saveSkillsBtn.disabled = true;
              try {{
                await api(adminPath(`/users/${{encodeURIComponent(userId)}}`), {{
                  method: 'PATCH',
                  headers: {{ 'x-csrf-token': window.__HERMES_ADMIN__.csrfToken }},
                  body: JSON.stringify({{ add, remove }}),
                }});
                await loadUserDetails();
              }} finally {{
                els.saveSkillsBtn.disabled = state.skillsUser !== state.selectedUser;
              }}
            }}

            async function loadConversation(generation) {{
              const sessionId = state.selectedSession;
              if (!sessionId) return;
              const timeline = await api(adminPath(`/conversations/${{encodeURIComponent(sessionId)}}/timeline`));
              if (generation !== state.generation || sessionId !== state.selectedSession) return;
              renderConversation(timeline.conversation);
              renderMessages(timeline.timeline || timeline.messages || []);
            }}
            function doSearch() {{
              state.keyword = els.searchInput.value.trim();
              state.userFilterText = els.userFilter.value.trim();
              state.scopeUser = ''; state.selectedSession = ''; state.offset = 0;
              document.getElementById('onlyMatches').checked = false;
              refreshAll();
            }}
            async function refreshAll() {{
              const generation = ++state.generation;
              const previousSession = state.selectedSession;
              state.selectedUser = ''; state.selectedSession = '';
              renderConversation(null); renderSkills(null);
              els.usersTable.innerHTML = '<tr><td colspan="3">正在加载…</td></tr>';
              els.sessionsTable.innerHTML = '<tr><td colspan="3">正在加载…</td></tr>';
              document.getElementById('footerNote').textContent = '正在加载…';
              const params = new URLSearchParams({{keyword: state.keyword, user_query: state.userFilterText, offset: state.offset}});
              if (state.scopeUser) params.set('user_id', state.scopeUser);
              try {{
                const [data] = await Promise.all([api(adminPath(`/browse?${{params}}`)), loadDashboard(generation)]);
                if (generation !== state.generation) return;
                state.users = data.users || []; state.conversations = data.conversations || [];
                state.scopeUser = data.scoped_user || '';
                const selected = state.conversations.find(row => row.external_session_id === previousSession) || state.conversations[0];
                state.selectedSession = selected?.external_session_id || '';
                state.selectedUser = selected?.user_id || state.scopeUser || state.users[0]?.user_id || '';
                renderUsers(state.users); renderConversations(state.conversations);
                els.sessionsMeta.textContent = `${{state.scopeUser ? '当前用户：' + state.scopeUser : '全部匹配用户'}} · ${{data.total_conversations}} 个会话`;
                document.getElementById('prevPageBtn').disabled = !state.offset;
                document.getElementById('nextPageBtn').disabled = !data.pagination?.has_more;
                document.getElementById('allUsersBtn').disabled = !state.scopeUser || !!state.userFilterText;
                document.getElementById('footerNote').textContent = `用户条件：${{state.userFilterText || '全部'}} · 会话条件：${{state.keyword || '全部'}} · ${{state.users.length}} 个用户 / ${{data.total_conversations}} 个会话${{state.keyword ? '（用户列表计数按匹配会话统计）' : ''}}`;
                await Promise.all([loadConversation(generation), loadUserDetails().catch(error => {{
                  if (generation === state.generation) els.skillsMeta.textContent = `Skills 加载失败：${{error.message}}`;
                }})]);
              }} catch (error) {{
                if (generation !== state.generation) return;
                state.selectedUser = ''; state.selectedSession = '';
                state.users = []; state.conversations = [];
                renderUsers([]); renderConversations([]); renderConversation(null); renderSkills(null);
                showError(error);
              }}
            }}
            els.refreshBtn.addEventListener('click', refreshAll);
            document.getElementById('resetBtn').addEventListener('click', () => {{
              els.userFilter.value = ''; els.searchInput.value = ''; doSearch();
            }});
            document.getElementById('clearUserBtn').addEventListener('click', () => {{ els.userFilter.value = ''; doSearch(); }});
            document.getElementById('clearSearchBtn').addEventListener('click', () => {{ els.searchInput.value = ''; doSearch(); }});
            document.getElementById('allUsersBtn').addEventListener('click', () => {{
              state.scopeUser = ''; state.offset = 0; state.selectedSession = ''; refreshAll();
            }});
            for (const [id, delta] of [['prevPageBtn', -100], ['nextPageBtn', 100]]) {{
              document.getElementById(id).addEventListener('click', () => {{
                state.offset = Math.max(0, state.offset + delta); state.selectedSession = ''; refreshAll();
              }});
            }}
            document.getElementById('prevMatchBtn').addEventListener('click', () => navigateMatch(-1));
            document.getElementById('nextMatchBtn').addEventListener('click', () => navigateMatch(1));
            document.getElementById('onlyMatches').addEventListener('change', () => {{ state.matchIndex = -1; renderMessages(state.timeline); }});

            els.saveSkillsBtn.addEventListener('click', saveSkills);
            els.logoutBtn.addEventListener('click', async () => {{
              // Do not follow the backend redirect here. Behind a subpath reverse
              // proxy, following a prefixed Location can add publicBasePath twice.
              // Derive the mount point from the URL currently serving this page.
              // This remains correct when a proxy has already rewritten the
              // configured publicBasePath in the rendered page.
              const currentPath = window.location.pathname || '';
              const adminIndex = currentPath.lastIndexOf('/admin');
              const mountPath = adminIndex >= 0 ? currentPath.slice(0, adminIndex) : '';
              await fetch(`${{mountPath}}/admin/logout`, {{ credentials: 'same-origin', redirect: 'manual' }});
              location.replace(`${{mountPath}}/admin/login`);
            }});
            els.searchInput.addEventListener('keydown', (event) => {{
              if (event.key === 'Enter') doSearch();
            }});
            els.userFilter.addEventListener('keydown', (event) => {{
              if (event.key === 'Enter') doSearch();
            }});

            refreshAll();
          </script>
        </body>
        </html>
        """
    )


def render_latency_page(*, username: str, public_base_path: str = "") -> str:
    payload = json.dumps({"username": username, "publicBasePath": public_base_path}, ensure_ascii=False)
    return textwrap.dedent(f"""\
    <!doctype html><html lang="zh-CN"><head><meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" /><title>网关对话数据统计</title>
    <style>
    :root{{--bg:#0f1115;--panel:#171b22;--line:#273041;--text:#e8edf4;--muted:#93a4bb;--accent:#78a6ff}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
    header{{padding:16px 20px;border-bottom:1px solid var(--line);background:#12161c}} .top{{max-width:1180px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:12px}}
    main{{max-width:1180px;margin:auto;padding:20px}} h1{{margin:0;font-size:19px}} h2{{font-size:15px;margin:0 0 12px}} .muted,.note{{color:var(--muted);font-size:12px}}
    a{{color:var(--text);border:1px solid var(--line);border-radius:6px;padding:8px 10px;text-decoration:none}} .grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:16px}}
    section,.card{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:14px}} .card .label{{color:var(--muted);font-size:12px}} .card .value{{font-size:22px;font-weight:700;margin-top:5px}}
    section{{margin-bottom:16px;overflow:auto}} table{{width:100%;border-collapse:collapse;white-space:nowrap}} th,td{{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line)}} th{{color:var(--muted);font-size:12px;font-weight:500}}
    .bar{{height:8px;background:#263044;border-radius:4px;min-width:80px}} .bar i{{display:block;height:100%;background:var(--accent);border-radius:4px}}
    @media(max-width:800px){{main{{padding:12px}}.grid{{grid-template-columns:repeat(2,1fr)}}}}
    </style></head><body><header><div class="top"><div><h1>网关对话数据统计</h1><div class="muted">管理员：{_html_escape(username)}</div></div><div><a href="{_html_escape(public_base_path)}/admin">管理面板</a> <a href="{_html_escape(public_base_path)}/admin/chat">在线对话</a></div></div></header>
    <main><div class="note" id="window">加载中…</div><div class="grid" id="cards"></div>
    <section><h2>最近四周趋势</h2><table><thead><tr><th>周期</th><th>完成数</th><th>平均</th><th>中位数</th><th>P90</th></tr></thead><tbody id="trends"></tbody></table></section>
    <section><h2>最近 7 天耗时分布</h2><table><thead><tr><th>区间</th><th>数量</th><th>占比</th><th>分布</th></tr></thead><tbody id="distribution"></tbody></table></section>
    <section><h2>匿名会话维度</h2><table><thead><tr><th>对象</th><th>完成数</th><th>平均</th><th>中位数</th><th>最长</th></tr></thead><tbody id="users"></tbody></table></section>
    <section><h2>按日汇总</h2><table><thead><tr><th>日期</th><th>完成数</th><th>平均</th><th>最长</th></tr></thead><tbody id="daily"></tbody></table></section>
    <section><h2>数据质量</h2><div class="note" id="quality"></div></section></main>
    <script>window.__LATENCY__={payload};const c=window.__LATENCY__,route=p=>`${{c.publicBasePath||''}}${{p}}`;const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
    const fmt=v=>v==null?'—':(v<1000?`${{Math.round(v)}}毫秒`:v<60000?`${{Math.round(v/1000)}}秒`:`${{Math.floor(v/60000)}}分${{Math.round(v%60000/1000)}}秒`);const pct=v=>`${{Number(v||0).toFixed(1)}}%`;
    function row(x){{return `<tr>${{x.map(v=>`<td>${{v}}</td>`).join('')}}</tr>`}} async function load(){{const r=await fetch(route('/admin/v1/latency-report'));if(!r.ok)throw Error('加载失败');const d=await r.json();const s=d.core||{{}};document.getElementById('window').textContent=`窗口：${{d.window.start}} 至 ${{d.window.end}}（${{d.window.timezone}}） · 口径：${{d.metric}}`;
    document.getElementById('cards').innerHTML=[['完成回复',s.count],['平均耗时',fmt(s.average_ms)],['中位数',fmt(s.median_ms)],['P90',fmt(s.p90_ms)],['P95',fmt(s.p95_ms)],['最长',fmt(s.max_ms)]].map(x=>`<div class="card"><div class="label">${{x[0]}}</div><div class="value">${{x[1]}}</div></div>`).join('');
    document.getElementById('trends').innerHTML=(d.trends||[]).map(x=>row([esc(x.period.start.slice(0,16))+' – '+esc(x.period.end.slice(0,16)),x.count,fmt(x.average_ms),fmt(x.median_ms),fmt(x.p90_ms)])).join('');
    document.getElementById('distribution').innerHTML=(d.distribution||[]).map(x=>row([esc(x.label),x.count,pct(x.percentage),`<div class="bar"><i style="width:${{Math.min(100,x.percentage)}}%"></i></div>`])).join('');
    const names='ABCDEFGHIJKLMN';document.getElementById('users').innerHTML=(d.users||[]).map((x,i)=>row([`用户${{names[i]||i+1}}`,x.count,fmt(x.average_ms),fmt(x.median_ms),fmt(x.max_ms)])).join('');
    document.getElementById('daily').innerHTML=(d.daily||[]).map(x=>row([esc(x.date),x.count,fmt(x.average_ms),fmt(x.max_ms)])).join('');const p=d.pairing||{{}};document.getElementById('quality').textContent=`完成记录：${{p.total||0}}；有 Hermes 阶段记录：${{p.matched||0}}；缺失：${{p.unmatched||0}}。API 调用总数：${{d.api_calls==null?'暂不可用':d.api_calls}}。${{d.api_calls==null?'当前数据源尚未持久化 response_ready / API 调用日志。':''}}`}}load().catch(e=>document.getElementById('quality').textContent=e.message);</script></body></html>""")
