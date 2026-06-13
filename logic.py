import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect
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

# 🌟 업데이트: 딕셔너리 형태로 제목과 경로를 분리해서 반환
def get_media_files():
    media_path_raw = P.ModelSetting.get("media_path")
    if not media_path_raw:
        return []
        
    ext_setting = P.ModelSetting.get("extensions")
    valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
    
    file_list = []
    paths = [p.strip() for p in media_path_raw.split('\n') if p.strip()]
    
    for line in paths:
        # 제목|경로 형태인지 검사
        if '|' in line:
            parts = line.split('|', 1)
            title = parts[0].strip()
            path = parts[1].strip()
        else:
            path = line.strip()
            title = ""
        
        # 제목이 없으면 자동 부여
        if not title:
            if path.startswith("http"):
                title = "YouTube Stream"
            else:
                title = os.path.basename(path)
                
        # 경로 검증 후 리스트 추가
        if path.startswith("http://") or path.startswith("https://"):
            file_list.append({"title": title, "path": path})
        elif os.path.isfile(path):
            if path.lower().endswith(valid_exts):
                file_list.append({"title": title, "path": path.replace('\\', '/')})
        else:
            P.logger.error(f"[로컬 M3U] 파일이 없거나 폴더입니다: {path}")
            
    return file_list

def get_media_list(req):
    files = get_media_files()
    result = []
    
    for idx, item in enumerate(files, 1):
        encoded_name = _safe_b64encode(item['path']) # 경로는 그대로 암호화
        play_url = get_api_url(req, "play", {"file": encoded_name})
        
        display_name = item['title']
        if display_name == "YouTube Stream":
            display_name = f"YouTube Stream [{idx}]"
        
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
        
        for index, item in enumerate(files, 1):
            encoded_name = _safe_b64encode(item['path'])
            play_url = get_api_url(req, "play", {"file": encoded_name})
            
            display_name = item['title']
            if display_name == "YouTube Stream":
                display_name = f"YouTube Stream [{index}]"
            
            # 여기서 지정된 제목(display_name)이 m3u의 채널명으로 들어갑니다!
            lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n{play_url}\n')
            
        return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return Response(f"make_m3u 생성 중 에러: {str(e)}", status=500)

def play_ffmpeg_copy(encoded_name):
    try:
        P.logger.info("========== [재생 준비 단계] ==========")
        full_path = _safe_b64decode(encoded_name)
        P.logger.info(f"-> 최종 재생 시도 경로: {full_path}")
        
        if full_path.startswith("http://") or full_path.startswith("https://"):
            P.logger.info("-> yt-dlp 원본 추출 시도")
            try:
                import yt_dlp
                ydl_opts = {'format': 'best/best[ext=mp4]', 'quiet': True, 'noplaylist': True}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    stream_url = info.get('url', None)
                    if stream_url:
                        P.logger.info("-> [성공] 스트림 주소 리다이렉트")
                        return redirect(stream_url, code=302)
                    else:
                        return Response("유튜브 스트림 추출 실패", status=500)
            except Exception as e:
                P.logger.error(f"-> [실패] 유튜브 추출 중 에러: {e}")
                P.logger.error(traceback.format_exc())
                return Response(f"유튜브 에러: {e}", status=500)

        if not os.path.isfile(full_path):
            P.logger.error(f"-> [실패] 실제 파일이 존재하지 않습니다!!")
            return Response(f"File not found: {full_path}", status=404)

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-re", "-i", full_path,
            "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "copy",
            "-muxdelay", "0", "-f", "mpegts", "-"
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
        P.logger.error(f"재생 처리 에러: {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
