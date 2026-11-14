export const clone = value => JSON.parse(JSON.stringify(value));
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const splitNames = value => [...new Set(String(value).split(/[\n,，]/).map(x => x.trim()).filter(Boolean))];
export const parseSource = value => {
  const parts = String(value || '').split(':');
  return {platform:parts[0] || '',kind:parts[1] || 'GroupMessage',peer:parts[2] || ''};
};
export function makeSource(platform, kind, peer) {
  if (!platform || platform.includes(':') || !['GroupMessage','FriendMessage'].includes(kind) || !/^[0-9]+$/.test(peer)) throw new Error('请选择实例，并填写纯数字的群号或 QQ。');
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
    if (!id || id.includes(':') || /[\x00-\x1f]/.test(id)) issues.push(`机器人实例 ${i+1}：请填写有效的实例 ID。`);
    if (instances.has(id)) issues.push(`实例 ID“${id}”重复。`);instances.add(id);
    if (!/^[0-9]+$/.test(item.bot_id.trim())) issues.push(`机器人实例 ${i+1}：QQ 号须为纯数字。`);
  });
  config.readers.forEach((reader,i) => {
    if (!instances.has(reader.platform_id)) issues.push(`查询用户 ${i+1}：请选择已绑定的实例。`);
    if (!/^[0-9]+$/.test(reader.user_id.trim())) issues.push(`查询用户 ${i+1}：请填写纯数字 QQ 号。`);
    const key=`${reader.platform_id}:${reader.user_id.trim()}`;
    if (readers.has(key)) issues.push(`查询用户 ${reader.user_id} 在同一实例中重复。`);readers.add(key);
    for (const [field,kind] of [['groups','GroupMessage'],['private_sessions','FriendMessage']]) {
      for (const source of reader[field]) {const parsed=parseSource(source);if (!instances.has(parsed.platform) || parsed.kind!==kind || !/^[0-9]+$/.test(parsed.peer)) issues.push(`查询用户 ${i+1}：存在无效的会话授权。`);}
    }
    for (const field of ['all_groups_on','all_private_on']) if (reader[field].some(id => !instances.has(id))) issues.push(`查询用户 ${i+1}：全部会话授权引用了未绑定的实例。`);
  });
  config.aliases.forEach((alias,i) => {
    const source=parseSource(alias.source);
    if (!instances.has(source.platform) || !['GroupMessage','FriendMessage'].includes(source.kind) || !/^[0-9]+$/.test(source.peer)) issues.push(`会话别名 ${i+1}：请选择有效来源。`);
    if (aliases.has(alias.source)) issues.push(`会话别名 ${i+1}：同一来源请合并到一个条目中。`);aliases.add(alias.source);
    if (!alias.names.length) issues.push(`会话别名 ${i+1}：至少填写一个别名，或删除空条目。`);
  });
  const labels={scan:'每轮逻辑消息数',queue_seconds:'累计排队时间',execution_seconds:'累计读取与筛选时间',queue_capacity:'等待请求容量',page_size:'每批读取条数',return_chars:'每轮返回字符数',blob_bytes:'单段数据上限',idle_seconds:'查询进程空闲回收时间'};
  for (const [key,[min,max]] of Object.entries(bounds || {})) {
    const value=config.limits[key];
    if (!Number.isFinite(value) || value<min || value>max) issues.push(`${labels[key]||'资源限额'}须在 ${min} 到 ${max} 之间。`);
  }
  return [...new Set(issues)];
}
