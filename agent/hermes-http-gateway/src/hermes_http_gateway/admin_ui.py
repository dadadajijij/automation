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


def render_login_page(*, username_default: str, password_configured: bool) -> str:
    payload = json.dumps(
        {
            "usernameDefault": username_default,
            "passwordConfigured": password_configured,
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
                const resp = await fetch('/admin/login', {{
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
                location.href = data.redirect || '/admin';
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


def render_dashboard_page(*, username: str) -> str:
    payload = json.dumps({"username": username}, ensure_ascii=False)
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
            h1 {{ margin: 0; font-size: 18px; }}
            .toolbar {{
              margin-top: 12px;
              display: grid;
              grid-template-columns: 1.1fr .9fr auto auto auto;
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
            button {{
              cursor: pointer;
              background: #1c2330;
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
              grid-template-columns: 1fr 1.05fr 1.25fr;
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
            .foot {{ color: var(--muted); font-size: 12px; }}
            @media (max-width: 1200px) {{
              .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
              .layout {{ grid-template-columns: 1fr; }}
              .toolbar {{ grid-template-columns: 1fr; }}
            }}
          </style>
        </head>
        <body>
          <header>
            <div class="topbar">
              <h1>Hermes 管理面板</h1>
              <div class="pill ok" id="loginLabel">{_html_escape(username)}</div>
            </div>
            <div class="toolbar">
              <input id="searchInput" type="text" placeholder="搜索用户 / 会话 / 内容" autocomplete="off" />
              <input id="userFilter" type="text" placeholder="筛选用户" autocomplete="off" />
              <button class="primary" id="searchBtn">搜索</button>
              <button id="refreshBtn">刷新</button>
              <button id="logoutBtn">退出</button>
            </div>
          </header>

          <main>
            <div class="summary">
              <div class="stat"><div class="label">用户</div><div class="value" id="usersCount">-</div></div>
              <div class="stat"><div class="label">会话</div><div class="value" id="sessionsCount">-</div></div>
              <div class="stat"><div class="label">消息</div><div class="value" id="messagesCount">-</div></div>
              <div class="stat"><div class="label">当前管理员</div><div class="value" style="font-size:16px" id="currentAdmin">{_html_escape(username)}</div></div>
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
                </div>
              </section>

              <section>
                <div class="section-head">
                  <h2>会话</h2>
                  <span class="meta" id="sessionsMeta">全部</span>
                </div>
                <div class="section-body">
                  <table>
                    <thead>
                      <tr><th>会话</th><th>用户</th><th>状态</th><th>消息</th></tr>
                    </thead>
                    <tbody id="sessionsTable"></tbody>
                  </table>
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
                    <pre id="conversationDetail">请选择一个会话</pre>
                  </div>
                  <div class="detail-box">
                    <h3>消息</h3>
                    <div class="messages" id="messagesList">
                      <div class="notice">请选择一个会话查看消息</div>
                    </div>
                  </div>
                  <div class="detail-box">
                    <h3>搜索结果</h3>
                    <div class="messages" id="searchResults">
                      <div class="notice">输入关键词后点击搜索</div>
                    </div>
                  </div>
                </div>
              </section>
            </div>

            <div class="foot" id="footerNote">管理员登录后可查看所有用户、会话和消息。</div>
          </main>

          <script>
            window.__HERMES_ADMIN__ = {payload};
            const state = {{
              userFilterText: '',
              selectedUser: '',
              selectedSession: '',
            }};
            const els = {{
              searchInput: document.getElementById('searchInput'),
              userFilter: document.getElementById('userFilter'),
              searchBtn: document.getElementById('searchBtn'),
              refreshBtn: document.getElementById('refreshBtn'),
              logoutBtn: document.getElementById('logoutBtn'),
              usersCount: document.getElementById('usersCount'),
              sessionsCount: document.getElementById('sessionsCount'),
              messagesCount: document.getElementById('messagesCount'),
              usersMeta: document.getElementById('usersMeta'),
              sessionsMeta: document.getElementById('sessionsMeta'),
              detailMeta: document.getElementById('detailMeta'),
              usersTable: document.getElementById('usersTable'),
              sessionsTable: document.getElementById('sessionsTable'),
              conversationDetail: document.getElementById('conversationDetail'),
              messagesList: document.getElementById('messagesList'),
              searchResults: document.getElementById('searchResults'),
            }};

            function esc(v) {{
              return String(v ?? '')
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#39;');
            }}

            async function api(path, options = {{}}) {{
              const resp = await fetch(path, {{
                credentials: 'same-origin',
                headers: {{ 'content-type': 'application/json', ...(options.headers || {{}}) }},
                ...options,
              }});
              const data = await resp.json().catch(() => ({{}}));
              if (resp.status === 401) {{
                location.href = '/admin/login';
                return data;
              }}
              if (!resp.ok) {{
                throw new Error(data.detail || data.message || resp.statusText);
              }}
              return data;
            }}

            function renderUsers(items) {{
              els.usersMeta.textContent = state.userFilterText ? `筛选：${{state.userFilterText}}` : '全部';
              els.usersTable.innerHTML = items.length ? items.map((row) => `
                <tr class="clickable ${{row.user_id === state.selectedUser ? 'selected' : ''}}" data-user="${{esc(row.user_id)}}">
                  <td><strong>${{esc(row.user_id)}}</strong><div class="meta">${{esc(row.default_hermes_profile || '-')}}</div></td>
                  <td>${{row.conversation_count}}</td>
                  <td>${{row.message_count}}</td>
                </tr>
              `).join('') : '<tr><td colspan="3" class="meta">暂无用户</td></tr>';
              els.usersTable.querySelectorAll('[data-user]').forEach((row) => {{
                row.addEventListener('click', async () => {{
                  state.selectedUser = row.getAttribute('data-user') || '';
                  state.selectedSession = '';
                  renderConversation(null);
                  await loadUsers();
                  await loadConversations();
                }});
              }});
            }}

            function renderConversations(items) {{
              els.sessionsMeta.textContent = state.selectedUser ? `当前用户：${{state.selectedUser}}` : '全部';
              els.sessionsTable.innerHTML = items.length ? items.map((row) => `
                <tr class="clickable ${{row.external_session_id === state.selectedSession ? 'selected' : ''}}" data-session="${{esc(row.external_session_id)}}" data-user="${{esc(row.user_id)}}">
                  <td><strong>${{esc(row.title || row.external_session_id)}}</strong><div class="meta">${{esc(row.external_session_id)}}</div></td>
                  <td>${{esc(row.user_id)}}</td>
                  <td><span class="pill ${{row.status === 'error' ? 'bad' : 'ok'}}">${{esc(row.status)}}</span></td>
                  <td>${{row.message_count}}</td>
                </tr>
              `).join('') : '<tr><td colspan="4" class="meta">暂无会话</td></tr>';
              els.sessionsTable.querySelectorAll('[data-session]').forEach((row) => {{
                row.addEventListener('click', async () => {{
                  state.selectedSession = row.getAttribute('data-session') || '';
                  state.selectedUser = row.getAttribute('data-user') || state.selectedUser;
                  await loadUsers();
                  await loadConversations();
                  await loadConversation();
                }});
              }});
            }}

            function renderConversation(data) {{
              if (!data) {{
                els.detailMeta.textContent = '未选择';
                els.conversationDetail.textContent = '请选择一个会话';
                els.messagesList.innerHTML = '<div class="notice">请选择一个会话查看消息</div>';
                return;
              }}
              els.detailMeta.textContent = data.external_session_id;
              els.conversationDetail.textContent = JSON.stringify(data, null, 2);
            }}

            function renderMessages(items) {{
              els.messagesList.innerHTML = items.length ? items.map((row) => `
                <div class="msg">
                  <div class="msg-head">
                    <span><strong>${{esc(row.role)}}</strong> / ${{esc(row.kind)}}</span>
                    <span>${{esc(row.created_at)}}</span>
                  </div>
                  <div class="meta">${{esc(row.model || '-')}} • ${{esc(row.provider || '-')}}</div>
                  <div style="margin-top:8px; white-space:pre-wrap; word-break:break-word;">${{esc(row.content)}}</div>
                </div>
              `).join('') : '<div class="notice">这个会话没有消息</div>';
            }}

            function renderSearch(items) {{
              els.searchResults.innerHTML = items.length ? items.map((row) => `
                <div class="msg">
                  <div class="msg-head">
                    <span><strong>${{esc(row.conversation_title || row.external_session_id)}}</strong> • ${{esc(row.user_id)}}</span>
                    <span>${{esc(row.created_at)}}</span>
                  </div>
                  <div class="meta">${{esc(row.external_session_id)}} • ${{esc(row.role)}} • ${{esc(row.kind)}}</div>
                  <div style="margin-top:8px; white-space:pre-wrap; word-break:break-word;">${{esc(row.content)}}</div>
                </div>
              `).join('') : '<div class="notice">没有搜索结果</div>';
            }}

            async function loadSummary() {{
              const data = await api('/admin/api/summary');
              els.usersCount.textContent = data.users;
              els.sessionsCount.textContent = data.conversations;
              els.messagesCount.textContent = data.messages;
            }}

            async function loadUsers() {{
              const q = state.userFilterText.trim();
              const path = q ? `/admin/api/users?query=${{encodeURIComponent(q)}}` : '/admin/api/users';
              const data = await api(path);
              renderUsers(data.users || []);
            }}

            async function loadConversations() {{
              const params = new URLSearchParams();
              const q = els.searchInput.value.trim();
              if (q && q.length < 2) {{
                // leave it alone; the search box is for global search only
              }}
              if (state.selectedUser) params.set('user_id', state.selectedUser);
              const path = params.toString() ? `/admin/api/conversations?${{params.toString()}}` : '/admin/api/conversations';
              const data = await api(path);
              renderConversations(data.conversations || []);
            }}

            async function loadConversation() {{
              if (!state.selectedSession) {{
                renderConversation(null);
                return;
              }}
              const detail = await api(`/admin/api/conversations/${{encodeURIComponent(state.selectedSession)}}`);
              renderConversation(detail.conversation);
              const messages = await api(`/admin/api/conversations/${{encodeURIComponent(state.selectedSession)}}/messages`);
              renderMessages(messages.messages || []);
            }}

            async function doSearch() {{
              const q = els.searchInput.value.trim();
              if (!q) {{
                renderSearch([]);
                return;
              }}
              const data = await api(`/admin/api/search?q=${{encodeURIComponent(q)}}`);
              renderSearch(data.results || []);
            }}

            async function refreshAll() {{
              state.userFilterText = '';
              state.selectedUser = '';
              state.selectedSession = '';
              els.userFilter.value = '';
              await loadSummary();
              await loadUsers();
              await loadConversations();
              renderConversation(null);
            }}

            els.searchBtn.addEventListener('click', doSearch);
            els.refreshBtn.addEventListener('click', refreshAll);
            els.logoutBtn.addEventListener('click', async () => {{
              await fetch('/admin/logout', {{ credentials: 'same-origin' }});
              location.href = '/admin/login';
            }});
            els.searchInput.addEventListener('keydown', (event) => {{
              if (event.key === 'Enter') doSearch();
            }});
            els.userFilter.addEventListener('keydown', (event) => {{
              if (event.key === 'Enter') {{
                state.userFilterText = els.userFilter.value.trim();
                loadUsers();
              }}
            }});
            els.userFilter.addEventListener('input', () => {{
              state.userFilterText = els.userFilter.value.trim();
            }});

            refreshAll();
          </script>
        </body>
        </html>
        """
    )
