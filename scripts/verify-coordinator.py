"""Live publisher-funded Minimax planning and agent-funded mail triage in a temporary workspace.
Google mail is a labelled fixture; no real inbox is read and no email is sent.
"""
from __future__ import annotations
import json
import argparse
import os
import secrets
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from mavo.config import Settings
from mavo.coordinator_client import CoordinatorClient
from mavo.credentials import DpapiCredentialStore,MemoryCredentialStore
from mavo.schemas import CoordinatorDecision, CrewDraft
from mavo.database import CrewInstance
from mavo.firebase_auth import FirebaseIdentity
from mavo.main import create_app
from mavo.publisher import publisher_google_config

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--triage-only",action="store_true",help="Replay the last real Minimax setup and test only the agent model")
    args=parser.parse_args()
    real_dir=Path(os.environ['LOCALAPPDATA'])/'dev.mavo.desktop'
    real_settings=Settings()
    with sqlite3.connect((real_dir/'mavo.db').as_uri()+'?mode=ro',uri=True) as db:
        model,credential_id=db.execute("SELECT model,credential_id FROM model_routes WHERE provider='airouter' ORDER BY created_at LIMIT 1").fetchone()
    protected=DpapiCredentialStore(real_dir)
    agent_key=protected.get(credential_id)
    report=json.loads((ROOT/".build/coordinator-live-verification.json").read_text(encoding="utf-8")) if args.triage_only else {}
    with TemporaryDirectory(prefix='coordinator-live-',dir=ROOT/'.build') as directory:
        settings=Settings(require_login=False,workers_enabled=False,data_dir=Path(directory),api_token=secrets.token_urlsafe(32),core_gateway_url='http://127.0.0.1:8741')
        with TestClient(create_app(settings,credentials=MemoryCredentialStore())) as client:
            client.headers['Authorization']='Bearer '+settings.api_token
            runtime=client.app.state.crew_runtime
            identity=FirebaseIdentity(runtime.factory,protected,publisher_google_config(real_settings.google_client_id,real_settings.google_client_secret))
            runtime.planner.coordinator=CoordinatorClient(settings,identity)
            route=client.post('/api/v1/routes',json={'name':'Agent under test','provider':'airouter','model':model,'api_key':agent_key}).json()
            session_id=str(uuid4())
            def turn(message,crew_id=None):
                data={'message':message,'request_id':str(uuid4()),'session_id':session_id,'crew_id':crew_id}
                response=client.post('/api/v1/core/turn',json=data);response.raise_for_status()
                result=response.json()
                report.setdefault('turns',[]).append(result)
                (ROOT/'.build/coordinator-live-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                return result,data
            if args.triage_only:
                draft={**report['turns'][-1]['crew']['configuration_plan']['draft'],'route_id':route['id']}
                with runtime.factory() as db:
                    created=runtime.planner.apply_plan(db,CoordinatorDecision(reply='Replay the previously verified live setup',crew_proposal=CrewDraft.model_validate(draft)),None)
                    db.commit();crew_id=created.id
                second={'crew':client.get('/api/v1/crew').json()[0]}
            else:
                first,payload=turn('I need a Crew that shows me important emails while filtering out spam and promotions.')
                assert first['crew'] and not first['crew']['is_running']
                crew_id=first['crew']['id']
                assert first['crew']['permissions']==['model:invoke']
                repeated=client.post('/api/v1/core/turn',json=payload).json()
                assert repeated['crew']['id']==crew_id and len(client.get('/api/v1/crew').json())==1
                second,_=turn('For this test college.edu is the exact real sender domain, not all .edu senders. Important means exams or assignment submissions with an explicitly stated deadline within 48 hours; ignore messages without a deadline. Use computer local time. Ignore advertisements and newsletters. Show a desktop update. Only read and notify; do not send or change mail, calendars, or tasks. These criteria are complete; please save the configuration.',crew_id)
                if second['crew']['configuration_plan']['questions']:
                    second,_=turn('Use the exact college.edu domain and computer local time. Ignore messages without an explicit deadline and ignore borderline messages. Only deadlines in the next 48 hours matter. Show one short desktop notification per matching message. Read-only, no sending. Please use these defaults for any remaining detail and finish setup.',crew_id)
                assert second['crew']['id']==crew_id
                assert second['model']=='minimax/minimax-m3.1-flash'
                assert second['crew']['configuration_plan']['draft']['trigger_type']=='gmail_message'
                assert not second['crew']['configuration_plan']['questions'],second['crew']['configuration_plan']['questions']
            caps=second['crew']['configuration_plan']['draft']['requested_google_capabilities']
            caps=sorted(set(caps)|{'gmail:read'})
            assert set(caps)<={'gmail:read','gmail:body'}
            runtime.planner.google.status=lambda:{'capabilities':['gmail:read','gmail:body']}
            response=client.post(f'/api/v1/crew/{crew_id}/access',json={'capabilities':caps,'actions':{},'notifications':True,'route_id':route['id']})
            response.raise_for_status();active=response.json()
            assert active['is_running'] and active['schedule_time'] is None
            report['planning']='Publisher-funded Minimax created and refined one Crew, requested minimal access, and activated the mail event trigger'
            print('Previously verified Minimax plan replayed for isolated agent testing' if args.triage_only else 'Minimax automatic Crew creation, refinement, access, and idempotency: PASS',flush=True)
            provider_chat=runtime.router.chat
            async def capture(*values,**options):
                raw=await provider_chat(*values,**options)
                report.setdefault('agent_outputs',[]).append(raw)
                (ROOT/'.build/coordinator-live-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                return raw
            runtime.router.chat=capture
            current={}
            async def snapshot(resource):return {'items':[current]}
            async def body(message_id):return {'id':message_id,'body':current['body']}
            runtime.google_pool.read=snapshot
            runtime.google_pool.message_body=body
            async def event(crew_id):
                with runtime.factory() as db:
                    generation=db.get(CrewInstance,crew_id).runtime_generation
                return {'resource':'gmail','subject':'fixture-account','generation':generation,'cursor':current['cursor'],'events':[current]}
            runtime.prepare_event=event
            current.update(id='urgent-1',cursor=1,sender='instructor@college.edu',subject='URGENT: assignment submission due tomorrow',labels=['INBOX'],body='Your college assignment must be submitted tomorrow at 9 AM. This is an academic deadline within 48 hours.')
            client.portal.call(runtime.work,crew_id)
            runs=client.get(f'/api/v1/crew-runs?crew_id={crew_id}').json()
            assert runs[0]['status']=='completed',runs[0].get('error')
            notices=client.get('/api/v1/reminders/due').json()
            assert len(notices)==1 and notices[0]['kind']=='crew_update',runs[0]['result']
            current.clear();current.update(id='promotion-2',cursor=2,sender='offers@shopping.example',subject='Weekend sale: 40 percent off clothing',labels=['INBOX','CATEGORY_PROMOTIONS'],body='Shop our clothing sale. This is an advertisement, unrelated to college exams or submissions.')
            client.portal.call(runtime.work,crew_id)
            runs=client.get(f'/api/v1/crew-runs?crew_id={crew_id}').json()
            assert runs[0]['status']=='completed',runs[0].get('error')
            assert len(client.get('/api/v1/reminders/due').json())==1,runs[0]['result']
            report['triage']='Agent-funded AIRouter work highlighted a synthetic urgent college message and kept synthetic promotional mail quiet'
            report['agent_model']=model
            report['mail_source']='Synthetic fixtures; no real inbox read or outbound mail'
            (ROOT/'.build/coordinator-live-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print('Agent connection isolation and important/promotional mail triage: PASS',flush=True)


if __name__=='__main__':main()
