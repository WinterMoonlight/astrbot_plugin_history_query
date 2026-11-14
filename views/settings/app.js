import {clone, escapeHTML as esc, splitNames, parseSource, makeSource, entry, setPath, instanceReferences, validate} from './model.js';

const $ = selector => document.querySelector(selector);
const sections = {
  overview:['使用概况','先选机器人和可查询的人，再开启查询。'],
  instances:['连接机器人','选择已在 AstrBot 接入的机器人，并核对它的 QQ 号。'],
  readers:['谁能查询','给每个人单独设置：可以查哪些群、哪些人的私聊。'],
  aliases:['常用名称','给群聊或私聊起个好记的名字，聊天时就能这样称呼它。'],
  limits:['查询设置','决定一次翻多少条、最多花多长时间。拿不准时保留默认值即可。'],
};
const state = {snapshot:null,draft:null,tab:'overview',reader:0,busy:false,groups:{},validationIssue:false,
  groupPlatform:'',privatePlatform:'',groupPeer:'',privatePeer:''};
let bridge = null;
let toastTimer = null;
let confirmation = null;
let helpTarget = null;
let helpTimer = null;
const dirty = () => Boolean(state.snapshot && JSON.stringify(state.draft)!==JSON.stringify(state.snapshot.config));

function helpButton(label, text) {
  return `<button type="button" class="help-button" aria-label="${esc(label)}：查看说明" data-help="${esc(text)}">?</button>`;
}
function hideHelp() {
  clearTimeout(helpTimer);
  helpTarget?.removeAttribute('aria-describedby');
  helpTarget=null;$('#help-tooltip').hidden=true;
}
function showHelp(button) {
  hideHelp();helpTarget=button;
  const box=$('#help-tooltip');box.textContent=button.dataset.help;box.hidden=false;
  button.setAttribute('aria-describedby','help-tooltip');
  const viewportWidth=document.documentElement.clientWidth;
  const viewportHeight=document.documentElement.clientHeight;
  box.style.maxWidth=`${Math.min(330,viewportWidth-24)}px`;
  const rect=button.getBoundingClientRect();
  const width=box.offsetWidth;const height=box.offsetHeight;
  box.style.left=`${Math.max(12,Math.min(rect.left,viewportWidth-width-12))}px`;
  const top=rect.bottom+9+height<=viewportHeight-12?rect.bottom+9:rect.top-height-9;
  box.style.top=`${Math.max(12,Math.min(top,viewportHeight-height-12))}px`;
}
// One shared tooltip: works with mouse, keyboard and touch without per-field listeners.
document.addEventListener('pointerover',event=>{
  const button=event.target.closest('.help-button');
  if(button)showHelp(button);
  else if(event.target.closest('#help-tooltip'))clearTimeout(helpTimer);
});
document.addEventListener('pointerout',event=>{
  if(event.target.closest('.help-button, #help-tooltip')) {
    const next=event.relatedTarget;
    if(next instanceof Element && (next.closest('#help-tooltip')||next.closest('.help-button')===helpTarget))return;
    if(document.activeElement===helpTarget)return;
    helpTimer=setTimeout(hideHelp,140);
  }
});
document.addEventListener('focusin',event=>{if(event.target.matches('.help-button'))showHelp(event.target);});
document.addEventListener('focusout',event=>{if(event.target===helpTarget)hideHelp();});
document.addEventListener('click',event=>{
  const button=event.target.closest('.help-button');
  if(button)showHelp(button);
  else if(!event.target.closest('#help-tooltip'))hideHelp();
});
document.addEventListener('keydown',event=>{if(event.key==='Escape')hideHelp();});
window.addEventListener('resize',hideHelp);
document.addEventListener('scroll',event=>{if(event.target!==$('#help-tooltip'))hideHelp();},true);

