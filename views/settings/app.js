import {clone, escapeHTML as esc, splitNames, parseSource, makeSource, entry, setPath, instanceReferences, validate} from './model.js';

const $ = selector => document.querySelector(selector);
const sections = {
  overview:['OVERVIEW','查询控制台','先绑定机器人，再设置查询用户与可访问的会话。'],
  instances:['CONNECTIONS','机器人实例','将 AstrBot 平台实例与实际机器人 QQ 绑定，避免跨实例读错记录。'],
  readers:['ACCESS CONTROL','查询用户','每个用户单独授权。空列表表示不开放，不会自动允许查询全部会话。'],
  aliases:['SESSION ALIASES','会话别名','让“英语群”等常用名称指向确定的群或私聊，减少定位歧义。'],
  limits:['RESOURCE LIMITS','资源与审计','控制一次查询的读取量、等待时间和返回内容，适合低资源部署。'],
};
const state = {snapshot:null,draft:null,tab:'overview',reader:0,busy:false,groups:{},validationIssue:false,
  groupPlatform:'',privatePlatform:'',groupPeer:'',privatePeer:''};
let bridge = null;
let toastTimer = null;
let confirmation = null;
const dirty = () => Boolean(state.snapshot && JSON.stringify(state.draft)!==JSON.stringify(state.snapshot.config));

function error(message='') { $('#load-error').textContent=message;$('#load-error').hidden=!message; }
function toast(message, failure=false) {
  clearTimeout(toastTimer);const box=$('#toast');box.textContent=message;box.classList.toggle('error',failure);box.hidden=false;
  toastTimer=setTimeout(()=>{box.hidden=true;},failure?6500:3800);
}
function confirmChange(title, message) {
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
  const changed=dirty();$('#dirty-badge').textContent=changed?'● 未保存':'已保存';$('#dirty-badge').classList.toggle('unsaved',changed);
  $('#save-button').disabled=state.busy||!changed;$('#discard-button').disabled=state.busy||!changed;
  $('#save-button').textContent=state.busy?'处理中…':'保存设置';
  $('#refresh-button').disabled=state.busy;
  $('#save-hint').textContent=!state.snapshot?'设置尚未加载':changed?'更改尚未保存，保存后才会生效。':'设置已与服务器同步。';
  for(const key of ['instances','readers','aliases']) document.querySelector(`[data-count="${key}"]`).textContent=state.draft?.[key]?.length||0;
  document.querySelectorAll('[data-action="select-reader"]').forEach(button=>{
    const reader=state.draft.readers[Number(button.dataset.index)];
    if(reader){button.querySelector('strong').textContent=reader.user_id||'新查询用户';button.querySelector('small').textContent=reader.platform_id||'尚未选择实例';}
  });
}
function busy(value) {
  state.busy=value;
  $('#editor-content').querySelectorAll('input,select,textarea,button').forEach(node=>{node.disabled=value;});
  $('#navigation').querySelectorAll('button').forEach(node=>{node.disabled=value;});
  markDirty();
}
function options(selected, blank='选择实例') {
  return `<option value="">${esc(blank)}</option>`+state.draft.instances.map(instance=>`<option value="${esc(instance.platform_id)}" ${instance.platform_id===selected?'selected':''}>${esc(instance.platform_id||'未填写 ID')}</option>`).join('');
}
function field(label,path,value,help='',attributes='') {
  return `<label class="field"><span>${esc(label)}</span><input data-path="${esc(path)}" value="${esc(value)}" ${attributes}>${help?`<p class="help">${esc(help)}</p>`:''}</label>`;
}
function selectField(label,path,value) {
  return `<label class="field"><span>${esc(label)}</span><select data-path="${esc(path)}">${options(value)}</select></label>`;
}
function switchRow(title,path,value,help) {
  return `<div class="switch-row"><div><h3>${esc(title)}</h3><p>${esc(help)}</p></div><label class="switch"><input type="checkbox" aria-label="${esc(title)}" data-path="${esc(path)}" ${value?'checked':''}><span class="switch-track"></span></label></div>`;
}
function intro(number,title,description) {
  return `<div class="section-title"><span class="section-number">${number}</span><div><h3>${esc(title)}</h3><p>${esc(description)}</p></div></div>`;
}
function empty(title,description,action,label) {
  return `<div class="empty-state"><h3>${esc(title)}</h3><p>${esc(description)}</p><button type="button" class="secondary" data-action="${action}">＋ ${esc(label)}</button></div>`;
}

