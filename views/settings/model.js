export const clone = value => JSON.parse(JSON.stringify(value));
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const splitNames = value => [...new Set(String(value).split(/[\n,，]/).map(x => x.trim()).filter(Boolean))];
export const parseSource = value => {
  const parts = String(value || '').split(':');
  return {platform:parts[0] || '',kind:parts[1] || 'GroupMessage',peer:parts[2] || ''};
};
export function makeSource(platform, kind, peer) {
  if (!platform || platform.includes(':') || !['GroupMessage','FriendMessage'].includes(kind) || !/^[0-9]+$/.test(peer)) throw new Error('请选择机器人，并填写纯数字的群号或 QQ 号。');
  return `${platform}:${kind}:${peer}`;
}
export function entry(kind, first='') {
  if (kind === 'instances') return {__template_key:'entry',platform_id:'',bot_id:''};
  if (kind === 'readers') return {__template_key:'entry',platform_id:first,user_id:'',groups:[],private_sessions:[],all_groups_on:[],all_private_on:[]};
  return {__template_key:'entry',source:'',names:[]};
}
export function setPath(object, path, value) {
  const keys=path.split('.');
  if (!['enabled','audit_enabled','instances','readers','aliases','limits'].includes(keys[0]) || keys.some(k => ['__proto__','prototype','constructor'].includes(k))) throw new Error('不支持的设置字段');
  const last=keys.pop();let target=object;
  for (const key of keys) { if (target[key] == null) throw new Error('设置条目已变化，请重试');target=target[key]; }
  target[last]=value;
}
export function instanceReferences(config, platform) {
  let count=0;
  for (const reader of config.readers) {
    if (reader.platform_id === platform) count++;
    for (const key of ['groups','private_sessions']) count += reader[key].filter(source => parseSource(source).platform === platform).length;
    for (const key of ['all_groups_on','all_private_on']) count += reader[key].filter(id => id === platform).length;
  }
  count += config.aliases.filter(alias => parseSource(alias.source).platform === platform).length;
  return count;
}
export function validate(config, bounds) {
  const issues=[];const instances=new Set();const readers=new Set();const aliases=new Set();
  config.instances.forEach((item,i) => {
    const id=item.platform_id.trim();
    if (!id || id.includes(':') || /[\x00-\x1f]/.test(id)) issues.push(`第 ${i+1} 个机器人：请填写有效的 AstrBot 连接名称。`);
    if (instances.has(id)) issues.push(`连接名称“${id}”重复，请只保留一项。`);instances.add(id);
    if (!/^[0-9]+$/.test(item.bot_id.trim())) issues.push(`第 ${i+1} 个机器人：QQ 号须为纯数字。`);
  });
  config.readers.forEach((reader,i) => {
    if (!instances.has(reader.platform_id)) issues.push(`第 ${i+1} 个人：请选择已经添加的机器人。`);
    if (!/^[0-9]+$/.test(reader.user_id.trim())) issues.push(`第 ${i+1} 个人：请填写纯数字的 QQ 号。`);
    const key=`${reader.platform_id}:${reader.user_id.trim()}`;
    if (readers.has(key)) issues.push(`QQ ${reader.user_id} 在同一个机器人下重复添加，请合并设置。`);readers.add(key);
    for (const [field,kind] of [['groups','GroupMessage'],['private_sessions','FriendMessage']]) {
      for (const source of reader[field]) {const parsed=parseSource(source);if (!instances.has(parsed.platform) || parsed.kind!==kind || !/^[0-9]+$/.test(parsed.peer)) issues.push(`第 ${i+1} 个人：请检查允许查询的机器人、群号和 QQ 号。`);}
    }
    for (const field of ['all_groups_on','all_private_on']) if (reader[field].some(id => !instances.has(id))) issues.push(`第 ${i+1} 个人：“全部聊天”中选中的机器人已被移除，请重新选择。`);
  });
  config.aliases.forEach((alias,i) => {
    const source=parseSource(alias.source);
    if (!instances.has(source.platform) || !['GroupMessage','FriendMessage'].includes(source.kind) || !/^[0-9]+$/.test(source.peer)) issues.push(`第 ${i+1} 项常用名称：请选择机器人，并填写群号或 QQ 号。`);
    if (aliases.has(alias.source)) issues.push(`第 ${i+1} 项常用名称：同一个聊天的多个名称，请填在同一项中。`);aliases.add(alias.source);
    if (!alias.names.length) issues.push(`第 ${i+1} 项常用名称：至少填写一个名称，或者移除这一项。`);
  });
  const labels={scan:'一次最多读多少条',private_recent_scan:'私聊回看条数',queue_seconds:'最多排队多久',execution_seconds:'查找时间上限',queue_capacity:'最多排队几个请求',page_size:'每批读取条数',return_chars:'给模型的内容上限',blob_bytes:'单份记录大小上限',idle_seconds:'闲置多久释放内存'};
  for (const [key,[min,max]] of Object.entries(bounds || {})) {
    const value=config.limits[key];
    if (!Number.isFinite(value) || value<min || value>max) issues.push(`${labels[key]||'查询设置'}须在 ${min} 到 ${max} 之间。`);
    else if (!['queue_seconds','execution_seconds'].includes(key) && !Number.isInteger(value)) issues.push(`${labels[key]||'这项设置'}必须为整数。`);
  }
  return [...new Set(issues)];
}