function error(message='') {
  const box=$('#load-error');box.textContent=message;box.hidden=!message;
  if(message){box.tabIndex=-1;box.focus({preventScroll:true});box.scrollIntoView({block:'nearest'});}
}
function toast(message, failure=false) {
  clearTimeout(toastTimer);const box=$('#toast');box.textContent=message;box.classList.toggle('error',failure);box.hidden=false;
  toastTimer=setTimeout(()=>{box.hidden=true;},failure?6500:3800);
}
function confirmChange(title, message) {
  hideHelp();
  return new Promise(resolve=>{
    if (confirmation) confirmation(false);
    confirmation=resolve;$('#confirm-title').textContent=title;$('#confirm-text').textContent=message;$('#confirm-dialog').showModal();
  });
}
function finishConfirm(value) { $('#confirm-dialog').close();const fn=confirmation;confirmation=null;if(fn)fn(value); }
$('#confirm-ok').addEventListener('click',()=>finishConfirm(true));
$('#confirm-cancel').addEventListener('click',()=>finishConfirm(false));
$('#confirm-dialog').addEventListener('cancel',event=>{event.preventDefault();finishConfirm(false);});

function markDirty() {
  const changed=dirty();$('#dirty-badge').textContent=changed?'未保存':'已保存';$('#dirty-badge').classList.toggle('unsaved',changed);
  $('#save-button').disabled=state.busy||!changed;$('#discard-button').disabled=state.busy||!changed;
  $('#save-button').textContent=state.busy?'处理中…':'保存设置';
  $('#refresh-button').disabled=state.busy;
  $('#save-hint').textContent=!state.snapshot?'设置尚未加载':changed?'有修改，保存后生效':'已保存，可以回到聊天中使用';
  for(const key of ['instances','readers','aliases']) document.querySelector(`[data-count="${key}"]`).textContent=state.draft?.[key]?.length||0;
  document.querySelectorAll('[data-action="select-reader"]').forEach(button=>{
    const reader=state.draft.readers[Number(button.dataset.index)];
    if(reader){button.querySelector('strong').textContent=reader.user_id||'未填写 QQ 号';button.querySelector('small').textContent=reader.platform_id||'未选择机器人';}
  });
}
function busy(value) {
  state.busy=value;hideHelp();
  $('#editor-content').querySelectorAll('input,select,textarea,button').forEach(node=>{node.disabled=value;});
  $('#navigation').querySelectorAll('button').forEach(node=>{node.disabled=value;});
  markDirty();
}
function options(selected, blank='选择机器人') {
  return `<option value="">${esc(blank)}</option>`+state.draft.instances.map(instance=>`<option value="${esc(instance.platform_id)}" ${instance.platform_id===selected?'selected':''}>${esc(instance.platform_id||'未填写连接名称')}${instance.bot_id?` · QQ ${esc(instance.bot_id)}`:''}</option>`).join('');
}
function field(label,path,value,help='',attributes='') {
  const id='field-'+path.replaceAll('.','-');
  return `<div class="field"><div class="field-label"><label for="${esc(id)}">${esc(label)}</label>${help?helpButton(label,help):''}</div><input id="${esc(id)}" data-path="${esc(path)}" value="${esc(value)}" ${attributes}></div>`;
}
function selectField(label,path,value,help='') {
  const id='field-'+path.replaceAll('.','-');
  return `<div class="field"><div class="field-label"><label for="${esc(id)}">${esc(label)}</label>${help?helpButton(label,help):''}</div><select id="${esc(id)}" data-path="${esc(path)}">${options(value)}</select></div>`;
}
function switchRow(title,path,value,description,help='') {
  return `<div class="switch-row"><div class="switch-copy"><div class="field-label"><h3>${esc(title)}</h3>${help?helpButton(title,help):''}</div><p>${esc(description)}</p></div><label class="switch"><input type="checkbox" aria-label="${esc(title)}" data-path="${esc(path)}" ${value?'checked':''}><span class="switch-track"></span></label></div>`;
}
function empty(title,description,action,label) {
  return `<div class="empty-state"><h3>${esc(title)}</h3><p>${esc(description)}</p><button type="button" class="secondary" data-action="${action}">＋ ${esc(label)}</button></div>`;
}