function overview() {
  const d=state.draft;const s=state.snapshot.status;
  const steps=[['绑定机器人实例',d.instances.length>0,'明确平台实例与机器人 QQ。','instances'],
    ['授权查询用户',d.readers.length>0,'为每个人选择可查的群与私聊。','readers'],
    ['设置常用别名',d.aliases.length>0,'可选：使用“英语群”等熟悉的名称。','aliases']];
  return `<div class="section"><div class="stats">${[['instances','机器人实例','个已配置'],['readers','查询用户','位已配置'],['aliases','会话别名','项已配置']].map(([key,label,unit])=>`<div class="stat"><div class="stat-label">${label}</div><div class="stat-value">${d[key].length}<small>${unit}</small></div></div>`).join('')}</div>
    ${switchRow('启用历史查询','enabled',d.enabled,'开启后，只有配置中的用户可以在私聊里使用。此开关保存后生效。')}
    <div class="runtime-line"><span>AstrBot <strong>${esc(s.astrbot_version)}</strong></span><span>查询进程 <strong>${esc(s.worker_state||'未启动')}</strong></span><span>执行中 <strong>${Number(s.active_queries)||0}</strong></span><span>排队中 <strong>${Number(s.queued_queries)||0}</strong></span></div></div>
    <div class="section">${intro('01','开始配置','按需填写即可；没有授权的会话不会因为开启总开关而自动开放。')}
    <div class="steps">${steps.map(([title,done,description,tab],i)=>`<div class="step"><span class="step-check ${done?'done':''}">${done?'✓':i+1}</span><div class="step-copy"><strong>${title}</strong><p>${description}</p></div><button type="button" class="small quiet" data-action="navigate" data-tab="${tab}">${done?'管理':'去配置'} →</button></div>`).join('')}</div></div>
    <div class="section">${intro('02','在聊天里使用','完成设置后，直接私聊机器人，不需要查询命令或切换到本页面。')}
    <div class="example">“学院群最近有没有修改活动时间？”</div><div class="example">“帮我查一下英语群昨天布置了什么作业。”</div>
    <p class="help">这里管理的是插件设置，不是聊天记录浏览器。读取历史后，本轮只继续查询和补读；其他操作留到下一条明确请求。</p></div>`;
}

function instances() {
  const values=state.draft.instances;
  const list=(state.snapshot.available_instances||[]).map(item=>`<option value="${esc(item.id)}">${esc(item.type)}</option>`).join('');
  return `<div class="section"><datalist id="known-instances">${list}</datalist><div class="toolbar"><div><h3>实例绑定</h3><p>实例 ID 来自 AstrBot 平台配置，不是 aiocqhttp 这个类型名称。</p></div><button type="button" class="secondary" data-action="add-instance">＋ 新增实例</button></div>
    ${values.length?values.map((item,i)=>`<article class="form-card"><div class="card-top"><h3><span class="serial">${String(i+1).padStart(2,'0')}</span>机器人实例</h3><button type="button" class="small quiet danger" data-action="remove-instance" data-index="${i}">移除</button></div><div class="grid">
      ${field('平台实例 ID',`instances.${i}.platform_id`,item.platform_id,'可从候选列表选择，也可以手动输入已有实例 ID。','list="known-instances" placeholder="例如：qq-main" maxlength="128"')}
      <label class="field"><span>机器人 QQ 号</span><div class="inline-controls"><input aria-label="机器人 QQ 号" data-path="instances.${i}.bot_id" value="${esc(item.bot_id)}" inputmode="numeric" placeholder="该实例登录的 QQ"><button type="button" class="small" data-action="identify" data-index="${i}">识别账号</button></div><p class="help">识别只读取当前账号信息，不发送消息。</p></label></div></article>`).join(''):empty('还没有绑定机器人','先填写一个实例，之后才能为查询用户选择来源。','add-instance','绑定第一个实例')}
    <p class="readonly-tip">已被授权或别名引用的实例不能直接移除。机器人换号时建议新建实例 ID，避免混用旧历史。</p></div>`;
}

