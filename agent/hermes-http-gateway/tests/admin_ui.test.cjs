// Execute the actual rendered admin script against a small DOM/fetch adapter.
const assert = require('node:assert/strict');
const vm = require('node:vm');
let source = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => source += chunk);
process.stdin.on('end', async () => {
  const elements = new Map();
  class Element {
    constructor() { this.value = ''; this.checked = false; this.handlers = {}; this.style = {}; }
    addEventListener(name, callback) { this.handlers[name] = callback; }
    set innerHTML(value) { this.html = value; }
    get innerHTML() { return this.html || ''; }
    querySelectorAll(selector) {
      const attr = selector.match(/^\[([^\]]+)\]/)?.[1];
      if (!attr) return [];
      const rows = [...this.innerHTML.matchAll(/<(?:tr|div)\b([^>]+)>/g)]
        .filter(match => match[1].includes(attr + '='));
      return rows.map(match => {
        const node = new Element();
        node.getAttribute = name => match[1].match(new RegExp(name + '="([^"]*)"'))?.[1];
        return node;
      });
    }
    scrollIntoView() {}
    appendChild() {}
  }
  const document = {getElementById: id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  }};
  let users = [{user_id:'alice', conversation_count:1, message_count:2}, {user_id:'bob', conversation_count:1,message_count:1}];
  let sessions = [{external_session_id:'a',user_id:'alice',title:'A',match_count:2,message_count:2},
                  {external_session_id:'b',user_id:'bob',title:'B',match_count:1,message_count:1}];
  let empty = false, deferred = null;
  const calls = [];
  const fetch = async path => {
    calls.push(path);
    if (deferred && path.includes('/browse?')) return await deferred;
    const url = new URL(path, 'http://localhost');
    let data = {};
    if (url.pathname.endsWith('/browse')) {
      const scoped = url.searchParams.get('user_id') || (url.searchParams.get('user_query') ? 'alice' : '');
      data = {users: empty ? [] : users, conversations: empty ? [] : sessions.filter(c => !scoped || c.user_id === scoped), scoped_user: scoped,
        total_conversations:empty ? 0 : 2,pagination:{has_more:false}};
    } else if (path.includes('/timeline')) {
      const id = path.split('/conversations/')[1].split('/')[0];
      data = {conversation:sessions.find(c => c.external_session_id === id), timeline:[
        {event_type:'message',role:'assistant',content:'超时 <script>oops</script>'},
        {event_type:'message',role:'assistant',content:'other context'}]};
    } else if (path.includes('/capabilities')) return {status:503,ok:false,json:async()=>({detail:'catalog unavailable'})};
    else if (path.includes('/dashboard')) data = {summary:{users:2,conversations:2}};
    else data = {skills:{}};
    return {status:200,ok:true,json:async()=>data};
  };
  const context = vm.createContext({document,window:{location:{pathname:'/admin'}},location:{},fetch,URLSearchParams,Set,console});
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  const tick = () => new Promise(resolve => setImmediate(resolve));
  await tick();
  assert.equal(run('state.selectedSession'), 'a');
  assert.match(document.getElementById('detailMeta').textContent,/alice \/ a/);
  assert.match(document.getElementById('skillsMeta').textContent,/Skills 加载失败/);
  document.getElementById('searchInput').value = '超时';
  run('doSearch()'); await tick();
  assert.equal(run('state.conversations.length'),2);
  assert.match(document.getElementById('messagesList').innerHTML, /<mark>超时<\/mark>/);
  assert.doesNotMatch(document.getElementById('messagesList').innerHTML, /<script>/);
  document.getElementById('onlyMatches').checked = true;
  document.getElementById('onlyMatches').handlers.change();
  assert.doesNotMatch(document.getElementById('messagesList').innerHTML, /other context/);
  document.getElementById('userFilter').value = 'ali';
  run('doSearch()'); await tick();
  assert.equal(run('state.scopeUser'), 'alice');
  assert.equal(run('state.conversations.length'), 1);
  await run('refreshAll()');
  assert.equal(run('state.keyword'),'超时');
  assert.equal(run('state.selectedSession'),'a');
  // An older response must not overwrite the newer search or its details.
  let resolveOld;
  deferred = new Promise(resolve => resolveOld = resolve);
  const oldRequest = run('refreshAll()');
  deferred = null; empty = true;
  await run('refreshAll()');
  resolveOld({status:200,ok:true,json:async()=>({users,conversations:sessions,pagination:{}})});
  await oldRequest;
  assert.equal(run('state.selectedSession'),'');
  assert.equal(run('state.users.length'),0);
  assert.match(document.getElementById('conversationDetail').textContent,/没有选中/);
  assert.match(document.getElementById('messagesList').innerHTML,/没有会话消息/);
  assert.equal(document.getElementById('nextMatchBtn').disabled,true);
  console.log('Admin UI interaction checks passed');
}).on('error', error => { console.error(error); process.exitCode = 1; });