function overview() {
  const d=state.draft;const s=state.snapshot.status;
  const steps=[['连接机器人',d.instances.length>0,'选择连接，核对 QQ 号。','instances'],
    ['决定谁能查',d.readers.length>0,'添加 QQ 号，再选可查的聊天。','readers'],
    ['起个常用名称',d.aliases.length>0,'可选，比如“英语群”或“小王”。','aliases']];
  return `<div class="stats">${[['instances','已配置的机器人','个'],['readers','可查询的人','人'],['aliases','常用名称','项']].map(([key,label,unit])=>`<div class="stat"><div class="stat-label">${label}</div><div class="stat-value">${d[key].length}<small>${unit}</small></div></div>`).join('')}</div>
    <div class="section">${switchRow('开启聊天记录查询','enabled',d.enabled,'只对下方设置好的人开放，在私聊机器人时使用。','打开后，机器人会根据聊天内容判断是否需要查历史。没有添加的人仍能正常聊天，但不能查询其他聊天的记录。')}
    <div class="runtime-line"><span>AstrBot <strong>${esc(s.astrbot_version)}</strong></span><span>正在查询 <strong>${Number(s.active_queries)||0}</strong></span><span>等待查询 <strong>${Number(s.queued_queries)||0}</strong></span><span>状态按刷新时显示</span></div></div>
    <div class="overview-columns"><div class="section"><div class="section-title"><h3>简单三步，准备好就能查</h3></div>
    <div class="steps">${steps.map(([title,done,description,tab],i)=>`<div class="step"><span class="step-check ${done?'done':''}">${done?'✓':i+1}</span><div class="step-copy"><strong>${title}</strong><p>${description}</p></div><button type="button" class="small quiet" data-action="navigate" data-tab="${tab}">${done?'修改':'设置'} →</button></div>`).join('')}</div></div>
    <div class="section"><div class="section-title"><h3>像平常一样问它</h3><p>保存设置后，直接私聊机器人。</p></div>
    <div class="example">英语群昨天布置了什么作业？</div><div class="example">帮我看看和小王最近聊了什么。</div>
    <p class="help">只能查到机器人已保存的记录。私聊缺少可靠时间时，会按“私聊回看条数”往前翻。</p></div></div>`;
}

function instances() {
  const values=state.draft.instances;
  const list=(state.snapshot.available_instances||[]).map(item=>`<option value="${esc(item.id)}">QQ 机器人连接</option>`).join('');
  return `<div class="section"><datalist id="known-instances">${list}</datalist><div class="toolbar"><div><h3>选择要用的机器人</h3><p>这里只添加已接入 AstrBot 的机器人。</p></div><button type="button" class="secondary" data-action="add-instance">＋ 添加机器人</button></div>
    ${values.length?values.map((item,i)=>`<article class="form-card"><div class="card-top"><h3><span class="serial">${String(i+1).padStart(2,'0')}</span>机器人连接</h3><button type="button" class="small quiet danger" data-action="remove-instance" data-index="${i}">移除</button></div><div class="grid">
      ${field('AstrBot 中的连接名称',`instances.${i}.platform_id`,item.platform_id,'在 AstrBot 的“机器人”页面找到这个连接的 ID，原样填入，也可从候选列表选择。它不是 QQ 号，也不是 aiocqhttp 这样的类型名称。更换登录 QQ 时建议新建连接，避免混用旧记录。','list="known-instances" placeholder="选择已有连接，如 qq-main" maxlength="128"')}
      <div class="field"><div class="field-label"><label for="bot-${i}">机器人 QQ 号</label>${helpButton('机器人 QQ 号','填写机器人实际登录的 QQ。点击“自动识别”可获取当前登录账号，不会发送消息。')}</div><div class="inline-controls"><input id="bot-${i}" data-path="instances.${i}.bot_id" value="${esc(item.bot_id)}" inputmode="numeric" placeholder="机器人登录的 QQ"><button type="button" class="small" data-action="identify" data-index="${i}">自动识别</button></div></div></div></article>`).join(''):empty('先添加你的机器人','选择 AstrBot 已有的连接，然后核对机器人 QQ 号。','add-instance','添加机器人')}
    </div>`;
}