function sourceName(source) {
  const p=parseSource(source);
  const group=(state.groups[p.platform]||[]).find(g=>g.id===p.peer);
  const alias=state.draft.aliases.find(a=>a.source===source)?.names?.[0];
  return `${group?.name||alias||(p.kind==='GroupMessage'?'群 '+p.peer:'私聊 '+p.peer)} · ${p.platform}`;
}
function sourceSection(reader,index,kind) {
  const group=kind==='group';const key=group?'groups':'private_sessions';const prefix=group?'group':'private';
  const title=group?'指定群聊':'指定私聊';const selected=state[`${prefix}Platform`];
  const current=state.draft.instances.some(item=>item.platform_id===selected)?selected:state.draft.instances[0]?.platform_id||'';
  state[`${prefix}Platform`]=current;
  const catalog=state.groups[current]||[];
  return `<div class="subsection"><h3>${title}</h3><p class="help">${group?'只允许查询这些群。支持读取当前群列表，也可以手填群号。':'填写对方 QQ，授权读取其与机器人的已保存 AI 对话；不读取个人 QQ 客户端数据。'}</p>
    ${group?`<button type="button" class="small quiet" data-action="load-groups">↻ 读取群列表</button>`:''}
    <div class="source-builder"><label class="field"><span>来源实例</span><select data-ui="${prefix}Platform">${options(current)}</select></label>
    <label class="field"><span>${group?'群号':'对方 QQ'}</span><input data-ui="${prefix}Peer" value="${esc(state[`${prefix}Peer`])}" inputmode="numeric" placeholder="${group?'输入或选择群号':'输入 QQ 号'}" ${group?'list="group-options"':''}></label>
    <button type="button" class="secondary" data-action="add-source" data-kind="${kind}" data-index="${index}">添加${group?'群':'私聊'}</button></div>
    ${group?`<datalist id="group-options">${catalog.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('')}</datalist>`:''}
    ${reader[key].length?`<div class="chips">${reader[key].map((source,j)=>`<div class="chip" title="${esc(source)}"><span>${esc(sourceName(source))}</span><button type="button" aria-label="移除${esc(sourceName(source))}" data-action="remove-source" data-index="${index}" data-key="${key}" data-item="${j}">×</button></div>`).join('')}</div>`:`<p class="empty-chips">尚未单独授权${group?'群聊':'私聊'}。</p>`}</div>`;
}
function allScopes(reader,index) {
  return `<div class="subsection"><h3>按实例开放全部会话</h3><p class="help">这是显式的全部授权，后续新增会话也会纳入。只在确实需要时勾选。</p>
    <div class="grid">${[['all_groups_on','全部群聊'],['all_private_on','全部私聊']].map(([key,label])=>`<div><strong class="muted">${label}</strong><div class="scope-checks">${state.draft.instances.map(instance=>`<label class="scope-check"><input type="checkbox" data-array-path="readers.${index}.${key}" data-member="${esc(instance.platform_id)}" ${reader[key].includes(instance.platform_id)?'checked':''}><span>${esc(instance.platform_id)}</span></label>`).join('')}</div></div>`).join('')}</div>
    ${reader.all_private_on.length?'<p class="inline-note">此用户将可查询所选实例下其他人与机器人的已保存 AI 对话，请确认这是你的授权意图。</p>':''}
    <p class="help">全部群聊授权仍需通过实时群访问检查：确认退群或无法确认在群内时，不读取该群历史。</p></div>`;
}
function readers() {
  if (!state.draft.instances.length) return `<div class="section">${empty('先绑定一个机器人实例','实例用于明确调用者身份和历史记录来源。','goto-instances','前往实例设置')}</div>`;
  const values=state.draft.readers;
  if (!values.length) return `<div class="section">${empty('还没有查询用户','普通聊天不会自动获得跨会话读取权限。新增用户并选择允许查询的会话。','add-reader','新增查询用户')}</div>`;
  state.reader=Math.min(state.reader,values.length-1);const index=state.reader;const reader=values[index];
  return `<div class="section"><div class="toolbar"><div><h3>独立用户白名单</h3><p>不需要给予完整 AstrBot 管理员权限。</p></div><button type="button" class="secondary" data-action="add-reader">＋ 新增用户</button></div>
    <div class="reader-workspace"><div class="reader-list" aria-label="查询用户列表">${values.map((item,i)=>`<button type="button" class="reader-choice ${i===index?'selected':''}" data-action="select-reader" data-index="${i}" aria-pressed="${i===index}"><strong>${esc(item.user_id||'新查询用户')}</strong><small>${esc(item.platform_id||'尚未选择实例')}</small></button>`).join('')}</div>
    <div class="reader-detail"><div class="card-top"><h3>查询者身份</h3><button type="button" class="small quiet danger" data-action="remove-reader" data-index="${index}">移除此用户</button></div>
    <div class="grid">${selectField('发起查询的实例',`readers.${index}.platform_id`,reader.platform_id)}${field('查询者 QQ',`readers.${index}.user_id`,reader.user_id,'仅该账号在此实例的私聊请求获得授权。','inputmode="numeric" placeholder="填写获授权用户 QQ"')}</div>
    ${sourceSection(reader,index,'group')}${sourceSection(reader,index,'private')}${allScopes(reader,index)}</div></div></div>`;
}

