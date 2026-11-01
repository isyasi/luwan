"""桌宠录音、识别、指定设备播报与 DSH 提交。"""
import base64
import io
import json
import os
import queue
import socket
import time
import wave
from pathlib import Path
import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
from 语音预设 import PRESETS, discover, voice_endpoint

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / '语音设置.json'
STATE = ROOT / '监听状态.json'
VOICES = {
 '本地 · 小鲸鱼音色': ['服务当前音色'],
 'Windows 本地语音': ['系统默认'],
 '阿里百炼': ['Cherry'],
 'MiniMax': ['Chinese (Mandarin)_Sweet_Girl', 'male-qn-qingse'],
 '小米 MiMo': ['冰糖', '茉莉', '苏打', '白桦', 'Mia', 'Chloe', 'Milo', 'Dean'],
}

def device(value):
    return None if not value or value == '系统默认' else int(value.split(':', 1)[0])

def http():
    s = requests.Session()
    s.trust_env = False
    return s

def checked(response):
    if not response.ok:
        raise RuntimeError(f'接口返回 HTTP {response.status_code}，请检查服务状态、密钥和模型权限')
    return response

def credentials(provider, key=''):
    env = {'阿里百炼': 'DASHSCOPE_API_KEY', 'MiniMax': 'MINIMAX_API_KEY', '小米 MiMo': 'MIMO_API_KEY'}.get(provider)
    key = key or (os.environ.get(env, '') if env else '')
    if env and not key:
        from 密钥存储 import read
        key = read(provider)
    if env and not key:
        raise ValueError('请在测试面板填写该服务商 API Key')
    if provider.startswith('本地'):
        p = Path(r'C:\asr\api_key.txt')
        key = p.read_text(encoding='utf-8-sig').strip() if p.exists() else ''
    return {'Authorization': 'Bearer ' + key} if key else {}

def wav_bytes(samples, rate):
    buffer = io.BytesIO()
    sf.write(buffer, samples, rate, format='WAV', subtype='PCM_16')
    return buffer.getvalue()

class InterruptGate:
    """人声、数字音量与持续时间共同判定打断。"""
    def __init__(self):
        self.hits = 0

    def accept(self, speech, samples, threshold, busy):
        if not busy:
            self.hits = 0
            return speech
        db = 20 * np.log10(max(float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))), 1e-8))
        threshold = max(-60., min(-10., float(threshold)))
        self.hits = self.hits + 1 if speech and db >= threshold else 0
        if self.hits >= 3:  # 3 × 32ms，过滤短促敲击、瞬时峰值。
            self.hits = 0
            return True
        return False

