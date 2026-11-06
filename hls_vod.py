import os
import re
import shutil
import hashlib
import subprocess
import threading
import time
import traceback
from flask import Response, send_file
from .setup import P

try:
    from .setup import F
    _PATH_DATA = F.config['path_data']
except Exception:
    _PATH_DATA = '/data'

# 로컬 mkv 등을 HLS(fmp4 조각)로 변환해서 구간 이동이 가능하도록 한다.
# 비디오는 복사, 오디오만 AAC로 변환하므로 변환 속도가 빠르다. (실시간의 수십 배)
HLS_ROOT = os.path.join(_PATH_DATA, 'tmp', 'ff_local_m3u_hls')
SEGMENT_TIME = 6
START_TIMEOUT = 30          # 첫 조각이 나올 때까지 최대 대기 시간(초)
MAX_RUNNING = 2             # 동시에 변환하는 최대 개수 (초과 시 가장 오래 안 본 것 중지)
IDLE_DELETE = 3 * 3600      # 마지막 재생 후 이 시간이 지나면 변환 파일 삭제(초)
SEGMENT_RE = re.compile(r'^(init\.mp4|seg_\d{5}\.m4s)$')
KEY_RE = re.compile(r'^[0-9a-f]{16}$')

_jobs = {}
_lock = threading.Lock()


def _key(full_path):
    return hashlib.md5(full_path.encode('utf-8')).hexdigest()[:16]


def _dir(key):
    return os.path.join(HLS_ROOT, key)


def _playlist_path(key):
    return os.path.join(_dir(key), 'index.m3u8')


def _read_playlist(key):
    try:
        with open(_playlist_path(key), 'r', encoding='utf-8') as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return ''


def _is_complete(key):
    return '#EXT-X-ENDLIST' in _read_playlist(key)


def _is_running(job):
    return job.get('proc') is not None and job['proc'].poll() is None


def _touch(key):
    job = _jobs.get(key)
    if job:
        job['last_access'] = time.time()


def _stop_job(key, remove_files=True):
    job = _jobs.pop(key, None)
    if job and _is_running(job):
        try:
            job['proc'].kill()
        except Exception:
            pass
    if remove_files:
        shutil.rmtree(_dir(key), ignore_errors=True)


def _cleanup_locked():
    now = time.time()
    if not os.path.isdir(HLS_ROOT):
        return
    for key in os.listdir(HLS_ROOT):
        directory = _dir(key)
        job = _jobs.get(key)
        if job and _is_running(job):
            continue
        last_access = job['last_access'] if job else os.path.getmtime(directory)
        if now - last_access > IDLE_DELETE:
            P.logger.info(f"[HLS] 오래된 변환 파일 삭제: {key}")
            _stop_job(key)


def cleanup():
    with _lock:
        try:
            _cleanup_locked()
        except Exception:
            P.logger.error(traceback.format_exc())


def _video_codec(ffmpeg_path, full_path):
    ffprobe = os.path.join(os.path.dirname(ffmpeg_path), 'ffprobe')
    if not os.path.exists(ffprobe):
        ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return ''
    try:
        result = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v:0',
                                 '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', full_path],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        return result.stdout.decode('utf-8', 'ignore').strip()
    except Exception:
        return ''


def _start_locked(key, full_path, ffmpeg_path):
    # 동시 변환 개수 제한: 가장 오래 안 본 변환부터 중지
    running = sorted([k for k, j in _jobs.items() if _is_running(j)], key=lambda k: _jobs[k]['last_access'])
    while len(running) >= MAX_RUNNING:
        old = running.pop(0)
        P.logger.info(f"[HLS] 동시 변환 제한으로 이전 변환 중지: {_jobs[old]['path']}")
        _stop_job(old)

    directory = _dir(key)
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory, exist_ok=True)

    cmd = [ffmpeg_path, '-hide_banner', '-loglevel', 'warning', '-y',
           '-i', full_path,
           '-map', '0:v:0?', '-map', '0:a:0?',
           '-c:v', 'copy']
    if _video_codec(ffmpeg_path, full_path) == 'hevc':
        # 애플 기기/사파리는 hvc1 태그여야 HEVC를 재생한다
        cmd += ['-tag:v', 'hvc1']
    cmd += ['-c:a', 'aac', '-ac', '2', '-b:a', '192k',
            '-f', 'hls', '-hls_time', str(SEGMENT_TIME), '-hls_playlist_type', 'event',
            '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4',
            '-hls_segment_filename', os.path.join(directory, 'seg_%05d.m4s'),
            _playlist_path(key)]

    log_file = open(os.path.join(directory, 'ffmpeg.log'), 'wb')
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log_file)
    log_file.close()
    _jobs[key] = {'proc': proc, 'path': full_path, 'last_access': time.time()}
    P.logger.info(f"[HLS] 변환 시작: {full_path}")


def start(full_path, ffmpeg_path):
    """변환을 시작(또는 기존 변환 재사용)하고 첫 조각이 준비되면 key를 반환. 실패 시 None"""
    key = _key(full_path)
    with _lock:
        _cleanup_locked()
        job = _jobs.get(key)
        if job and (_is_running(job) or _is_complete(key)):
            _touch(key)
        elif _is_complete(key):
            # 플러그인 재시작 전에 끝난 변환 결과 재사용
            _jobs[key] = {'proc': None, 'path': full_path, 'last_access': time.time()}
            P.logger.info(f"[HLS] 기존 변환 결과 재사용: {full_path}")
        else:
            _start_locked(key, full_path, ffmpeg_path)
        job = _jobs[key]

    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if '#EXTINF' in _read_playlist(key):
            return key
        if not _is_running(job):
            break
        time.sleep(0.3)

    try:
        with open(os.path.join(_dir(key), 'ffmpeg.log'), 'r', encoding='utf-8', errors='ignore') as f:
            log = f.read()[-1000:]
    except OSError:
        log = ''
    P.logger.error(f"[HLS] 변환 시작 실패: {full_path}\n{log}")
    with _lock:
        _stop_job(key)
    return None


def playlist(key, segment_url):
    """index.m3u8을 읽어서 조각 주소를 API 주소(apikey 포함)로 바꿔서 반환"""
    if not KEY_RE.match(key or ''):
        return Response("잘못된 key", status=400)
    text = _read_playlist(key)
    if not text:
        return Response("변환 정보 없음. 다시 재생해 주세요.", status=404)
    _touch(key)

    lines = []
    for line in text.splitlines():
        if line.startswith('#EXT-X-MAP:'):
            line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{segment_url(m.group(1))}"', line)
        elif line and not line.startswith('#'):
            line = segment_url(line.strip())
        lines.append(line)
        if line == '#EXTM3U' and '#EXT-X-ENDLIST' not in text:
            # 변환 중(EVENT)일 때 플레이어가 라이브처럼 끝 지점부터 재생하지 않도록 처음부터 시작 지시
            lines.append('#EXT-X-START:TIME-OFFSET=0')

    response = Response("\n".join(lines) + "\n", mimetype="application/vnd.apple.mpegurl")
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


def segment(key, name):
    if not KEY_RE.match(key or '') or not SEGMENT_RE.match(name or ''):
        return Response("잘못된 요청", status=400)
    path = os.path.join(_dir(key), name)
    if not os.path.isfile(path):
        return Response("조각 없음", status=404)
    _touch(key)
    response = send_file(path, mimetype='video/mp4', conditional=True)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response