function aliases() {
  if (!state.draft.instances.length) return `<div class="section">${empty('先绑定机器人实例','每个别名都需要指向明确的实例和会话。','goto-instances','前往实例设置')}</div>`;
  return `<div class="section"><div class="toolbar"><div><h3>常用会话名称</h3><p>别名只帮助定位，不会授予访问权限。</p></div><button type="button" class="secondary" data-action="add-alias">＋ 新增别名</button></div>
  ${state.draft.aliases.length?state.draft.aliases.map((item,i)=>{const parsed=parseSource(item.source);return `<article class="form-card" data-alias-index="${i}"><div class="card-top"><h3><span class="serial">${String(i+1).padStart(2,'0')}</span>会话别名</h3><button type="button" class="small quiet danger" data-action="remove-alias" data-index="${i}">移除</button></div>
    <div class="alias-source"><label class="field"><span>来源实例</span><select data-alias-part="platform">${options(parsed.platform||state.draft.instances[0].platform_id)}</select></label>
    <label class="field"><span>会话类型</span><select data-alias-part="kind"><option value="GroupMessage" ${parsed.kind==='GroupMessage'?'selected':''}>群聊</option><option value="FriendMessage" ${parsed.kind==='FriendMessage'?'selected':''}>私聊</option></select></label>
    <label class="field"><span>群号 / 对方 QQ</span><input data-alias-part="peer" value="${esc(parsed.peer)}" inputmode="numeric" placeholder="纯数字 ID"></label></div>
    <label class="field"><span>别名</span><textarea data-path="aliases.${i}.names" data-value-type="list" placeholder="例如：英语群&#10;大学英语班">${esc(item.names.join('\n'))}</textarea><p class="help">每行一个，也可用逗号分隔。不同会话使用同一别名时，机器人仍会询问你具体指哪一个。</p></label></article>`;}).join(''):empty('给常用会话起个熟悉的名字','例如把指定群设置为“英语群”。也可以直接使用平台提供的群名称，不必重复添加。','add-alias','添加会话别名')}</div>`;
}