def simplified(text):
    """使用 Windows 内置字形映射统一为简体，不改写识别内容。"""
    import ctypes
    mapping = ctypes.WinDLL('kernel32', use_last_error=True).LCMapStringEx
    mapping.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
    mapping.restype = ctypes.c_int
    size = mapping('zh-CN', 0x02000000, text, -1, None, 0, None, None, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    output = ctypes.create_unicode_buffer(size)
    if not mapping('zh-CN', 0x02000000, text, -1, output, size, None, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return output.value

def record(microphone, seconds=5):
    index = device(microphone)
    rate = int(sd.query_devices(index, 'input')['default_samplerate'])
    samples = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype='float32', device=index)
    sd.wait()
    return wav_bytes(samples, rate)

def transcribe(provider, audio, key=''):
    url, model = PRESETS['asr'][provider]
    if not url:
        raise ValueError('所选识别来源尚不可用')
    with http() as s:
        headers = credentials(provider, key)
        if provider.startswith('本地'):
            r = s.post(url, headers=headers, files={'file': ('speech.wav', audio, 'audio/wav')}, data={'language': 'zh', 'prompt': '以下是简体中文普通话，请使用简体中文转写。'}, timeout=(5, 90))
            text = checked(r).json().get('text', '')
        else:
            encoded = 'data:audio/wav;base64,' + base64.b64encode(audio).decode()
            url = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions' if provider == '阿里百炼' else url
            payload = {'model': model, 'messages': [{'role': 'user', 'content': [{'type': 'input_audio', 'input_audio': {'data': encoded, 'format': 'wav'}}]}], 'stream': False}
            r = s.post(url, headers=headers, json=payload, timeout=(5, 90))
            text = checked(r).json()['choices'][0]['message']['content']
    if not isinstance(text, str) or not text.strip():
        raise ValueError('未识别到文字，请检查麦克风或重新录音')
    return simplified(text.strip())

def apply_playback_speed(audio: bytes, speed: float) -> bytes:
    """Time-compress/expand WAV/PCM audio without calling streaming TTS. Pitch will shift slightly (resample)."""
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return audio
    if not audio or abs(speed - 1.0) < 0.02:
        return audio
    speed = max(0.5, min(2.0, speed))
    samples, rate = sf.read(io.BytesIO(audio), dtype="float32", always_2d=True)
    import math
    from scipy.signal import resample_poly
    # play faster => fewer samples at same rate
    up, down = 100, max(1, int(round(100 * speed)))
    g = math.gcd(up, down)
    up, down = up // g, down // g
    samples = resample_poly(samples, up, down)
    return wav_bytes(samples, rate)


def synthesize(provider, text, voice, key='', options=None):
    url, model = PRESETS['tts'][provider]
    if options is None:
        stored = json.loads(CONFIG.read_text(encoding='utf-8-sig')) if CONFIG.exists() else {}
        options = stored.get('tts_profiles', {}).get(provider, {})
    model = options.get('model') or model
    voice = options.get('voice') or voice
    with http() as s:
        if provider.startswith('本地'):
            references = list(Path(r'E:\ai\dshzbj\models\paimon-gpt-sovits-v2').glob('说话-*.wav'))
            if not references:
                raise ValueError('缺少本地派蒙参考音频（paimon-gpt-sovits-v2）')
            payload = {'text': text, 'text_lang': 'zh', 'media_type': 'wav', 'ref_audio_path': str(references[0]), 'prompt_text': '既然罗莎莉亚说足迹上有元素力，用元素视野应该能很清楚地看到吧。', 'prompt_lang': 'zh', 'streaming_mode': False}
            try:
                r = s.post(url + '/tts', json=payload, timeout=(5, 120))
            except requests.exceptions.ConnectionError:
                raise ValueError('本地音色服务连不上（9880）。请只开一个 gpt-sovits，或改用阿里百炼 TTS')
            except requests.exceptions.Timeout:
                raise ValueError('本地音色超时（9880）。可改用阿里百炼 TTS')
            if not r.ok:
                raise ValueError(f'本地音色失败 HTTP {r.status_code}（请确认只开一个 api_v2）')
            return checked(r).content
        if provider == 'Windows 本地语音':
            import subprocess
            import tempfile
            with tempfile.TemporaryDirectory(prefix='whale-tts-') as folder:
                target = Path(folder) / 'speech.wav'
                quote = lambda value: "'" + str(value).replace("'", "''") + "'"
                command = 'Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SetOutputToWaveFile(' + quote(target) + '); $s.Speak(' + quote(text) + '); $s.Dispose()'
                encoded = base64.b64encode(command.encode('utf-16-le')).decode()
                result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded], capture_output=True, timeout=60, creationflags=0x08000000)
                if result.returncode or not target.exists():
                    raise ValueError('Windows 语音合成失败，请检查是否安装中文语音包')
                return target.read_bytes()
        headers = credentials(provider, key)
        if provider == '阿里百炼':
            from 云端音色 import ali_base
            base = ali_base(options)
            try:
                speech_rate = float(options.get('speed', 1.2) or 1.2)
            except (TypeError, ValueError):
                speech_rate = 1.2
            speech_rate = max(0.5, min(2.0, speech_rate))
            if model == 'qwen3-tts-flash':
                url = base + '/services/aigc/multimodal-generation/generation'
                # 官方非实时文档未列语速；仍写入 speech_rate，若被忽略则下方本地变速兜底
                payload = {'model': model, 'input': {'text': text, 'voice': voice, 'language_type': 'Chinese', 'enable_audio_encoder_output': False, 'speech_rate': speech_rate}, 'parameters': {'speech_rate': speech_rate}}
            else:
                url = base + '/services/audio/tts/SpeechSynthesizer'
                payload = {'model': model, 'input': {'text': text, 'voice': voice, 'format': 'wav', 'sample_rate': 24000}, 'parameters': {'speech_rate': speech_rate}}
        elif provider == 'MiniMax':
            setting = {'voice_id': voice, 'speed': float(options.get('speed', 1)), 'vol': float(options.get('volume', 1)), 'pitch': int(options.get('pitch', 0))}
            if options.get('emotion') and options['emotion'] != '自动':
                setting['emotion'] = options['emotion']
            payload = {'model': model, 'text': text, 'stream': False, 'voice_setting': setting, 'audio_setting': {'format': 'wav', 'sample_rate': 32000, 'channel': 1}}
        else:
            from 云端音色 import reference
            audio_settings = {'format': 'wav'}
            mode = options.get('mode', '系统音色')
            if mode == '参考音频克隆':
                model = 'mimo-v2.5-tts-voiceclone'
                audio_settings['voice'] = reference(options.get('reference', ''))
            elif mode == '文字设计声音':
                model = 'mimo-v2.5-tts-voicedesign'
                if not options.get('instructions', '').strip():
                    raise ValueError('请填写声音描述')
            else:
                audio_settings['voice'] = voice
            messages = []
            if options.get('instructions'):
                messages.append({'role': 'user', 'content': options['instructions']})
            messages.append({'role': 'assistant', 'content': text})
            payload = {'model': model, 'messages': messages, 'audio': audio_settings}
        r = checked(s.post(url, json=payload, headers=headers, timeout=(5, 120)))
        data = r.json()
        if provider == 'MiniMax':
            if data.get('base_resp', {}).get('status_code', 0):
                raise ValueError('MiniMax 合成失败，请检查密钥、额度或音色权限')
            return bytes.fromhex(data['data']['audio'])
        if provider == '小米 MiMo':
            return base64.b64decode(data['choices'][0]['message']['audio']['data'])
        audio_url = data['output']['audio']['url']
        from urllib.parse import urlparse
        parsed = urlparse(audio_url)
        # 百炼当前会返回阿里云 OSS 的 http 预签名地址；仅对官方结果域名升级为 https。
        if parsed.scheme == 'http' and (parsed.hostname or '').startswith('dashscope-result-') and (parsed.hostname or '').endswith('.aliyuncs.com'):
            audio_url = parsed._replace(scheme='https').geturl()
            parsed = urlparse(audio_url)
        if parsed.scheme != 'https':
            raise ValueError('合成服务返回了不安全的音频地址')
        audio = checked(s.get(audio_url, timeout=45)).content
        if provider == '阿里百炼':
            try:
                speed = float(options.get('speed', 1.2) or 1.2)
            except (TypeError, ValueError):
                speed = 1.2
            audio = apply_playback_speed(audio, speed)
        return audio

