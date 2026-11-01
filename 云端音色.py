"""三家云端 TTS 的模型设置、音色创建和查询。"""
import base64
import re
import time
from pathlib import Path

MODELS = {
 '阿里百炼': ['qwen3-tts-flash', 'qwen-audio-3.1-tts-flash', 'qwen-audio-3.1-tts-plus', 'cosyvoice-v3.5-flash', 'cosyvoice-v3.5-plus'],
 'MiniMax': ['speech-2.8-turbo', 'speech-2.8-hd'],
 '小米 MiMo': ['mimo-v2.5-tts', 'mimo-v2.5-tts-voicedesign', 'mimo-v2.5-tts-voiceclone'],
}
MODES = ['系统音色', '已有音色 ID', '参考音频克隆', '文字设计声音']

def ali_base(options):
    region = options.get('region', '北京')
    workspace = options.get('workspace', '').strip()
    if workspace and not re.fullmatch(r'[A-Za-z0-9-]+', workspace):
        raise ValueError('业务空间 ID 格式错误')
    host = (workspace + ('.cn-beijing.maas.aliyuncs.com' if region == '北京' else '.ap-southeast-1.maas.aliyuncs.com')) if workspace else ('dashscope.aliyuncs.com' if region == '北京' else 'dashscope-intl.aliyuncs.com')
    return 'https://' + host + '/api/v1'

def reference(path):
    file = Path(path)
    if not file.is_file() or file.suffix.lower() not in ('.mp3', '.wav'):
        raise ValueError('请选择 MP3 或 WAV 参考音频')
    if file.stat().st_size > 7_500_000:
        raise ValueError('参考音频过大，请使用小于 7.5 MB 的片段')
    mime = 'audio/wav' if file.suffix.lower() == '.wav' else 'audio/mpeg'
    return 'data:' + mime + ';base64,' + base64.b64encode(file.read_bytes()).decode()

def response_json(response):
    from 语音链路 import checked
    data = checked(response).json()
    if data.get('base_resp', {}).get('status_code', 0):
        raise ValueError('服务商拒绝请求，请检查音色权限、余额及所选模型')
    return data

def voices(provider, options, key=''):
    from 语音链路 import http, credentials
    if provider == '小米 MiMo':
        from 语音链路 import VOICES
        return VOICES[provider]
    with http() as session:
        headers = credentials(provider, key)
        if provider == 'MiniMax':
            data = response_json(session.post('https://api.minimaxi.com/v1/get_voice', headers=headers, json={'voice_type': 'all'}, timeout=30))
            return [item['voice_id'] for group in ('system_voice', 'voice_cloning', 'voice_generation') for item in data.get(group, [])]
        result = []
        for page in range(100):
            data = response_json(session.post(ali_base(options) + '/services/audio/tts/customization', headers=headers, json={'model': 'voice-enrollment', 'input': {'action': 'list_voice', 'page_size': 100, 'page_index': page}}, timeout=30))
            items = data.get('output', {}).get('voice_list', [])
            result.extend(item['voice_id'] for item in items if item.get('voice_id') and item.get('status', 'OK') == 'OK')
            if len(items) < 100:
                return result
        raise ValueError('音色列表过大，请在控制台筛选后填写音色 ID')

def create(provider, options, key=''):
    from 语音链路 import http, credentials
    mode = options.get('mode')
    if mode not in ('参考音频克隆', '文字设计声音'):
        raise ValueError('请选择克隆或声音设计模式')
    if provider == '小米 MiMo':
        if mode == '参考音频克隆':
            reference(options.get('reference', ''))
        elif not options.get('instructions', '').strip():
            raise ValueError('请填写声音描述')
        return ''  # MiMo 在合成时传入样本或描述，不创建持久音色号。
    preview = options.get('preview', '').strip()
    if not preview:
        raise ValueError('请填写试听文字')
    with http() as session:
        headers = credentials(provider, key)
        if provider == 'MiniMax':
            if mode == '文字设计声音':
                description = options.get('instructions', '').strip()
                if not description:
                    raise ValueError('请填写声音描述')
                data = response_json(session.post('https://api.minimaxi.com/v1/voice_design', headers=headers, json={'prompt': description, 'preview_text': preview}, timeout=120))
                return data['voice_id']
            reference(options.get('reference', ''))
            name = options.get('name', '').strip()
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{7,255}', name):
                raise ValueError('MiniMax 音色 ID 请使用字母开头的至少 8 位字母、数字、下划线或连字符')
            with open(options['reference'], 'rb') as audio:
                uploaded = response_json(session.post('https://api.minimaxi.com/v1/files/upload', headers=headers, data={'purpose': 'voice_clone'}, files={'file': audio}, timeout=90))
            payload = {'file_id': uploaded['file']['file_id'], 'voice_id': name, 'text': preview, 'model': options['model'], 'need_noise_reduction': options.get('denoise', False), 'need_volume_normalization': options.get('normalize', False)}
            response_json(session.post('https://api.minimaxi.com/v1/voice_clone', headers=headers, json=payload, timeout=120))
            return name
        if options['model'] == 'qwen3-tts-flash':
            raise ValueError('请改选 Qwen-Audio 3.1 或 CosyVoice 3.5 后创建自定义音色')
        name = options.get('name', '').strip()
        if not re.fullmatch(r'[A-Za-z0-9]{1,10}', name):
            raise ValueError('百炼音色名称前缀须为 1–10 位英文字母或数字')
        body = {'action': 'create_voice', 'target_model': options['model'], 'prefix': name}
        if mode == '参考音频克隆':
            body['audio'] = reference(options.get('reference', ''))
        else:
            if not options.get('instructions', '').strip() or not 15 <= len(preview) <= 200:
                raise ValueError('请填写声音描述，试听文字需为 15–200 字')
            body.update(voice_prompt=options['instructions'], preview_text=preview, language_hints=['zh'])
        url = ali_base(options) + '/services/audio/tts/customization'
        data = response_json(session.post(url, headers=headers, json={'model': 'voice-enrollment', 'input': body}, timeout=120))
        voice_id = data['output']['voice_id']
        for _ in range(60):
            data = response_json(session.post(url, headers=headers, json={'model': 'voice-enrollment', 'input': {'action': 'query_voice', 'voice_id': voice_id}}, timeout=20))
            status = data['output'].get('status')
            if status == 'OK':
                return voice_id
            if status != 'DEPLOYING':
                raise ValueError('音色未通过审核，状态：' + str(status) + '；音色号：' + voice_id)
            time.sleep(1)
        raise TimeoutError('音色仍在处理，可稍后刷新音色列表；音色号：' + voice_id)