function limits() {
  const values=state.draft.limits;
  const fields=[['scan','每轮逻辑消息数','多个会话、频率采样和上下文补读共同使用。',1],['page_size','每批读取条数','来源之间轮流分批；某处查完后，额度留给其他来源。',1],['queue_seconds','累计排队时间（秒）','整轮累计，不因多次调用而重置。',0.1],['execution_seconds','累计读取与筛选时间（秒）','与排队分开计算，不包含模型生成回答。',0.1],['queue_capacity','等待请求容量','执行并发固定为 1；队列已满时提示稍后重试。',1],['return_chars','每轮返回字符数','正文过长时裁剪并标记，不表示完整原文。',1],['blob_bytes','单段数据上限（字节）','1 MiB = 1,048,576 字节；超出时拒绝或跳过并说明。',1],['idle_seconds','查询进程空闲回收（秒）','普通聊天不会启动查询进程；空闲后自动释放。',1]];
  return `<div class="section"><div class="toolbar"><div><h3>查询资源预算</h3><p>建议先使用默认值，再根据实测调整。</p></div><button type="button" class="small" data-action="reset-limits">恢复默认额度</button></div>
    <div class="grid">${fields.map(([key,label,help,step])=>{const bounds=state.snapshot.limits[key];return field(label,`limits.${key}`,values[key],help,`type="number" min="${bounds[0]}" max="${bounds[1]}" step="${step}"`);}).join('')}</div>
    <p class="inline-note">保存后会回收当前查询运行状态；旧引用需重新获取。5 秒是触发中止的预算，必要清理另有少量开销。</p></div>
    <div class="section">${switchRow('记录查询审计','audit_enabled',state.draft.audit_enabled,'只记录查询者、来源标识、状态和数量，不记录聊天正文、关键词或完整问题。')}
    <p class="help">审计保留当前和上一份约 1 MiB 文件。AstrBot 本身仍可能保存工具结果与最终回答，这个开关不改变宿主的正常留存。</p></div>`;
}

function render() {
  if (!state.draft) return;
  const [kicker,title,description]=sections[state.tab];$('#section-kicker').textContent=kicker;$('#section-title').textContent=title;$('#section-description').textContent=description;
  $('#navigation').querySelectorAll('[data-tab]').forEach(button=>{const selected=button.dataset.tab===state.tab;button.classList.toggle('is-active',selected);button.setAttribute('aria-pressed',String(selected));});
  const status=state.snapshot.status;const enabled=state.snapshot.config.enabled;
  $('#live-status').textContent=enabled?'● 查询已启用':'○ 查询未启用';$('#live-status').classList.toggle('enabled',enabled);
  const notice=$('#notice');notice.classList.toggle('warning',!status.version_supported||Boolean(status.runtime_error));
  const text=!status.version_supported?`当前 AstrBot ${status.astrbot_version} 尚未通过本插件的兼容核验。可编辑设置，历史查询保持不可用。`:status.runtime_error?`查询接入提示：${status.runtime_error}。设置仍可编辑；需要时请检查宿主日志或重载插件。`:'先授权，再启用。所有改动保存后生效；没有授权的用户和会话不会自动开放。';
  notice.querySelector('p').textContent=text;
  $('#editor-content').innerHTML=({overview,instances,readers,aliases,limits})[state.tab]();
  markDirty();
}

async function load(force=false) {
  if(state.busy)return;
  if(dirty()&&!force&&!await confirmChange('放弃未保存的更改？','刷新会重新读取服务器设置，当前未保存的草稿将被替换。'))return;
  busy(true);
  try {
    if(!bridge)throw new Error('请从 AstrBot 的插件视图打开此页面。');
    const value=await bridge.apiGet('admin/state');
    state.snapshot=value;state.draft=clone(value.config);state.groups={};error();
    render();
  } catch(e) {error(e.message||'加载失败，请稍后重试。');}
  finally{busy(false);}
}