def play(audio, speaker, cancel=None):
    samples, rate = sf.read(io.BytesIO(audio), dtype='float32', always_2d=True)
    try:
        index = device(speaker)
        info = sd.query_devices(index, 'output')
    except Exception as err:
        raise ValueError(f'播放设备不可用（speaker={speaker!r}）：{err}') from err
    target_rate = int(info['default_samplerate'])
    if rate != target_rate:
        from scipy.signal import resample_poly
        import math
        divisor = math.gcd(rate, target_rate)
        samples = resample_poly(samples, target_rate // divisor, rate // divisor)
    channels = min(samples.shape[1], int(info['max_output_channels']))
    with sd.OutputStream(samplerate=target_rate, device=index, channels=channels, dtype='float32') as stream:
        for offset in range(0, len(samples), 1024):
            if cancel is not None and cancel.is_set():
                stream.abort()
                return False
            stream.write(np.ascontiguousarray(samples[offset:offset+1024, :channels]))
    return True

def _with_screen_state(text):
    """bridge 优先注入；若文本尚未带 CURRENT_SCREEN_STATE 则兜底前缀一次。"""
    if not text or '[CURRENT_SCREEN_STATE]' in text:
        return text
    try:
        from 屏幕视觉 import format_context_block
        block = format_context_block()
    except Exception:
        block = ''
    if not block:
        return text
    return block + '\n\n' + text

def send(text, session_id):
    if not session_id:
        raise ValueError('请先保存绑定的 DSH 对话')
    base, sessions = discover()
    if session_id not in {s['id'] for s in sessions}:
        raise ValueError('绑定的 DSH 对话已不可用，请重新选择')
    url = voice_endpoint(base) + '/send'
    outbound = _with_screen_state(text)
    with http() as s:
        ready = s.get(url.removesuffix('/send') + '/status', timeout=5)
        if ready.status_code in (404, 405):
            raise RuntimeError('DSH 发送插件尚未加载，请重启 DSH 后重试')
        checked(ready)
        data = checked(s.post(url, json={'text': outbound, 'sessionId': session_id}, timeout=(5, 180))).json()
    if not data.get('ok'):
        raise RuntimeError('DSH 未接收消息：' + str(data.get('error', '未知错误')))

def state(message, **extra):
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps({'status': message, 'time': time.time(), **extra}, ensure_ascii=False), encoding='utf-8')
    temporary.replace(STATE)

