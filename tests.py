"""Minimal local smoke test. Run: python tests.py"""
import os, tempfile
os.environ['DB_PATH']=tempfile.mktemp(suffix='.db')
from fastapi.testclient import TestClient
from app.main import app, INPUT, RESULT, A2A
client=TestClient(app)
h={'Authorization':'Bearer agent-test','A2A-Version':'1.0','Content-Type':A2A}
package={'packageId':'pkg-1','vendorName':'Example Ltd','invoiceNumber':'INV-1','amountMinor':12500,'currency':'INR','determination':'Invoice reconciled and within autonomous authority. [REF-A] [REF-B] [REF-C]'}
msg={'messageId':'message-1','role':'ROLE_USER','parts':[{'mediaType':INPUT,'data':{'batchId':'batch-1','policyRevision':'rev-1','packages':[package]}}]}
r=client.post('/a2a/message:send',headers=h,json={'message':msg,'configuration':{'returnImmediately':False}})
assert r.status_code==200 and r.headers['content-type'].startswith(A2A)
task=r.json()['task']; proposal=task['artifacts'][0]['parts'][0]['data']['proposals'][0]
assert task['status']['state']=='TASK_STATE_INPUT_REQUIRED' and len(proposal['evidenceRefs'])==3
replay=client.post('/a2a/message:send',headers=h,json={'message':msg,'configuration':{'returnImmediately':True}})
assert replay.status_code==200 and replay.json()['task']['id']==task['id']
conflict={**msg,'parts':[{'mediaType':INPUT,'data':{'batchId':'batch-1','policyRevision':'other','packages':[package]}}]}
assert client.post('/a2a/message:send',headers=h,json={'message':conflict}).status_code==409
cont={'messageId':'message-2','taskId':task['id'],'contextId':task['contextId'],'role':'ROLE_USER','parts':[{'mediaType':RESULT,'data':{'batchId':'batch-1','results':[{'packageId':'pkg-1','actionId':proposal['actionId'],'action':proposal['action'],'outcome':'ACCEPTED','receiptNonce':'grader-nonce'}]}}]}
done=client.post('/a2a/message:send',headers=h,json={'message':cont})
assert done.status_code==200 and done.json()['task']['status']['state']=='TASK_STATE_COMPLETED'
assert len(done.json()['task']['artifacts'])==2
assert client.post(f"/a2a/tasks/{task['id']}:cancel",headers=h).status_code==409
assert client.get(f"/a2a/tasks/{task['id']}",headers={**h,'Authorization':'Bearer outsider-test'}).status_code==404
print('A2A smoke test passed')
# The opposite terminal path: race a valid continuation against cancellation.
pkg2={**package,'packageId':'pkg-2','invoiceNumber':'INV-2'}
msg2={'messageId':'message-race-create','role':'ROLE_USER','parts':[{'mediaType':INPUT,'data':{'batchId':'batch-race','policyRevision':'rev-1','packages':[pkg2]}}]}
t2=client.post('/a2a/message:send',headers=h,json={'message':msg2}).json()['task']
p2=t2['artifacts'][0]['parts'][0]['data']['proposals'][0]
cont2={'messageId':'message-race-result','taskId':t2['id'],'contextId':t2['contextId'],'role':'ROLE_USER','parts':[{'mediaType':RESULT,'data':{'batchId':'batch-race','results':[{'packageId':'pkg-2','actionId':p2['actionId'],'action':p2['action'],'outcome':'ACCEPTED','receiptNonce':'grader-nonce-2'}]}}]}
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=2) as pool:
    f1=pool.submit(client.post,'/a2a/message:send',headers=h,json={'message':cont2})
    f2=pool.submit(client.post,f"/a2a/tasks/{t2['id']}:cancel",headers=h)
    race_status=sorted([f1.result().status_code,f2.result().status_code])
assert race_status==[200,409],race_status
final=client.get(f"/a2a/tasks/{t2['id']}",headers=h).json()
assert final['status']['state'] in ('TASK_STATE_COMPLETED','TASK_STATE_CANCELED')
if final['status']['state']=='TASK_STATE_CANCELED': assert len(final['artifacts'])==1
else: assert len(final['artifacts'])==2
print('Cancellation/result race test passed')