function sourceName(source) {
  const p=parseSource(source);
  const group=(state.groups[p.platform]||[]).find(g=>g.id===p.peer);
  const alias=state.draft.aliases.find(a=>a.source===source)?.names?.[0];
  return `${group?.name||alias||(p.kind==='GroupMessage'?'群 '+p.peer:'私聊 '+p.peer)} · ${p.platform}`;
}
function sourceSection(reader,index,kind) {
  const group=kind==='group';const key=group?'groups':'private_sessions';const prefix=group?'group':'private';
  const title=group?'可以查哪些群':'可以查谁的私聊';const selected=state[`${prefix}Platform`];
  const current=state.draft.instances.some(item=>item.platform_id===selected)?selected:state.draft.instances[0]?.platform_id||'';
  state[`${prefix}Platform`]=current;
  const catalog=state.groups[current]||[];
  return `<div class="subsection"><div class="source-heading"><div class="field-label"><h3>${title}</h3>${helpButton(title,group?'添加后，这个人才能查指定群的已保存记录。机器人已退群，或暂时无法确认还在群里时，会暂停该群的查询。':'添加对方 QQ 后，这个人就能查对方与机器人的已保存 AI 对话，包括机器人的回复。不是 QQ 客户端里的完整聊天记录，也不保证每条都有发送时间。')}</div>
    ${group?'<button type="button" class="small quiet" data-action="load-groups">获取群列表</button>':''}</div>
    <div class="source-builder"><label class="field"><span>在哪个机器人上</span><select data-ui="${prefix}Platform">${options(current)}</select></label>
    <label class="field"><span>${group?'群号':'对方的 QQ 号'}</span><input data-ui="${prefix}Peer" value="${esc(state[`${prefix}Peer`])}" inputmode="numeric" placeholder="${group?'输入群号，或从列表选择':'填写允许被查看的 QQ'}" ${group?'list="group-options"':''}></label>
    <button type="button" class="secondary" data-action="add-source" data-kind="${kind}" data-index="${index}">添加</button></div>
    ${group?`<datalist id="group-options">${catalog.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('')}</datalist>`:''}
    ${reader[key].length?`<div class="chips">${reader[key].map((source,j)=>`<div class="chip" title="${esc(source)}"><span>${esc(sourceName(source))}</span><button type="button" aria-label="移除${esc(sourceName(source))}" data-action="remove-source" data-index="${index}" data-key="${key}" data-item="${j}">×</button></div>`).join('')}</div>`:`<p class="empty-chips">还没有单独添加${group?'群聊':'私聊'}。</p>`}</div>`;
}
function allScopes(reader,index) {
  return `<div class="subsection"><div class="field-label"><h3>也可以开放全部聊天</h3>${helpButton('开放全部聊天','勾选后，这个人可以查所选机器人上的全部群聊或全部私聊，后续新增的也包含在内。仍只读取已保存且当前允许访问的记录。')}</div><p class="help">勾选后不用逐个添加，之后新增的聊天也会包含在内。</p>
    <div class="grid">${[['all_groups_on','全部群聊'],['all_private_on','全部私聊']].map(([key,label])=>`<div><h3>${label}</h3><div class="scope-checks">${state.draft.instances.map(instance=>`<label class="scope-check"><input type="checkbox" aria-label="${esc(instance.platform_id)}：${label}" data-array-path="readers.${index}.${key}" data-member="${esc(instance.platform_id)}" ${reader[key].includes(instance.platform_id)?'checked':''}><span>${esc(instance.platform_id)}</span></label>`).join('')}</div></div>`).join('')}</div>
    ${reader.all_private_on.length?'<p class="inline-note">已开放全部私聊：这个人也能查看其他人与机器人的已保存对话。</p>':''}</div>`;
}
function readers() {
  if (!state.draft.instances.length) return `<div class="section">${empty('先连接一个机器人','添加机器人后，就能选择谁可以查它保存的记录。','goto-instances','去连接机器人')}</div>`;
  const values=state.draft.readers;
  if (!values.length) return `<div class="section">${empty('还没有人可以查询','先添加一个人的 QQ 号，再为他选择可以查的群和私聊。','add-reader','添加可查询的人')}</div>`;
  state.reader=Math.min(state.reader,values.length-1);const index=state.reader;const reader=values[index];
  return `<div class="section"><div class="toolbar"><div><h3>可查询的人</h3><p>仅允许这些人在私聊机器人时查询。</p></div><button type="button" class="secondary" data-action="add-reader">＋ 添加一个人</button></div>
    <div class="reader-workspace"><div class="reader-list" aria-label="可查询的人">${values.map((item,i)=>`<button type="button" class="reader-choice ${i===index?'selected':''}" data-action="select-reader" data-index="${i}" aria-pressed="${i===index}"><strong>${esc(item.user_id||'未填写 QQ 号')}</strong><small>${esc(item.platform_id||'未选择机器人')}</small></button>`).join('')}</div>
    <div class="reader-detail"><div class="card-top"><h3>这个人是谁</h3><button type="button" class="small quiet danger" data-action="remove-reader" data-index="${index}">移除这个人</button></div>
    <div class="grid">${selectField('他向哪个机器人提问',`readers.${index}.platform_id`,reader.platform_id,'这个人必须私聊此处选中的机器人，才能使用下面设置的查询范围。在其他机器人上提问，需要另行添加。')}${field('他的 QQ 号',`readers.${index}.user_id`,reader.user_id,'按实际发消息的 QQ 号识别人，不看昵称，也不相信聊天中自称的身份。这里只开放查询功能，不会把他设为 AstrBot 管理员。','inputmode="numeric" placeholder="填写可以发起查询的 QQ"')}</div>
    ${sourceSection(reader,index,'group')}${sourceSection(reader,index,'private')}${allScopes(reader,index)}</div></div></div>`;
}

