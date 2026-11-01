import json
import requests

PRESETS = {
 'asr': {
  '本地 · 小鲸鱼 ASR': ('http://127.0.0.1:8000/v1/audio/transcriptions', 'large-v3'),
  '阿里百炼': ('https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation', 'qwen3-asr-flash'),
  '小米 MiMo': ('https://api.xiaomimimo.com/v1/chat/completions', 'mimo-v2.5-asr'),
  'MiniMax（尚未核实 ASR）': ('', ''),
 },
 'tts': {
  '本地 · 小鲸鱼音色': ('http://127.0.0.1:9880', '现有本地音色'),
  'Windows 本地语音': ('sapi', '系统中文语音'),
  '阿里百炼': ('https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation', 'qwen3-tts-flash'),
  'MiniMax': ('https://api.minimaxi.com/v1/t2a_v2', 'speech-2.8-turbo'),
  '小米 MiMo': ('https://api.xiaomimimo.com/v1/chat/completions', 'mimo-v2.5-tts'),
 },
}

def discover():
 base, live = '', []
 with requests.Session() as session:
  session.trust_env = False
  try:
   r = session.get('http://127.0.0.1:19387/dsh-tts-bridge/where', timeout=.7)
   r.raise_for_status()
   candidate = r.json()['base'].rstrip('/')
   from urllib.parse import urlparse
   if urlparse(candidate).hostname in ('localhost', '127.0.0.1', '::1'):
    r = session.get(candidate + '/sessions', timeout=2)
    r.raise_for_status()
    base, live = candidate, json.loads(r.content.decode('utf-8'))['sessions']
  except (requests.RequestException, ValueError, KeyError):
   pass
  if not base:
   import psutil
   owners = {p.pid for p in psutil.process_iter(['name']) if 'deepseek harness' in (p.info['name'] or '').lower()}
   ports = {c.laddr.port for c in psutil.net_connections('tcp') if c.status == 'LISTEN' and c.pid in owners}
   # DSH 的网页服务常常跑在子进程里（进程名不带 .exe，或 psutil 认不到 pid），
   # 只按进程名匹配会漏掉，这里再用 netstat 把本地回环上所有监听端口补一遍。
   import subprocess
   try:
    listing = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True, text=True, timeout=15, creationflags=0x08000000).stdout
   except (OSError, subprocess.SubprocessError):
    listing = ''
   for line in listing.splitlines():
    parts = line.split()
    if len(parts) >= 4 and parts[3] == 'LISTENING' and parts[1].startswith(('127.0.0.1:', '[::1]:')):
     try:
      ports.add(int(parts[1].rsplit(':', 1)[1]))
     except ValueError:
      continue
   for port in sorted(ports):
    candidate = f'http://127.0.0.1:{port}/whale-pet'
    try:
     r = session.get(candidate + '/status', timeout=.4)
     if r.ok and r.json().get('bridge') == 'whale-pet':
      base = candidate
      break
    except (requests.RequestException, ValueError):
     continue
  from pathlib import Path
  home = Path.home() / '.dsh'
  workspace = json.loads((home / 'storages/workspace.json').read_text(encoding='utf-8'))
  archived = set(workspace.get('global', {}).get('archivedSessionIds', []))
  listed = {item['id']: item for item in live}
  for group in workspace.get('tables', {}).get('workspaces', {}).values():
   for sid in group.get('sessionIds', []):
    if sid in listed:
     continue
    cached = home / 'storages/session_projcache/sessions' / (sid + '.json')
    try:
     record = json.loads(cached.read_text(encoding='utf-8'))['record']
     title = record.get('rows', {}).get('title', {}).get('val', '')
     if title:
      listed[sid] = {'id': sid, 'title': ('[已归档] ' if sid in archived else '') + title, 'archived': sid in archived}
    except (OSError, ValueError, KeyError):
     continue
  return base, list(listed.values())

def voice_endpoint(base):
 if not base:
  raise RuntimeError('DSH 已打开，但桌宠连接插件尚未加载；历史对话仍可选择绑定。')
 return base if base.endswith('/whale-pet') else base.removesuffix('/dsh-tts-bridge') + '/dsh-voice-loop'
