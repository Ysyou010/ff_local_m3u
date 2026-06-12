import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import quote
from flask import Response, stream_with_context

from .setup import P

def _safe_b64encode(text):
    return urlsafe_b64encode(str(text).encode('utf-8')).decode('utf-8').rstrip('=')

def _safe_b64decode(text):
    padding = '=' * (-len(str(text)) % 4)
    return urlsafe_b64decode((str(text) + padding).encode('utf-8')).decode('utf-8')

def get_media_files():
    media_path = P.ModelSetting.get("media_path")
    ext_setting = P.ModelSetting.get("extensions")
    valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
    
    file_list = []
    if not os.path.isdir(media_path):
        return file_list

    for file_name in os.listdir(media_path):
        if file_name.lower().endswith(valid_exts):
            file_list.append(file_name)
            
    return sorted(file_list)

def make_m3u(req):
    media_path = P.ModelSetting.get("media_path")
    files = get_media_files()
    
    # 서버의 Base URL 추출 (포트 포함)
    host_url = req.host_url.rstrip('/')
    
    lines = ["#EXTM3U\n"]
    for index, file_name in enumerate(files, 1):
        encoded_name = _safe_b64encode(file_name)
        # FFmpeg 스트림 카피 라우트로 연결
        play_url = f"{host_url}/{P.package_name}/api/play/ffmpeg/{encoded_name}"
        
        lines.append(f'#EXTINF:-1 tvg-name="{file_name}" tvg-chno="{index}",{file_name}\n{play_url}\n')
        
    return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")

def play_ffmpeg_copy(encoded_name):
    try:
        file_name = _safe_b64decode(encoded_name)
        media_path = P.ModelSetting.get("media_path")
        full_path = os.path.join(media_path, file_name)
        
        if not os.path.isfile(full_path):
            return Response("File not found", status=404)

        # -re 옵션: 로컬 파일을 원래 재생 속도에 맞춰 읽음 (스트리밍 시 필수)
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
        
        P.logger.info(f"FFmpeg Play Start: {file_name}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        
        @stream_with_context
        def generate():
            try:
                while True:
                    # 188바이트(MPEG-TS 패킷 크기)의 배수로 청크를 읽어 전송
                    chunk = proc.stdout.read(188 * 32)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.kill()
                P.logger.info(f"FFmpeg Play End: {file_name}")

        return Response(generate(), mimetype="video/MP2T")
        
    except Exception as e:
        P.logger.error(f"Exception: {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