function aliases() {
  if (!state.draft.instances.length) return `<div class="section">${empty('先连接一个机器人','添加机器人后，就能为它的群聊或私聊起名字。','goto-instances','去连接机器人')}</div>`;
  return `<div class="section"><div class="toolbar"><div><h3>换个好记的称呼</h3><p>本来就能按群名找到的群，不用重复添加。</p></div><button type="button" class="secondary" data-action="add-alias">＋ 添加名称</button></div>
  ${state.draft.aliases.length?state.draft.aliases.map((item,i)=>{const parsed=parseSource(item.source);return `<article class="form-card" data-alias-index="${i}"><div class="card-top"><h3><span class="serial">${String(i+1).padStart(2,'0')}</span>常用名称</h3><button type="button" class="small quiet danger" data-action="remove-alias" data-index="${i}">移除</button></div>
    <div class="alias-source"><label class="field"><span>在哪个机器人上</span><select data-alias-part="platform">${options(parsed.platform||state.draft.instances[0].platform_id)}</select></label>
    <label class="field"><span>给哪种聊天起名</span><select data-alias-part="kind"><option value="GroupMessage" ${parsed.kind==='GroupMessage'?'selected':''}>群聊</option><option value="FriendMessage" ${parsed.kind==='FriendMessage'?'selected':''}>私聊</option></select></label>
    <label class="field"><span>群号 / 对方的 QQ 号</span><input data-alias-part="peer" value="${esc(parsed.peer)}" inputmode="numeric" placeholder="填写群号或 QQ 号"></label></div>
    <div class="field"><div class="field-label"><label for="alias-${i}">平时怎么称呼它</label>${helpButton('常用名称','每行一个名称，也可以用逗号分隔。多个聊天重名时，机器人会先问你指哪个。起名不会自动开放查询，仍需在“谁能查询”里设置范围。')}</div><textarea id="alias-${i}" data-path="aliases.${i}.names" data-value-type="list" placeholder="例如：英语群&#10;大学英语班">${esc(item.names.join('\n'))}</textarea></div></article>`;}).join(''):empty('用你熟悉的叫法','比如把一个群叫作“英语群”，把某个人的私聊叫作“小王”。','add-alias','添加名称')}</div>`;
}