async function save() {
  if(state.busy||!dirty())return;
  const issues=validate(state.draft,state.snapshot.limits);
  if(issues.length){state.validationIssue=true;error(issues.slice(0,8).join('\n'));toast('请先修正标出的配置问题。',true);return;}
  busy(true);error();
  try{
    const value=await bridge.apiPost('admin/save',{revision:state.snapshot.revision,config:clone(state.draft)});
    const oldBindings=new Map(state.snapshot.config.instances.map(item=>[item.platform_id,item.bot_id]));
    state.groups=Object.fromEntries(Object.entries(state.groups).filter(([id])=>value.config.instances.some(item=>item.platform_id===id&&item.bot_id===oldBindings.get(id))));
    state.snapshot=value;state.draft=clone(value.config);render();
    if(value.warning)error(value.warning);
    toast(value.message||'设置已保存。');
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
      if(count){toast(`该实例仍被 ${count} 处授权或别名引用，请先调整引用。`,true);return;}
      if(await confirmChange('移除机器人实例？','仅修改配置草稿，不删除历史记录。保存后才会生效。')){state.draft.instances.splice(index,1);render();}return;
    }
    if(action==='identify'){
      const original=state.draft.instances[index];const instance=original?.platform_id.trim();const previousBot=original?.bot_id;if(!instance)throw new Error('请先填写平台实例 ID。');
      button.disabled=true;button.textContent='识别中…';
      try{const result=await bridge.apiGet('admin/identify',{platform_id:instance});if(state.draft.instances[index]===original&&original.platform_id.trim()===instance&&original.bot_id===previousBot){original.bot_id=result.bot_id;render();toast('已识别机器人 QQ，请保存设置。');}else{toast('识别期间条目已更改，已保留你的输入。');}}
      finally{button.disabled=false;button.textContent='识别账号';}return;
    }
    if(action==='add-reader'){state.draft.readers.push(entry('readers',state.draft.instances[0]?.platform_id||''));state.reader=state.draft.readers.length-1;render();return;}
    if(action==='select-reader'){state.reader=index;state.groupPeer='';state.privatePeer='';render();return;}
    if(action==='remove-reader'){
      if(await confirmChange('移除这个查询用户？','保存后，将撤销该用户在本插件配置中的全部会话授权；聊天记录不会被删除。')){state.draft.readers.splice(index,1);state.reader=0;render();}return;
    }
    if(action==='load-groups'){
      const instance=state.groupPlatform;if(!instance)throw new Error('请先选择来源实例。');
      button.disabled=true;button.textContent='读取中…';
      try{const result=await bridge.apiGet('admin/groups',{platform_id:instance});state.groups[instance]=result.groups;render();toast(`已读取 ${result.groups.length} 个群，请在群号输入框中选择。`);}
      finally{button.disabled=false;button.textContent='↻ 读取群列表';}return;
    }
    if(action==='add-source'){
      const group=button.dataset.kind==='group';const prefix=group?'group':'private';const source=makeSource(state[`${prefix}Platform`],group?'GroupMessage':'FriendMessage',state[`${prefix}Peer`].trim());
      const key=group?'groups':'private_sessions';const values=state.draft.readers[index][key];if(!values.includes(source))values.push(source);state[`${prefix}Peer`]='';render();return;
    }
    if(action==='remove-source'){state.draft.readers[index][button.dataset.key].splice(Number(button.dataset.item),1);render();return;}
    if(action==='add-alias'){state.draft.aliases.push(entry('aliases'));render();return;}
    if(action==='remove-alias'){state.draft.aliases.splice(index,1);render();return;}
    if(action==='reset-limits'){
      if(await confirmChange('恢复默认资源额度？','只替换资源与时间额度的草稿，不改变用户授权或会话别名。')){state.draft.limits=clone(state.snapshot.defaults.limits);render();}return;
    }
  }catch(e){toast(e.message||'操作未完成。',true);}
});

$('#refresh-button').addEventListener('click',()=>load());
$('#save-button').addEventListener('click',save);
$('#discard-button').addEventListener('click',async()=>{if(await confirmChange('放弃本次更改？','当前草稿将恢复为最近一次读取或保存的设置。')){state.draft=clone(state.snapshot.config);error();render();}});
window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});

try {
  bridge=window.AstrBotPluginView||window.AstrBotPluginPage;
  if(!bridge)throw new Error('请在 AstrBot 的「授权历史查询 → 视图」中打开本页。');
  const context=await Promise.race([bridge.ready(),new Promise((_,reject)=>setTimeout(()=>reject(new Error('与 AstrBot 的连接超时，请刷新视图。')),8000))]);
  const theme=value=>{document.documentElement.dataset.theme=value?.isDark?'dark':'light';};theme(context);
  const off=bridge.onContext?.(theme);window.addEventListener('unload',()=>off?.());
  await load(true);
}catch(e){error(e.message);$('#editor-content').innerHTML='<div class="empty-state"><h3>尚未连接管理后台</h3><p>此页面通过 AstrBot 提供的安全通道读取和保存配置。请从插件的视图入口打开。</p></div>';$('#live-status').textContent='未连接';}
