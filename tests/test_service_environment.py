"""服务私有 OS 环境仅使用合成文件；不读取开发机 bindings 或真实凭据。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import service_runtime_environment as service

class ServiceEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.path=self.root/'synthetic.json'
        self.values={'AI_STORAGE_KEK_B64':'synthetic-base64-value','AI_STORAGE_KEK_ID':'fixture-v1',
                     'AI_MODELCONTROLPLANE_APIKEY':'synthetic-catalog-value'}
        self.save(self.values)
    def save(self,value):
        self.path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');self.path.chmod(0o600)
    def test_valid_private_json_and_digest_never_expose_values(self):
        values,metadata=service.load_service_environment(self.path,'ai')
        self.assertEqual(values,self.values);self.assertEqual(metadata['sha256'],hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(metadata['keys'],sorted(self.values));self.assertNotIn('synthetic-catalog-value',json.dumps(metadata))
    def test_mode_symlink_hardlink_owner_and_relative_paths_fail_closed(self):
        self.path.chmod(0o644)
        with self.assertRaises(service.EnvironmentError):service.load_service_environment(self.path,'ai')
        self.path.chmod(0o600);link=self.root/'link.json';link.symlink_to(self.path)
        with self.assertRaises(service.EnvironmentError):service.load_service_environment(link,'ai')
        hard=self.root/'hard.json';os.link(self.path,hard)
        with self.assertRaises(service.EnvironmentError):service.load_service_environment(self.path,'ai')
        hard.unlink()
        with patch.object(service.os,'getuid',return_value=os.getuid()+1):
            with self.assertRaises(service.EnvironmentError):service.load_service_environment(self.path,'ai')
        with self.assertRaises(service.EnvironmentError):service.load_service_environment(Path('relative.json'),'ai')
    def test_parent_symlink_and_digest_drift_fail_closed(self):
        directory=self.root/'linked';directory.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(service.EnvironmentError):service.load_service_environment(directory/self.path.name,'ai')
        _,metadata=service.load_service_environment(self.path,'ai');self.save({'AI_STORAGE_KEK_ID':'changed'})
        with self.assertRaises(service.EnvironmentError):service.load_service_environment(self.path,'ai',metadata['sha256'])
    def test_duplicate_unknown_empty_invalid_utf8_and_surrogates_are_rejected(self):
        for raw in [b'{"AI_STORAGE_KEK_ID":"one","AI_STORAGE_KEK_ID":"two"}',b'{}',b'[]',b'{"OTHER_KEY":"secret"}',
                    b'{"AI_STORAGE_KEK_ID":null}',b'{"AI_STORAGE_KEK_ID":true}',b'{"AI_STORAGE_KEK_ID":"\\u0000"}',
                    b'{"AI_STORAGE_KEK_ID":"\\ud800"}',b'\xff']:
            with self.subTest(kind=raw[:20]):
                self.path.write_bytes(raw)
                with self.assertRaises(service.EnvironmentError):service.load_service_environment(self.path,'ai')
    def test_foreign_service_never_receives_ai_keys_and_only_known_model_consumers_allowed(self):
        for name in ['gateway','user','ai-agent','rag','ai-memory','mcp','message','report']:
            with self.assertRaises(service.EnvironmentError):service.load_service_environment(self.path,name)
        self.save({'GITHUB_GOOGLE_API_KEY':'synthetic-agent-model'})
        service.load_service_environment(self.path,'ai-agent')
        with self.assertRaises(service.EnvironmentError):service.load_service_environment(self.path,'rag')
        self.save({'GITHUB_RAG_QWEN_API_KEY':'synthetic-embedding-key'});service.load_service_environment(self.path,'rag')
    def test_native_env_quoted_subset_preserves_unicode_hash_quote_and_backslash_but_rejects_expansion(self):
        text=service.native_env_contents({'AI_STORAGE_KEK_ID':"中文 # literal'\\n"})
        self.assertEqual(text,'AI_STORAGE_KEK_ID="中文 # literal\'\\n"\n')
        for value in ['two\nlines','nul\x00value','quoted"value','$NAME$','${NAME}','$PROJECT_DIR$',' leading',' ','\u00a0leading']:
            with self.assertRaises(service.EnvironmentError):service.native_env_contents({'AI_STORAGE_KEK_ID':value})
    def test_scope_filters_storage_and_models_without_changing_database_or_business_values(self):
        raw={'GITHUB_OPENROUTER_API_KEY':'unassigned-model','GITHUB_MINIO_SECRET_KEY':'storage','GITHUB_OSS_ACCESS_KEY_SECRET':'storage','GITHUB_GOOGLE_API_KEY':'google',
             'GITHUB_QWEN_API_KEY':'qwen','GITHUB_DEEPSEEK_API_KEY':'deepseek','GITHUB_RAG_QWEN_API_KEY':'embedding',
             'GITHUB_RAG_ENGINE_API_KEY':'engine','GITHUB_POSTGRESQL_AI_PASSWORD':'database','GITHUB_AGENT_CLIENT_SECRET':'hmac',
             'GITHUB_CUSTOM_SOURCE_URL':'business'}
        for name in service.SERVICES:
            scoped=service.github_environment(raw,name)
            self.assertNotIn('GITHUB_OPENROUTER_API_KEY',scoped);self.assertNotIn('GITHUB_MINIO_SECRET_KEY',scoped);self.assertNotIn('GITHUB_OSS_ACCESS_KEY_SECRET',scoped)
            self.assertEqual('GITHUB_GOOGLE_API_KEY'in scoped,name=='ai-agent')
            self.assertEqual('GITHUB_RAG_QWEN_API_KEY'in scoped,name=='rag')
            self.assertEqual('GITHUB_RAG_ENGINE_API_KEY'in scoped,name=='rag')
            for key in ['GITHUB_POSTGRESQL_AI_PASSWORD','GITHUB_AGENT_CLIENT_SECRET','GITHUB_CUSTOM_SOURCE_URL']:self.assertEqual(scoped[key],raw[key])
    def test_process_environment_uses_real_child_environment_without_secret_arguments(self):
        source={'PATH':os.defpath,'AI_STORAGE_KEK_B64':'inherited-old','AI_MODELCONTROLPLANE_APIKEY':'inherited-old',
                'GITHUB_MINIO_SECRET_KEY':'inherited-storage','BUSINESS_OPTION':'keep'}
        env=service.process_environment(source,'ai',self.values)
        command=[sys.executable,'-c',"import os; assert __import__('hashlib').sha256(os.environ['AI_STORAGE_KEK_ID'].encode()).hexdigest()=='fc1c0e0c7ae8690bf702ade724d2543f81603a03b3956338f76ddef54ad713b9'; assert 'GITHUB_MINIO_SECRET_KEY' not in os.environ; print('CHILD_SCOPE_OK')"]
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0);self.assertEqual(result.stdout.strip(),'CHILD_SCOPE_OK')
        for name in service.SERVICES:
            if name!='ai':self.assertFalse(any(k.startswith(('AI_STORAGE_','AI_MODELCONTROLPLANE_'))for k in service.process_environment(source,name,{})))
        self.assertFalse(any(v in ' '.join(command)+result.stdout+result.stderr for v in self.values.values()))

if __name__=='__main__':unittest.main()