function limits() {
  const values=state.draft.limits;
  const fields={
    private_recent_scan:['私聊回看条数','每个私聊一次最多检查多少条已保存的对话，默认 50 条。按保存顺序往前翻，不代表这些消息都在指定日期内。重复读取和补读前后文也占条数，并且受“一次最多读多少条”等总限制约束。',1],
    scan:['一次最多读多少条','你发一条消息后，机器人为回答这条消息而进行的所有历史查询，共享这个上限。多个群、多个私聊、重复读取和补读前后文都一起计数。',1],
    execution_seconds:['查找时间上限（秒）','读取和筛选记录累计最多花多久，默认 5 秒。达到上限就停止继续查找，并返回已查到的部分。收尾可能稍多花一点时间；不包括排队时间和模型写回答的时间。',0.1],
    return_chars:['给模型的内容上限（字符）','一次提问中，所有查询结果发给模型的文字总量上限。过长会截短并说明，所以实际返回的条数可能少于读取条数。不是最终回答的字数限制。',1],
    page_size:['每批读取条数','默认每批 25 条。查询多个聊天时轮流分批读取，避免一个聊天先占完所有额度。',1],
    queue_seconds:['最多排队多久（秒）','一次只执行一个查询。其他查询正在执行时，本次提问累计最多等待多久，默认 5 秒。超时提示繁忙；与查找时间上限分开计算。',0.1],
    queue_capacity:['最多排队几个请求','不包含正在执行的查询。默认最多允许 4 个请求排队，满了就提示稍后重试。',1],
    blob_bytes:['单份记录大小上限（字节）','防止一次读取过大的数据而占用太多内存。私聊的一份记录可能包含一整段 AI 对话。超过上限会跳过或拒绝读取并说明；默认 1048576 字节，约 1 MB。',1],
    idle_seconds:['闲置多久释放内存（秒）','查询使用一个按需启动的工作进程，默认闲置 30 秒后关闭。下次查询会自动启动，不影响正常聊天。这里只释放查询占用的内存，不会删除聊天记录。',1],
  };
  const input=key=>{const [label,help,step]=fields[key];const bounds=state.snapshot.limits[key];return field(label,`limits.${key}`,values[key],`${help}\n可填范围：${bounds[0]}–${bounds[1]}${step===1?'，请填整数':''}。`,`type="number" min="${bounds[0]}" max="${bounds[1]}" step="${step}"`);};
  return `<div class="section"><div class="toolbar"><div><h3>常用设置</h3><p>每次提问，所有查询共用一份总限额。</p></div><button type="button" class="small" data-action="reset-limits">恢复默认值</button></div>
    <div class="grid">${['private_recent_scan','scan','execution_seconds','return_chars'].map(input).join('')}</div>
    <div class="limit-note">条数是查找上限，不是保证返回的数量。${helpButton('查找上限','例如私聊回看设为 50，而一次总共最多读 30 条，本次最多只能读 30 条。时间或返回内容先用完时，也会提前停止。')}</div>
    <details class="advanced"><summary>更多性能设置 <span class="muted">通常不用修改</span></summary><div class="grid">${['page_size','queue_seconds','queue_capacity','blob_bytes','idle_seconds'].map(input).join('')}</div></details></div>
    <div class="section">${switchRow('记录查询日志','audit_enabled',state.draft.audit_enabled,'记录谁查了哪些聊天，方便之后核对。','只记录查询者、聊天标识、时间、状态和数量，不记录聊天正文、关键词或完整问题。保留当前与上一份日志，每份约 1 MB。关闭后不再写新日志，但不会删除已有日志，也不会改变 AstrBot 自己的对话保存设置。')}</div>`;
}

function render() {
  if (!state.draft) return;
  hideHelp();
  const [title,description]=sections[state.tab];$('#section-title').textContent=title;$('#section-description').textContent=description;
  $('#navigation').querySelectorAll('[data-tab]').forEach(button=>{const selected=button.dataset.tab===state.tab;button.classList.toggle('is-active',selected);button.setAttribute('aria-pressed',String(selected));});
  const status=state.snapshot.status;const enabled=state.snapshot.config.enabled;
  const available=status.version_supported&&!status.runtime_error;
  $('#live-status').textContent=!available?'查询暂不可用':enabled?'查询已开启':'查询未开启';$('#live-status').classList.toggle('enabled',enabled&&available);
  const notice=$('#notice');notice.hidden=available;
  const text=!status.version_supported?`当前 AstrBot ${status.astrbot_version} 还未通过兼容检查，暂时不能查询。你仍可编辑设置。`:status.runtime_error?`查询暂不可用：${status.runtime_error}。可以先保存设置，再检查 AstrBot 日志或重新加载插件。`:'';
  notice.querySelector('p').textContent=text;
  $('#editor-content').innerHTML=({overview,instances,readers,aliases,limits})[state.tab]();
  markDirty();
}

