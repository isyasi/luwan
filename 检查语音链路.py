"""不录音、不发送 DSH 消息、不调用付费服务的自检。"""
import io
from unittest.mock import patch
import numpy as np
import soundfile as sf
import 语音链路 as v

assert v.device('系统默认') is None
assert v.device('12: USB 麦克风') == 12
assert v.simplified('你好，這是臺灣的語音識別測試。') == '你好，这是台湾的语音识别测试。'
assert v.simplified('简体中文、API 123 😀') == '简体中文、API 123 😀'
audio = v.wav_bytes(np.zeros((1600, 1), np.float32), 16000)
data, rate = sf.read(io.BytesIO(audio))
assert rate == 16000 and len(data) == 1600

class Reply:
    ok = True
    status_code = 200
    def json(self):
        return {'ok': True, 'text': '测试识别', 'choices': [{'message': {'content': '测试识别'}}]}

class Client:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, url, **kwargs): return Reply()
    def post(self, url, **kwargs):
        self.url, self.body = url, kwargs
        return Reply()

client = Client()
with patch.object(v, 'http', return_value=client), patch.object(v, 'credentials', return_value={}):
    assert v.transcribe('本地 · 小鲸鱼 ASR', audio) == '测试识别'
    assert 'files' in client.body
    assert v.transcribe('阿里百炼', audio, 'test') == '测试识别'
    assert client.body['json']['messages'][0]['content'][0]['input_audio']['data'].startswith('data:audio/wav;base64,')
    with patch.object(v, 'discover', return_value=('http://127.0.0.1:1234/dsh-tts-bridge', [{'id': 'selected'}])):
        v.send('测试发送', 'selected')
        assert client.body['json'] == {'text': '测试发送', 'sessionId': 'selected'}
        try:
            v.send('不可误发', 'missing')
            raise AssertionError('应拒绝不存在的会话')
        except ValueError:
            pass
print('语音链路自检通过')

# 验证三家的请求格式；不发送网络请求。
class AudioReply:
    ok = True
    content = audio
    def __init__(self, data=None): self.data = data or {}
    def json(self): return self.data

class AudioClient(Client):
    def post(self, url, **kwargs):
        self.url, self.body = url, kwargs
        provider_model = kwargs['json']['model']
        if provider_model.startswith('mimo'):
            import base64
            return AudioReply({'choices': [{'message': {'audio': {'data': base64.b64encode(audio).decode()}}}]})
        if provider_model.startswith('speech'):
            return AudioReply({'data': {'audio': audio.hex()}, 'base_resp': {'status_code': 0}})
        return AudioReply({'output': {'audio': {'url': 'https://example.com/test.wav'}}})
    def get(self, url, **kwargs): return AudioReply()

client = AudioClient()
with patch.object(v, 'http', return_value=client), patch.object(v, 'credentials', return_value={}):
    assert v.synthesize('小米 MiMo', '你好', '冰糖', 'test', {}) == audio
    assert client.body['json']['messages'][-1] == {'role': 'assistant', 'content': '你好'}
    assert v.synthesize('MiniMax', '你好', 'custom_voice', 'test', {'model': 'speech-2.8-hd', 'speed': '1.2'}) == audio
    assert client.body['json']['voice_setting']['speed'] == 1.2
    assert v.synthesize('阿里百炼', '你好', 'custom_voice', 'test', {'model': 'qwen-audio-3.1-tts-flash'}) == audio
    assert client.url.endswith('/services/audio/tts/SpeechSynthesizer')

# 开口打断必须中止播放器，不再写入音频。
import threading
class Output:
    aborted = False
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def abort(self): Output.aborted = True
    def write(self, data): raise AssertionError('取消后不应继续播放')
cancel = threading.Event()
cancel.set()
with patch.object(v.sd, 'OutputStream', Output), patch.object(v.sd, 'query_devices', return_value={'default_samplerate': 16000, 'max_output_channels': 2}):
    assert v.play(audio, '系统默认', cancel) is False
    assert Output.aborted
print('云端请求格式与播放打断自检通过')
gate = v.InterruptGate()
quiet = np.full(512, .01, np.float32)  # -40 dBFS
loud = np.full(512, .1, np.float32)  # -20 dBFS
assert not any(gate.accept(True, quiet, -30, True) for _ in range(10))
assert not gate.accept(False, loud, -30, True)
assert not gate.accept(True, loud, -30, True)
assert not gate.accept(True, loud, -30, True)
assert gate.accept(True, loud, -30, True)
assert gate.accept(True, quiet, -30, False)
assert not any(gate.accept(True, loud, -10, True) for _ in range(10))
print('人声、音量及持续时间打断判定通过')
