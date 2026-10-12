"""Read-only stdio MCP status/inventory; uploads stay explicit in CLI."""
import json
import sys


def serve(root):
    from .cli import build_engine
    for line in sys.stdin:
        try:
            if len(line)>1024*1024: raise ValueError('Request limit')
            req=json.loads(line); method=req.get('method'); ident=req.get('id')
            if ident is None: continue
            if method=='initialize':
                result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'codex-drive-sync','version':'0.1.0'}}
            elif method=='ping': result={}
            elif method=='tools/list':
                result={'tools':[{'name':name,'description':desc,'inputSchema':{'type':'object','properties':{},'additionalProperties':False}} for name,desc in [('sync_status','حالة المزامنة؛ الاستيراد الأصلي معطل'),('sync_inventory','جرد الملفات قبل الرفع'),('sync_conflicts','قائمة التعارضات')]]}
            elif method=='tools/call':
                name=req.get('params',{}).get('name')
                engine=build_engine(root,read_only=True)
                fn={'sync_status':engine.status,'sync_inventory':engine.inventory,'sync_conflicts':engine.conflicts}.get(name)
                if fn is None: raise ValueError('Unknown tool')
                result={'content':[{'type':'text','text':json.dumps(fn(),ensure_ascii=False,default=str)}]}
            else:
                print(json.dumps({'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Method not found'}}),flush=True); continue
            print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result},ensure_ascii=False),flush=True)
        except Exception:
            print(json.dumps({'jsonrpc':'2.0','id':req.get('id') if isinstance(locals().get('req'),dict) else None,'error':{'code':-32603,'message':'Operation unavailable; sensitive details omitted'}}),flush=True)