async function load(force=false) {
  if(state.busy)return;
  if(dirty()&&!force&&!await confirmChange('刷新并放弃修改？','刷新会读取上次保存的设置，你刚才没保存的修改会丢失。'))return;
  busy(true);
  try {
    if(!bridge)throw new Error('请从 AstrBot 插件的“视图”入口打开本页。');
    const value=await bridge.apiGet('admin/state');
    state.snapshot=value;state.draft=clone(value.config);state.groups={};error();
    render();
  } catch(e) {error(e.message||'加载失败，请稍后重试。');}
  finally{busy(false);}
}

async function save() {
  if(state.busy||!dirty())return;
  const issues=validate(state.draft,state.snapshot.limits);
  if(issues.length){state.validationIssue=true;error(issues.slice(0,8).join('\n'));toast('有几项没填对，请查看页面上方的提示。',true);return;}
  busy(true);error();
  try{
    const value=await bridge.apiPost('admin/save',{revision:state.snapshot.revision,config:clone(state.draft)});
    const oldBindings=new Map(state.snapshot.config.instances.map(item=>[item.platform_id,item.bot_id]));
    state.groups=Object.fromEntries(Object.entries(state.groups).filter(([id])=>value.config.instances.some(item=>item.platform_id===id&&item.bot_id===oldBindings.get(id))));
    state.snapshot=value;state.draft=clone(value.config);render();
    if(value.warning)error(value.warning);
    toast(value.saved===false?'设置没有变化。':'已保存，下次查询按新设置执行。');
  }catch(e){error(e.message||'保存失败，草稿仍保留。');toast('保存未完成，当前草稿仍保留。',true);}
  finally{busy(false);}
}

document.addEventListener('input',event=>{
  const node=event.target;if(!state.draft||state.busy)return;
  if(state.validationIssue){state.validationIssue=false;error();}
  if(node.dataset.path){let value=node.type==='checkbox'?node.checked:node.value;if(node.type==='number')value=node.value===''?null:Number(node.value);if(node.dataset.valueType==='list')value=splitNames(node.value);setPath(state.draft,node.dataset.path,value);markDirty();}
  if(node.dataset.ui){state[node.dataset.ui]=node.value;if(node.dataset.ui==='groupPlatform'){state.groupPeer='';render();}}
  if(node.dataset.arrayPath){const parts=node.dataset.arrayPath.split('.');const reader=state.draft.readers[Number(parts[1])];const key=parts[2];reader[key]=node.checked?[...new Set([...reader[key],node.dataset.member])]:reader[key].filter(x=>x!==node.dataset.member);render();}
  if(node.dataset.aliasPart){const card=node.closest('[data-alias-index]');const index=Number(card.dataset.aliasIndex);const values={};card.querySelectorAll('[data-alias-part]').forEach(input=>values[input.dataset.aliasPart]=input.value);state.draft.aliases[index].source=`${values.platform}:${values.kind}:${values.peer.trim()}`;markDirty();}
});