def reply_snapshot(session_id):
    import re
    import zstandard
    if not re.fullmatch(r'session-[a-zA-Z0-9-]+', session_id):
        raise ValueError('会话 ID 格式错误')
    files = list((Path.home() / '.dsh/sessions').glob('*/' + session_id + '/session.v4.jsonl.zstd'))
    if not files:
        return 0, ''
    latest, text = 0, ''
    with files[0].open('rb') as raw:
        with io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(raw), encoding='utf-8') as reader:
            for line in reader:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('type') == 'assistant/message':
                    parts = event.get('data', {}).get('message', {}).get('content', [])
                    spoken = ''.join(part.get('text', '') for part in parts if isinstance(part, dict) and part.get('type') == 'text')
                    if spoken.strip():
                        latest, text = event.get('seq', 0), spoken.strip()
    return latest, text

def stream_reply(session_id, previous=0, cancel=None):
    """轮询桥接插件的流式接口，拿这一轮已生成的全部回复文本。

    返回生成器：每当文本比上一次更长就 yield (text, finished)。
    调用方拿到第一段即可开合成，后续每变长一段就续念一段；
    finished 为 True 表示这一轮彻底结束（turn/end completed）。

    previous：发消息前该会话最新的 assistant 回复 seq，用于回退时过滤旧回复。

    兼容性：若 DSH 桥接插件尚未升级出 /reply 接口（重启 DSH 前），
    自动回退到旧的磁盘轮询逻辑，保证无论是否重启都能出声。
    """
    base, _ = discover()
    url = voice_endpoint(base).removesuffix('/send') + '/reply'
    deadline = time.monotonic() + 180
    last = ''
    fallback = False
    with http() as s:
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                return
            if not fallback:
                try:
                    r = s.get(url, params={'sessionId': session_id}, timeout=5)
                except Exception:
                    time.sleep(.22)
                    continue
                if r.status_code in (404, 405):
                    # 桥接插件还是旧版，没有 /reply；回退到磁盘轮询。
                    fallback = True
                    continue
                data = checked(r).json()
                text = (data.get('text') or '').strip()
                finished = bool(data.get('finished'))
                if text and text != last:
                    last = text
                    yield text, finished
                if finished:
                    if text and text == last:
                        yield text, True
                    return
                time.sleep(.22)
                continue
            # 旧逻辑：轮询磁盘会话缓存，等 turnBoundary 关闭。
            seq, text = reply_snapshot(session_id)
            cache = Path.home() / '.dsh/storages/session_projcache/sessions' / (session_id + '.json')
            try:
                record = json.loads(cache.read_text(encoding='utf-8'))['record']['rows']
                finished = record.get('turnBoundary', {}).get('val', {}).get('openTurnStartSeq') is None
            except (OSError, ValueError, KeyError):
                finished = False
            # 严格还原旧语义：只有「出现了比 previous 更新的回复」且「整轮已结束」才念。
            if seq > previous and finished and text:
                last = text
                yield text, True
                return
            time.sleep(.22)
    raise TimeoutError('DSH 已接收消息，但尚未等到完整回复')


def wait_reply(session_id, previous, cancel=None):
    """兼容入口：等这一轮完整回复。prefer 用 stream_reply 做流式。"""
    for text, finished in stream_reply(session_id, previous, cancel):
        if finished:
            return text
    return ''

