import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context
from framework import SystemModelSetting
from .setup import P

def get_apikey():
    try:
        if SystemModelSetting.get_bool("use_apikey"):
            return str(SystemModelSetting.get("apikey") or "").strip()
    except:
        pass
    return ""

def get_base_url(req):
    try:
        return req.url_root.rstrip("/")
    except:
        return ""

def get_api_url(req, endpoint, params=None):
    if params is None:
        params = {}
    apikey = get_apikey()
    if apikey:
        params['apikey'] = apikey
        
    base = get_base_url(req)
    query = urlencode(params)
    url = f"{base}/{P.package_name}/api/{endpoint}" if base else f"/{P.package_name}/api/{endpoint}"
    return f"{url}?{query}" if query else url

def _safe_b64encode(text):
    return urlsafe_b64encode(str(text).encode('utf-8')).decode('utf-8').rstrip('=')

def _safe_b64decode(text):
    padding = '=' * (-len(str(text)) % 4)
    return urlsafe_b64decode((str(text) + padding).encode('utf-8')).decode('utf-8')

def get_media_files():
    media_path_raw = P.ModelSetting.get("media_path")
    if not media_path_raw:
        return []
        
    ext_setting = P.ModelSetting.get("extensions")
    valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
    
    file_list = []
    # 입력된 텍스트를 줄바꿈(엔터) 단위로 분리합니다.
    paths = [p.strip() for p in media_path_raw.split('\n') if p.strip()]
    
    for path in paths:
        # 폴더 스캔 삭제: 오직 지정된 파일만 검사합니다.
        if os.path.isfile(path):
            if path.lower().endswith(valid_exts):
                file_list.append(path.replace('\\', '/'))
        else:
            P.logger.error(f"[로컬 M3U] 파일이 존재하지 않거나 폴더 경로입니다 (무시됨): {path}")
            
    return file_list

def get_media_list(req):
    files = get_media_files()
    result = []
    
    for idx, full_path in enumerate(files, 1):
        # 이제 기준 폴더가 없으므로 절대 경로 전체를 인코딩합니다.
        encoded_name = _safe_b64encode(full_path)
        play_url = get_api_url(req, "play", {"file": encoded_name})
        
        # 목록 화면에는 전체 경로가 아닌 파일 이름만 깔끔하게 출력
        display_name = os.path.basename(full_path)
        
        result.append({
            "idx": idx,
            "name": display_name,
            "url": play_url
        })
        
    return result

def make_m3u(req):
    try:
        files = get_media_files()
        lines = ["#EXTM3U\n"]
        
        for index, full_path in enumerate(files, 1):
            encoded_name = _safe_b64encode(full_path)
            play_url = get_api_url(req, "play", {"file": encoded_name})
            display_name = os.path.basename(full_path)
            
            lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n{play_url}\n')
            
        return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return Response(f"make_m3u 생성 중 에러: {str(e)}", status=500)

def play_ffmpeg_copy(encoded_name):
    try:
        P.logger.info("========== [FFmpeg 재생 준비 단계] ==========")
        # 인코딩된 문자열 자체가 완벽한 절대 경로입니다.
        full_path = _safe_b64decode(encoded_name)
        P.logger.info(f"-> 암호 해독된 최종 재생 시도 경로: {full_path}")
        
        if not os.path.isfile(full_path):
            P.logger.error(f"-> [실패] 실제 파일이 존재하지 않습니다!! (404 Error)")
            return Response(f"File not found: {full_path}", status=404)

        cmd = [
            "ffmpeg", 
            "-hide_banner", 
            "-loglevel", "warning",
            "-re", 
            "-i", full_path,
            "-map", "0:v:0?", 
            "-map", "0:a:0?",
            "-c:v", "copy", 
            "-c:a", "copy",
            "-muxdelay", "0",
            "-f", "mpegts",
            "-"
        ]
        
        P.logger.info(f"FFmpeg 전송 시작: {full_path}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        
        @stream_with_context
        def generate():
            try:
                while True:
                    chunk = proc.stdout.read(188 * 32)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.kill()
                P.logger.info(f"FFmpeg 전송 종료: {full_path}")

        return Response(generate(), mimetype="video/MP2T")
        
    except Exception as e:
        P.logger.error(f"재생 처리 중 에러 발생: {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