document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button||state.busy||!state.draft)return;
  const action=button.dataset.action;const index=Number(button.dataset.index);
  try{
    if(button.closest('#navigation')||action==='navigate'){state.tab=button.dataset.tab;render();return;}
    if(action==='goto-instances'){state.tab='instances';render();return;}
    if(action==='add-instance'){state.draft.instances.push(entry('instances'));render();return;}
    if(action==='remove-instance'){
      const item=state.draft.instances[index];const count=item.platform_id?instanceReferences(state.draft,item.platform_id):0;
      if(count){toast(`还有 ${count} 处查询范围或常用名称使用这个机器人，请先到对应页面移除。`,true);return;}
      if(await confirmChange('移除这个机器人？','保存后，这个机器人将从查询设置中移除。已有聊天记录不会被删除。')){state.draft.instances.splice(index,1);render();}return;
    }
    if(action==='identify'){
      const original=state.draft.instances[index];const instance=original?.platform_id.trim();const previousBot=original?.bot_id;if(!instance)throw new Error('请先填写 AstrBot 中的连接名称。');
      button.disabled=true;button.textContent='识别中…';
      try{const result=await bridge.apiGet('admin/identify',{platform_id:instance});if(state.draft.instances[index]===original&&original.platform_id.trim()===instance&&original.bot_id===previousBot){original.bot_id=result.bot_id;render();toast('已填入机器人 QQ，保存后生效。');}else{toast('识别期间条目已更改，已保留你的输入。');}}
      finally{button.disabled=false;button.textContent='自动识别';}return;
    }
    if(action==='add-reader'){state.draft.readers.push(entry('readers',state.draft.instances[0]?.platform_id||''));state.reader=state.draft.readers.length-1;render();return;}
    if(action==='select-reader'){state.reader=index;state.groupPeer='';state.privatePeer='';render();return;}
    if(action==='remove-reader'){
      if(await confirmChange('不再允许这个人查询？','保存后，这个人将不能再通过此处设置的机器人查询记录。已有聊天记录不会被删除。')){state.draft.readers.splice(index,1);state.reader=0;render();}return;
    }
    if(action==='load-groups'){
      const instance=state.groupPlatform;if(!instance)throw new Error('请先选择机器人。');
      button.disabled=true;button.textContent='读取中…';
      try{const result=await bridge.apiGet('admin/groups',{platform_id:instance});state.groups[instance]=result.groups;render();toast(`找到 ${result.groups.length} 个群，可在群号输入框中选择。`);}
      finally{button.disabled=false;button.textContent='获取群列表';}return;
    }
    if(action==='add-source'){
      const group=button.dataset.kind==='group';const prefix=group?'group':'private';const source=makeSource(state[`${prefix}Platform`],group?'GroupMessage':'FriendMessage',state[`${prefix}Peer`].trim());
      const key=group?'groups':'private_sessions';const values=state.draft.readers[index][key];if(!values.includes(source))values.push(source);state[`${prefix}Peer`]='';render();return;
    }
    if(action==='remove-source'){state.draft.readers[index][button.dataset.key].splice(Number(button.dataset.item),1);render();return;}
    if(action==='add-alias'){state.draft.aliases.push(entry('aliases'));render();return;}
    if(action==='remove-alias'){state.draft.aliases.splice(index,1);render();return;}
    if(action==='reset-limits'){
      if(await confirmChange('恢复默认查询设置？','将条数、时间等限制恢复为默认值。可查询的人、聊天范围和常用名称保持不变。保存后生效。')){state.draft.limits=clone(state.snapshot.defaults.limits);render();}return;
    }
  }catch(e){toast(e.message||'操作未完成。',true);}
});

$('#refresh-button').addEventListener('click',()=>load());
$('#save-button').addEventListener('click',save);
$('#discard-button').addEventListener('click',async()=>{if(await confirmChange('撤销还没保存的修改？','会恢复到上次读取或保存时的设置。')){state.draft=clone(state.snapshot.config);error();render();}});
window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});

try {
  bridge=window.AstrBotPluginView||window.AstrBotPluginPage;
  if(!bridge)throw new Error('请在 AstrBot 的“授权历史查询 → 视图”中打开本页。');
  const context=await Promise.race([bridge.ready(),new Promise((_,reject)=>setTimeout(()=>reject(new Error('与 AstrBot 的连接超时，请刷新视图。')),8000))]);
  const theme=value=>{document.documentElement.dataset.theme=value?.isDark?'dark':'light';};theme(context);
  const off=bridge.onContext?.(theme);window.addEventListener('unload',()=>off?.());
  await load(true);
}catch(e){error(e.message);$('#editor-content').innerHTML='<div class="empty-state"><h3>还没有连接到 AstrBot</h3><p>请登录 AstrBot，在插件的“视图”入口打开此页面。</p></div>';$('#live-status').textContent='未连接';}