def listen():
    # ponytail: 单实例使用本机端口锁；多个桌宠实例需独立分配端口。
    lock = socket.socket()
    try:
        lock.bind(('127.0.0.1', 19389))
    except OSError:
        return False
    import onnxruntime as ort
    model = Path.home() / '.dsh/local-plugins/dsh-voice-loop/models/silero_vad.onnx'
    vad = ort.InferenceSession(str(model), providers=['CPUExecutionProvider'])
    h = np.zeros((2, 1, 64), dtype=np.float32)
    c = h.copy()
    config = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    base, _ = discover()
    with http() as session:
        ready = session.get(voice_endpoint(base) + '/status', timeout=5)
    if ready.status_code != 200:
        state('DSH 发送插件未加载，请重启 DSH。麦克风尚未开启。')
        lock.close()
        return
    index = device(config.get('microphone'))
    rate = int(sd.query_devices(index, 'input')['default_samplerate'])
    from scipy.signal import resample_poly
    import math
    divisor = math.gcd(rate, 16000)
    incoming = queue.Queue(maxsize=300)
    import threading
    jobs = queue.Queue(maxsize=1)
    stopped = threading.Event()
    reply_busy = threading.Event()
    interrupt_gate = InterruptGate()
    current_cancel = [threading.Event()]
    state_lock = threading.Lock()
    def report(cancel, message, **extra):
        with state_lock:
            if not cancel.is_set() and not stopped.is_set():
                state(message, **extra)
    def dialogue_worker():
        while not stopped.is_set():
            try:
                audio, config, cancel = jobs.get(timeout=.4)
            except queue.Empty:
                continue
            try:
                if cancel.is_set():
                    continue
                reply_busy.set()
                report(cancel, '正在识别')
                text = transcribe(config['asr_provider'], wav_bytes(audio, 16000))
                if cancel.is_set():
                    continue
                sid = config.get('conversation_id', '')
                previous, _ = reply_snapshot(sid)
                report(cancel, '正在发送到绑定对话', text=text)
                send(text, sid)
                report(cancel, 'DSH 已接收，等待回复', text=text)
                voice = config.get('voice') or VOICES[config['tts_provider']][0]
                speaker = config.get('speaker')
                # 流式首句即读：拿到第一段文字就立刻合成播报，后续每变长一段续念尾巴。
                spoken_len = 0
                for answer, finished in stream_reply(sid, previous, cancel):
                    if cancel.is_set():
                        break
                    if not answer:
                        continue
                    tail = answer[spoken_len:]
                    if tail.strip():
                        report(cancel, '正在合成回复', text=text)
                        try:
                            output = synthesize(config['tts_provider'], tail, voice)
                        except Exception as err:
                            report(cancel, '合成失败：' + str(err), text=text)
                            break
                        if cancel.is_set():
                            break
                        report(cancel, '正在播报回复', text=text)
                        try:
                            played = play(output, speaker, cancel)
                        except Exception as err:
                            report(cancel, '播放失败：' + str(err), text=text)
                            break
                        if not played:
                            break
                        spoken_len = len(answer)
                    if finished:
                        break
                report(cancel, '正在听你说话', text=text)
            except Exception as error:
                report(cancel, '本轮失败：' + str(error))
            finally:
                reply_busy.clear()
                jobs.task_done()
    threading.Thread(target=dialogue_worker, daemon=True).start()
    def callback(data, frames, timing, status):
        try:
            incoming.put_nowait(data.copy())
        except queue.Full:
            pass
    pending = np.zeros(0, np.float32)
    utterance, silence, active = [], 0, False
    try:
        with sd.InputStream(device=index, samplerate=rate, channels=1, blocksize=int(rate * .032), callback=callback):
            state('正在听你说话')
            while True:
                config = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
                if not config.get('vad_enabled'):
                    break
                try:
                    chunk = incoming.get(timeout=.5).reshape(-1)
                except queue.Empty:
                    continue
                pending = np.concatenate((pending, resample_poly(chunk, 16000 // divisor, rate // divisor)))
                while len(pending) >= 512:
                    block, pending = pending[:512], pending[512:]
                    probability, h, c = vad.run(None, {'input': block[None, :].astype(np.float32), 'sr': np.array(16000, np.int64), 'h': h, 'c': c})
                    speech = float(probability[0][0]) >= .5
                    if not active and not interrupt_gate.accept(speech, block, config.get('interrupt_db', -30), reply_busy.is_set()):
                        continue
                    if speech:
                        if not active:
                            with state_lock:
                                current_cancel[0].set()
                                current_cancel[0] = threading.Event()
                                state('人声音量达到门槛，已打断，等待你说完…' if reply_busy.is_set() else '听到你说话，等待你说完…')
                        active, silence = True, 0
                    if active:
                        utterance.append(block)
                        silence = 0 if speech else silence + 1
                    if active and (silence >= 15 or len(utterance) >= 938):
                        audio = np.concatenate(utterance)
                        utterance, active, silence = [], False, 0
                        if len(audio) < 8000:
                            continue
                        try:
                            jobs.put_nowait((audio, config, current_cancel[0]))
                        except queue.Full:
                            try:
                                old = jobs.get_nowait()
                                old[2].set()
                                jobs.task_done()
                            except queue.Empty:
                                pass
                            jobs.put_nowait((audio, config, current_cancel[0]))
    except Exception as error:
        state('监听失败：' + str(error))
    finally:
        stopped.set()
        current_cancel[0].set()
        lock.close()

if __name__ == '__main__':
    while True:
        try:
            config = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
            if not config.get('vad_enabled'):
                state('监听已关闭')
                break
            if listen() is False:
                break
        except Exception as error:
            # 不把带请求细节的异常直接暴露到界面，避免泄露服务凭据。
            state('连接暂不可用，请确认 DSH 和本地语音服务已打开；稍后自动重试。')
        time.sleep(3)
