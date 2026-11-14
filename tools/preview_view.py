"""Loopback-only preview with synthetic, in-memory settings. Never serves host data."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history_query.admin import AdminError,ConfigEditor,PLUGIN_NAME,defaults

config=defaults()
config.update(instances=[{'__template_key':'entry','platform_id':'qq-main','bot_id':'100000'}],
              readers=[{'__template_key':'entry','platform_id':'qq-main','user_id':'200000',
                        'groups':['qq-main:GroupMessage:300000'],'private_sessions':[],
                        'all_groups_on':[],'all_private_on':[]}],
              aliases=[{'__template_key':'entry','source':'qq-main:GroupMessage:300000','names':['英语群']}])


class Context:
    def __init__(self):
        self.platform_manager=NS(platform_insts=[self.get_platform_inst('qq-main')])
    def get_platform_inst(self,instance):
        if instance!='qq-main':return None
        async def action(method):
            if method=='get_login_info':return {'user_id':'100000'}
            if method=='get_group_list':return [{'group_id':'300000','group_name':'大学英语班'},
                                               {'group_id':'300001','group_name':'学院通知群'},
                                               {'group_id':'300002','group_name':'<img src=x onerror=alert(1)>'}]
            raise AssertionError('No other preview RPC')
        return NS(meta=lambda:NS(id='qq-main',name='aiocqhttp'),bot=NS(call_action=action))


async def persist(value):config.update(copy.deepcopy(value));return True
async def apply():return None
editor=ConfigEditor(config,Context(),'4.29.4',lambda:{'query_enabled':config['enabled'],'active_queries':0,
    'queued_queries':0,'worker_state':'未启动','runtime_error':None},persist,apply,lambda:False)

BRIDGE=r"""
window.AstrBotPluginView={
  ready:async()=>({pluginName:'astrbot_plugin_history_query',pageName:'settings',isDark:new URL(location.href).searchParams.get('theme')==='dark'}),
  onContext:()=>()=>{},
  apiGet:async(endpoint,params={})=>request('GET',endpoint,params),
  apiPost:async(endpoint,body)=>request('POST',endpoint,body)
};
async function request(method,endpoint,value){
 const url='/__preview/'+endpoint.replace(/^admin\//,'')+(method==='GET'?'?'+new URLSearchParams(value):'');
 const response=await fetch(url,{method,headers:{'Content-Type':'application/json'},...(method==='POST'?{body:JSON.stringify(value)}:{})});
 const data=await response.json();if(!response.ok)throw new Error(data.message||'Preview request failed');return data;
}
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,code,data,mime='application/json'):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type',mime+'; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        url=urlparse(self.path)
        if url.path=='/__preview/bridge.js':return self.send(200,BRIDGE.encode(),'text/javascript')
        if url.path.startswith('/__preview/'):
            try:
                query=parse_qs(url.query);name=url.path.rsplit('/',1)[-1]
                if name=='state':value=asyncio.run(editor.get('preview-admin',PLUGIN_NAME))
                elif name=='groups':value=asyncio.run(editor.groups('preview-admin',PLUGIN_NAME,query.get('platform_id',[''])[0]))
                elif name=='identify':value=asyncio.run(editor.identify('preview-admin',PLUGIN_NAME,query.get('platform_id',[''])[0]))
                else:return self.send(404,{'message':'Unknown preview route'})
                return self.send(200,value)
            except AdminError as exc:return self.send(exc.status,{'message':str(exc)})
        name=url.path.strip('/') or 'index.html'
        if name not in {'index.html','app.js','model.js','style.css'}:return self.send(404,{'message':'Not found'})
        data=(ROOT/'views/settings'/name).read_bytes()
        if name=='index.html':data=data.replace(b'<script type="module"',b'<script src="/__preview/bridge.js"></script><script type="module"')
        self.send(200,data,'text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'text/javascript')
    def do_POST(self):
        if urlparse(self.path).path!='/__preview/save':return self.send(404,{'message':'Unknown preview route'})
        size=int(self.headers.get('Content-Length','0'))
        if size>262144:return self.send(413,{'message':'Payload too large'})
        try:
            payload=json.loads(self.rfile.read(size));result=asyncio.run(editor.save('preview-admin',PLUGIN_NAME,payload))
            self.send(200,result)
        except AdminError as exc:self.send(exc.status,{'message':str(exc)})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=0);args=parser.parse_args()
    server=HTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Synthetic preview: http://127.0.0.1:{server.server_port}/',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
