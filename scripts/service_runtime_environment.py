"""每服务受限 OS 环境与已退役凭据过滤；不执行 shell，不返回秘密回执。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

SERVICES=frozenset({'gateway','user','ai-agent','ai','ai-memory','rag','mcp','message','report'})
AI_KEYS=frozenset({'AI_STORAGE_KEK_B64','AI_STORAGE_KEK_ID','AI_STORAGE_ALLOWED_ORIGINS',
                  'AI_MODELCONTROLPLANE_APIBASEURL','AI_MODELCONTROLPLANE_APIKEY','AI_MODELCONTROLPLANE_CONSOLEBASEURL'})
SERVICE_KEYS={name:frozenset() for name in SERVICES}
SERVICE_KEYS.update(ai=AI_KEYS,**{
    'ai-agent':frozenset({'GITHUB_GOOGLE_API_KEY','GITHUB_GOOGLE_CHAT_MODEL','GITHUB_GOOGLE_BASE_URL',
                         'GITHUB_DEEPSEEK_API_KEY','GITHUB_DEEPSEEK_BASE_URL','GITHUB_DEEPSEEK_MODEL',
                         'GITHUB_QWEN_API_KEY','GITHUB_QWEN_BASE_URL','GITHUB_QWEN_CHAT_MODEL'}),
    'rag':frozenset({'GITHUB_RAG_QWEN_API_KEY','GITHUB_RAG_QWEN_BASE_URL','GITHUB_RAG_ENGINE_API_KEY','GITHUB_RAG_ENGINE_URL'})})
STORAGE_PREFIXES=('githubminio','githuboss','githubcos','githubs3','githubstorage','githubaliyunoss')
STORAGE_NAMES=frozenset({'githubaliyunaccesskey','githubaliyunaccesskeyid','githubaliyunaccesskeysecret'})
MODEL_SCOPES=(('githubgoogle',frozenset({'ai-agent'})),('githubdeepseek',frozenset({'ai-agent'})),
              ('githubqwen',frozenset({'ai-agent'})),('githubragqwen',frozenset({'rag'})),
              ('githubragengine',frozenset({'rag'})),('githubopenai',frozenset()),
              ('githubanthropic',frozenset()),('githubgemini',frozenset()),('githublitellm',frozenset()),
              ('githubmodel',frozenset()))
REASON_CODES=frozenset({'SERVICE_ENV_ABSOLUTE_PATH_REQUIRED','SERVICE_ENV_SYMLINK_REFUSED','SERVICE_ENV_OPEN_FAILED',
    'SERVICE_ENV_OWNERSHIP_MODE_INVALID','SERVICE_ENV_TOO_LARGE','SERVICE_ENV_JSON_INVALID','SERVICE_ENV_DUPLICATE_KEY',
    'SERVICE_ENV_FIELDS_INVALID','SERVICE_ENV_VALUES_INVALID','SERVICE_ENV_ENCODING_INVALID','SERVICE_ENV_CHANGED',
    'SERVICE_ENV_NATIVE_VALUE_UNSUPPORTED','SERVICE_ENV_SCOPE_INVALID','SERVICE_ENV_BINDING_INVALID'})

class EnvironmentError(ValueError):
    def __init__(self,code):
        self.reason_code=code if code in REASON_CODES else 'SERVICE_ENV_VALUES_INVALID'
        super().__init__(self.reason_code)

def normalized(name):return re.sub(r'[^a-z0-9]','',name.lower())

def sensitive_github_name(name):
    key=normalized(name)
    return key.startswith(STORAGE_PREFIXES)or key in STORAGE_NAMES or (key.startswith('github')and key.endswith('apikey'))or any(key.startswith(prefix)for prefix,_ in MODEL_SCOPES)

def controlled_service_name(name):
    return normalized(name).startswith(('aistorage','aimodelcontrolplane'))or sensitive_github_name(name)

def github_environment(values,service):
    if service not in SERVICES:raise EnvironmentError('SERVICE_ENV_SCOPE_INVALID')
    result={}
    for key,value in values.items():
        if not key.startswith('GITHUB_'):continue
        name=normalized(key)
        if name.startswith(STORAGE_PREFIXES)or name in STORAGE_NAMES:continue
        scope=next((allowed for prefix,allowed in MODEL_SCOPES if name.startswith(prefix)),None)
        if scope is None and name.endswith('apikey'):continue  # 未声明消费者的 API key 不向所有服务广播。
        if scope is None or service in scope:result[key]=value
    return result

def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise EnvironmentError('SERVICE_ENV_DUPLICATE_KEY')
        result[key]=value
    return result

def private_bytes(path,expected=None,allow_system_aliases=False):
    path=Path(path)
    if not path.is_absolute()or any(ord(c)<32 or ord(c)==127 for c in str(path)):
        raise EnvironmentError('SERVICE_ENV_ABSOLUTE_PATH_REQUIRED')
    if any(part.is_symlink()and(part==path or not allow_system_aliases or part.lstat().st_uid!=0)for part in (path,*path.parents)):raise EnvironmentError('SERVICE_ENV_SYMLINK_REFUSED')
    try:fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    except OSError:raise EnvironmentError('SERVICE_ENV_OPEN_FAILED')from None
    with os.fdopen(fd,'rb')as handle:
        before=os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode)or before.st_uid!=os.getuid()or stat.S_IMODE(before.st_mode)!=0o600 or before.st_nlink!=1:
            raise EnvironmentError('SERVICE_ENV_OWNERSHIP_MODE_INVALID')
        raw=handle.read(65537)
        if len(raw)>65536:raise EnvironmentError('SERVICE_ENV_TOO_LARGE')
        after=os.fstat(handle.fileno())
        try:current=path.lstat()
        except OSError:raise EnvironmentError('SERVICE_ENV_CHANGED')from None
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if identity(before)!=identity(after)or identity(before)!=identity(current):raise EnvironmentError('SERVICE_ENV_CHANGED')
    if expected is not None and hashlib.sha256(raw).hexdigest()!=expected:raise EnvironmentError('SERVICE_ENV_CHANGED')
    return raw

def native_env_contents(values):
    # 原生 EnvFileParser 不做 shell unescape；外层双引号保留 #、单引号与反斜线；原生 parser 会 trim 引号后的开头空白，故拒绝。
    # 后续还会执行 $NAME$/IDE macro 展开，所以此受管格式一律拒绝美元符号和双引号。
    for key,value in values.items():
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*',key)or not isinstance(value,str)or not value or len(value)>8192:
            raise EnvironmentError('SERVICE_ENV_VALUES_INVALID')
        if any(ord(c)<32 or ord(c)==127 for c in value):raise EnvironmentError('SERVICE_ENV_VALUES_INVALID')
        if value[0].isspace()or '$'in value or '"'in value:raise EnvironmentError('SERVICE_ENV_NATIVE_VALUE_UNSUPPORTED')
        try:value.encode('utf-8',errors='strict');os.fsencode(value)
        except (UnicodeError,ValueError):raise EnvironmentError('SERVICE_ENV_ENCODING_INVALID')from None
    return ''.join(f'{key}="{values[key]}"\n'for key in sorted(values))

def load_service_environment(path,service,expected=None):
    if service not in SERVICE_KEYS:raise EnvironmentError('SERVICE_ENV_SCOPE_INVALID')
    raw=private_bytes(path,expected)
    try:value=json.loads(raw.decode('utf-8',errors='strict'),object_pairs_hook=_pairs)
    except EnvironmentError:raise
    except (UnicodeError,ValueError):raise EnvironmentError('SERVICE_ENV_JSON_INVALID')from None
    if not isinstance(value,dict)or not value or not set(value)<=SERVICE_KEYS[service]:raise EnvironmentError('SERVICE_ENV_FIELDS_INVALID')
    native_env_contents(value)
    return value,{'path':str(Path(path)),'sha256':hashlib.sha256(raw).hexdigest(),'keys':sorted(value)}

def binding_references(value):
    if not isinstance(value,dict)or not set(value)<=SERVICES or any(not isinstance(v,(str,Path))or not Path(v).is_absolute()for v in value.values()):
        raise EnvironmentError('SERVICE_ENV_BINDING_INVALID')
    return {name:Path(path)for name,path in value.items()}

def parse_references(values):
    result={}
    for item in values or []:
        if '='not in item:raise EnvironmentError('SERVICE_ENV_BINDING_INVALID')
        name,path=item.split('=',1)
        if name in result:raise EnvironmentError('SERVICE_ENV_BINDING_INVALID')
        result[name]=path
    return binding_references(result)

def select_environment(service,references=None,explicit=None):
    refs=binding_references(references or {})
    path=explicit or refs.get(service)
    return load_service_environment(path,service)if path is not None else ({},None)

def process_environment(base,service,overrides):
    if service not in SERVICE_KEYS or not set(overrides)<=SERVICE_KEYS[service]:raise EnvironmentError('SERVICE_ENV_SCOPE_INVALID')
    native_env_contents(overrides)
    clean={k:v for k,v in base.items()if not controlled_service_name(k)}
    clean.update(overrides)
    return clean
