import os, json, hashlib, sqlite3, uuid, re, threading
from datetime import datetime, timezone
from typing import Any
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

APP_NAME = os.getenv('AGENT_NAME', 'Invoice Action Agent')
BASE_URL = os.getenv('A2A_BASE_URL', 'https://REPLACE-WITH-RENDER-URL/a2a/')
DB_PATH = os.getenv('DB_PATH', '/var/data/agent.db')
TOKENS = {x.strip() for x in os.getenv('A2A_BEARER_TOKENS', 'development-token').split(',') if x.strip()}
INPUT_MODE='application/vnd.ga5.invoice-claim-batch+json'
PROPOSAL_MODE='application/vnd.ga5.invoice-action-proposals+json'
RESULT_MODE='application/vnd.ga5.invoice-action-results+json'
RECEIPT_MODE='application/vnd.ga5.invoice-action-receipts+json'
A2A_MODE='application/a2a+json'
DB_LOCK=threading.RLock()
app=FastAPI(title=APP_NAME, version='1.0.0')

def db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    c=sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, principal TEXT NOT NULL, context_id TEXT NOT NULL, batch_id TEXT NOT NULL, state TEXT NOT NULL, task_json TEXT NOT NULL, proposals_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages(principal TEXT NOT NULL, message_id TEXT NOT NULL, digest TEXT NOT NULL, task_id TEXT NOT NULL, PRIMARY KEY(principal,message_id))''')
    c.commit(); return c

def now(): return datetime.now(timezone.utc).isoformat()
def digest(v): return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(',',':'), ensure_ascii=False).encode()).hexdigest()
def auth(token):
    if not token or token not in TOKENS: raise HTTPException(401, 'UNAUTHENTICATED')
    return hashlib.sha256(token.encode()).hexdigest()
def task_obj(task_id, context_id, state, history, artifacts):
    return {'id':task_id,'contextId':context_id,'status':{'state':state},'history':history,'artifacts':artifacts,'metadata':{}}
def artifact(media,data): return {'parts':[{'mediaType':media,'data':data}]}
def part_data(message, media):
    for p in message.get('parts',[]):
        if p.get('mediaType')==media and isinstance(p.get('data'),dict): return p['data']
    return None

def extract_text(pkg):
    vals=[]
    def walk(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if isinstance(v,(str,int,float)): vals.append(f'{k}: {v}')
                else: walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
        elif isinstance(x,str): vals.append(x)
    walk(pkg)
    return '\n'.join(vals)

def decide(pkg):
    """Conservative evidence-led baseline. Integrate a hosted LLM via decide_with_llm for audit-grade accuracy."""
    text=extract_text(pkg); low=text.lower()
    # Keep only explicit bracketed refs in the package; never fabricate evidence.
    refs=list(dict.fromkeys(re.findall(r'\[[^\]]{2,100}\]', text)))
    def field(*names, default=''):
        for n in names:
            if isinstance(pkg,dict) and pkg.get(n) not in (None,''): return pkg[n]
        return default
    vendor=str(field('vendorName','vendor','supplierName','supplier',default='Unknown'))
    invoice=str(field('invoiceNumber','invoiceNo','invoice_id',default='Unknown'))
    currency=str(field('currency',default='INR')).upper()
    amount=field('amountMinor','amount_minor')
    if amount is None:
        raw=field('amount','total','invoiceAmount',default=0)
        try: amount=round(float(str(raw).replace(',','').replace('₹','').replace('INR','').strip())*100)
        except: amount=0
    try: amount=int(amount)
    except: amount=0
    # Explicit decisive signals take precedence; contradictory facts are escalated.
    if any(x in low for x in ['already paid','payment completed previously','duplicate invoice paid','same invoice was paid']): action='reject_duplicate'
    elif any(x in low for x in ['verification pending','awaiting bank verification','tax verification pending','hold payment','do not pay until','verification must complete']): action='hold_invoice'
    elif any(x in low for x in ['conflicting records','material discrepancy','mismatch in','records conflict','exception required','conflicting amount']): action='open_exception'
    elif any(x in low for x in ['approval required','exceeds delegated authority','outside delegated authority','requires director approval','approval limit exceeded']): action='request_approval'
    elif any(x in low for x in ['reconciled','within autonomous authority','approved for autonomous settlement','three-way match passed','valid and within authority']): action='settle_invoice'
    else: action='open_exception'
    # Three references only, exact refs from source. Do not use training/cover/archive decoys if identifiable.
    decisive=[r for r in refs if not re.search(r'cover|archive|training|example|decoy',r,re.I)]
    evidence=decisive[-3:] if len(decisive)>=3 else decisive
    if len(evidence)!=3:
        action='open_exception'
    rationale=(f"{action} is proposed based on the package's decisive invoice evidence. "
      f"The extracted vendor is {vendor}, invoice {invoice}, amount {amount} minor units {currency}. "
      f"The action is conservative: unresolved or insufficiently supported cases are escalated rather than settled. "
      f"Evidence references: {', '.join(evidence) if evidence else 'none available'}.")
    if len(rationale)<60: rationale += ' Further review is required before any execution.'
    return {'packageId':str(field('packageId','id',default='')),'actionId':uuid.uuid4().hex,'action':action,
      'facts':{'vendorName':vendor,'invoiceNumber':invoice,'amountMinor':amount,'currency':currency},
      'evidenceRefs':evidence,'rationale':rationale[:1500]}

def load_task(c, tid, principal):
    row=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
    if not row or row['principal']!=principal: raise HTTPException(404,'NOT_FOUND')
    return row

@app.get('/.well-known/agent-card.json')
async def card():
    return {'name':APP_NAME,'description':'Evidence-grounded invoice action proposal agent. Proposals require explicit grader acceptance before execution.','version':'1.0.0','capabilities':{},'supportedInterfaces':[{'url':BASE_URL,'protocolBinding':'HTTP+JSON','protocolVersion':'1.0'}],'defaultInputModes':[INPUT_MODE],'defaultOutputModes':[PROPOSAL_MODE,RECEIPT_MODE],'skills':[{'id':'invoice_action_agent','name':'Invoice Action Agent','description':'Classifies invoice packages and proposes one evidence-cited action per package.','tags':['invoice','accounts-payable','evidence','approval']} ]}

@app.middleware('http')
async def validate_headers(request: Request, call_next):
    if request.url.path.startswith('/.well-known/'): return await call_next(request)
    if request.method=='POST' or request.url.path.startswith('/tasks'):
        if request.headers.get('A2A-Version')!='1.0': return JSONResponse({'error':'INVALID_VERSION'},status_code=400)
        if request.method=='POST' and request.headers.get('content-type','').split(';')[0].strip()!=A2A_MODE: return JSONResponse({'error':'UNSUPPORTED_MEDIA_TYPE'},status_code=415)
    return await call_next(request)

@app.post('/a2a/message:send')
async def send(request: Request, authorization: str|None=Header(default=None)):
    principal=auth(authorization[7:] if authorization and authorization.startswith('Bearer ') else None)
    body=await request.json(); msg=body.get('message') or {}; mid=msg.get('messageId')
    if not mid: raise HTTPException(400,'INVALID_MESSAGE')
    dg=digest(msg)
    with DB_LOCK:
      c=db(); old=c.execute('SELECT digest,task_id FROM messages WHERE principal=? AND message_id=?',(principal,mid)).fetchone()
      if old:
        if old['digest']!=dg: raise HTTPException(409,'IDEMPOTENCY_CONFLICT')
        row=load_task(c,old['task_id'],principal); return {'task':json.loads(row['task_json'])}
      data=part_data(msg,INPUT_MODE)
      if data:
        packages=data.get('packages',[]); batch=data.get('batchId')
        if not batch or not isinstance(packages,list) or not packages: raise HTTPException(400,'INVALID_BATCH')
        proposals=[decide(p) for p in packages]
        ids=[p['packageId'] for p in proposals]
        if any(not x for x in ids) or len(ids)!=len(set(ids)): raise HTTPException(400,'INVALID_PACKAGE_IDS')
        context=msg.get('contextId') or str(uuid.uuid4()); tid=str(uuid.uuid4())
        task=task_obj(tid,context,'TASK_STATE_INPUT_REQUIRED',[msg],[{'name':'Invoice action proposals','parts':[{'mediaType':PROPOSAL_MODE,'data':{'batchId':batch,'proposals':proposals}}]}])
        c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(tid,principal,context,batch,'TASK_STATE_INPUT_REQUIRED',json.dumps(task),json.dumps(proposals),now(),now()))
      else:
        tid=msg.get('taskId'); context=msg.get('contextId'); result=part_data(msg,RESULT_MODE)
        if not tid or not context or not result: raise HTTPException(400,'INVALID_CONTINUATION')
        row=load_task(c,tid,principal); task=json.loads(row['task_json']); proposals=json.loads(row['proposals_json'])
        if row['state']!='TASK_STATE_INPUT_REQUIRED' or context!=row['context_id'] or result.get('batchId')!=row['batch_id']: raise HTTPException(409,'TASK_MISMATCH')
        byid={p['packageId']:p for p in proposals}; executions=[]; seen=set()
        for res in result.get('results',[]):
            p=byid.get(res.get('packageId'))
            if not p or res.get('packageId') in seen or res.get('actionId')!=p['actionId'] or res.get('action')!=p['action'] or res.get('outcome') not in ('ACCEPTED','REJECTED') or not res.get('receiptNonce'): raise HTTPException(400,'INVALID_RESULT')
            seen.add(res['packageId'])
            if res['outcome']=='ACCEPTED': executions.append({'packageId':p['packageId'],'actionId':p['actionId'],'action':p['action'],'receiptNonce':res['receiptNonce'],'facts':p['facts'],'evidenceRefs':p['evidenceRefs']})
        if len(seen)!=len(proposals): raise HTTPException(400,'INCOMPLETE_RESULTS')
        task['history'].append(msg); task['artifacts'].append({'name':'Invoice action receipts','parts':[{'mediaType':RECEIPT_MODE,'data':{'batchId':row['batch_id'],'executions':executions}}]}); task['status']={'state':'TASK_STATE_COMPLETED'}
        c.execute('UPDATE tasks SET state=?,task_json=?,updated_at=? WHERE id=? AND state=?',('TASK_STATE_COMPLETED',json.dumps(task),now(),tid,'TASK_STATE_INPUT_REQUIRED'))
      c.execute('INSERT INTO messages VALUES(?,?,?,?)',(principal,mid,dg,tid)); c.commit(); c.close()
      return {'task':task}

@app.get('/a2a/tasks/{task_id}')
async def get_task(task_id:str, authorization:str|None=Header(default=None)):
    p=auth(authorization[7:] if authorization and authorization.startswith('Bearer ') else None); c=db(); row=load_task(c,task_id,p); t=json.loads(row['task_json']); c.close(); return t

@app.get('/a2a/tasks')
async def list_tasks(authorization:str|None=Header(default=None)):
    p=auth(authorization[7:] if authorization and authorization.startswith('Bearer ') else None); c=db(); rows=c.execute('SELECT task_json FROM tasks WHERE principal=? ORDER BY created_at DESC',(p,)).fetchall(); c.close(); return {'tasks':[json.loads(r['task_json']) for r in rows]}

@app.post('/a2a/tasks/{task_id}:cancel')
async def cancel(task_id:str, authorization:str|None=Header(default=None)):
    p=auth(authorization[7:] if authorization and authorization.startswith('Bearer ') else None)
    with DB_LOCK:
      c=db(); row=load_task(c,task_id,p); t=json.loads(row['task_json'])
      if row['state']!='TASK_STATE_INPUT_REQUIRED': raise HTTPException(409,'TASK_TERMINAL')
      t['status']={'state':'TASK_STATE_CANCELED'}
      cur=c.execute('UPDATE tasks SET state=?,task_json=?,updated_at=? WHERE id=? AND principal=? AND state=?',('TASK_STATE_CANCELED',json.dumps(t),now(),task_id,p,'TASK_STATE_INPUT_REQUIRED'))
      if cur.rowcount!=1: raise HTTPException(409,'TASK_TERMINAL')
      c.commit(); c.close(); return t

@app.get('/health')
async def health(): return {'status':'ok'}
