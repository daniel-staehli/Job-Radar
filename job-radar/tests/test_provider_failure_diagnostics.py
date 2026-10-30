"""Provider failure evidence never retains response text or reasoning traces."""
import json
import httpx
import pytest
from job_radar import providers
from test_ollama_provider import Document, settings, completed

@pytest.mark.parametrize('change,reason,phrase',[
    ({'done_reason':'length'},'output_limit','generation or context limit'),
    ({'done':False},'incomplete','did not report'),
    ({'message':{'content':'','thinking':'PRIVATE THINKING'}},'empty_content','reasoning but no document'),
    ({'message':{'content':'','refusal':'PRIVATE REFUSAL'}},'refusal','declined'),
    ({'error':'PRIVATE ERROR'},'service_error','generation error'),
    ({'message':{'tool_calls':[{'private':'tool'}]}},'unexpected_tool_call','tool request'),
    ({'message':[]},'malformed_response','unexpected response structure'),
    ({'message':{'content':'{"wrong":"PRIVATE CONTENT"}'}},'schema_validation','required document schema'),
])
def test_provider_failure_diagnostics_native_reason_and_request_budget(tmp_path,change,reason,phrase):
    raw=completed(**change)
    fn=providers.resolve_response_fn(settings(),transport=httpx.MockTransport(lambda req:httpx.Response(200,json=raw)))
    with pytest.raises(providers.ProviderError,match=phrase):
        fn(schema_type=Document,schema_name='test',system='SYSTEM PRIVATE',user='PROFILE PRIVATE',
           use_web_search=False,artifact_dir=tmp_path)
    text=(tmp_path/'provider_failure.json').read_text(encoding='utf-8')
    saved=json.loads(text)
    assert saved['failure_reason']==reason
    assert saved['request_limits']=={'context_tokens':32768,'output_tokens':16384,'timeout_seconds':600}
    assert saved['counts']['prompt_eval_count']==30
    for secret in ['PRIVATE','Grounded','private reasoning trace','synthetic-model']:
        assert secret not in text
    assert sorted(p.name for p in tmp_path.iterdir())==['provider_failure.json']

def test_provider_failure_diagnostics_bounds_and_whitelists_every_field(tmp_path):
    raw={'done':True,'done_reason':'SECRET REASON','status':'SECRET STATUS','message':
         {'content':'SECRET CONTENT','thinking':'SECRET THOUGHT','refusal':'SECRET REFUSAL'},
         'error':'SECRET ERROR','prompt_eval_count':True,'eval_count':-1,
         'total_duration':10**20,'usage':{'secret':'SECRET KEY','input_tokens':123}}
    providers._save_response_failure(tmp_path,'ollama',raw,'not-safe',
        request_limits={'context_tokens':32768,'output_tokens':True,'timeout_seconds':-1,'secret':'SECRET'})
    text=(tmp_path/'provider_failure.json').read_text(encoding='utf-8');saved=json.loads(text)
    assert 'SECRET' not in text
    assert saved['failure_reason']==saved['stop_reason']=='unknown'
    assert saved['counts']=={'input_tokens':123}
    assert saved['request_limits']=={'context_tokens':32768}
    assert saved['content_chars']==14

def test_provider_failure_diagnostics_io_failure_preserves_provider_error(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path,'write_text',lambda *a,**kw:(_ for _ in ()).throw(PermissionError('private path')))
    fn=providers.resolve_response_fn(settings(),transport=httpx.MockTransport(
        lambda req:httpx.Response(200,json=completed(done_reason='length'))))
    with pytest.raises(providers.ProviderError,match='generation or context limit'):
        fn(schema_type=Document,schema_name='test',system='facts',user='facts',use_web_search=False,artifact_dir=tmp_path)

@pytest.mark.parametrize('provider,raw',[
    ('openai',{'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'}}),
    ('openai_compatible',{'choices':[{'finish_reason':'length','message':{}}]}),
    ('anthropic',{'stop_reason':'max_tokens'}),
    ('gemini',{'candidates':[{'finishReason':'MAX_TOKENS'}]}),
])
def test_provider_failure_diagnostics_limit_explanations_across_adapters(provider,raw):
    with pytest.raises(providers.ProviderError,match='generation or context limit') as caught:
        providers._response_text(provider,raw)
    assert caught.value.reason=='output_limit'

def test_provider_failure_diagnostics_success_does_not_create_failure_file(tmp_path):
    fn=providers.resolve_response_fn(settings(),transport=httpx.MockTransport(
        lambda req:httpx.Response(200,json=completed())))
    doc,raw=fn(schema_type=Document,schema_name='test',system='facts',user='facts',use_web_search=False,artifact_dir=tmp_path)
    assert doc.answer=='Grounded'
    assert not (tmp_path/'provider_failure.json').exists()
    assert 'private reasoning trace' not in json.dumps(raw)
