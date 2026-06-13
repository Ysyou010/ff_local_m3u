import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect, send_file
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

def get_media_files(target_category=None):
    try:
        media_path_raw = P.ModelSetting.get("media_path")
        if not media_path_raw:
            return []
            
        ext_setting = P.ModelSetting.get("extensions")
        valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
        
        file_list = []
        paths = [p.strip() for p in media_path_raw.split('\n') if p.strip()]
        
        for line in paths:
            parts = line.split('|')
            if len(parts) >= 4:
                category = parts[0].strip()
                title = parts[1].strip()
                quality = parts[2].strip()
                path = "|".join(parts[3:]).strip()
            elif len(parts) == 3:
                category = parts[0].strip()
                title = parts[1].strip()
                quality = "자동"
                path = "|".join(parts[2:]).strip()
            elif len(parts) == 2:
                category = "기본"
                title = parts[0].strip()
                quality = "자동"
                path = parts[1].strip()
            else:
                category = "기본"
                title = ""
                quality = "자동"
                path = line.strip()
                
            if not title:
                title = "YouTube Stream" if path.startswith("http") else os.path.basename(path)

            if target_category and target_category != 'all' and category != target_category:
                continue
                
            if path.startswith("http://") or path.startswith("https://"):
                file_list.append({"category": category, "title": title, "quality": quality, "path": path})
            elif os.path.isfile(path):
                if path.lower().endswith(valid_exts):
                    file_list.append({"category": category, "title": title, "quality": quality, "path": path.replace('\\', '/')})
                    
        return file_list
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return []

def get_media_list(req):
    try:
        files = get_media_files('all')
        result = []
        for idx, item in enumerate(files, 1):
            encoded_payload = f"{item['quality']}||{item['path']}"
            encoded_name = _safe_b64encode(encoded_payload)
            
            # 안드로이드 인식용 가짜 확장자
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mp4'
            if not ext: ext = '.mp4'
            
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
            display_name = f"[{item['category']}] {item['title']}"
            result.append({
                "idx": idx,
                "name": display_name,
                "url": play_url
            })
        return result
    except Exception as e:
        P.logger.error(traceback.format_exc())
        raise e

def make_m3u(req):
    try:
        target_category = req.args.get('id', 'all')
        files = get_media_files(target_category)
        lines = ["#EXTM3U\n"]
        
        for index, item in enumerate(files, 1):
            encoded_payload = f"{item['quality']}||{item['path']}"
            encoded_name = _safe_b64encode(encoded_payload)
            
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mp4'
            if not ext: ext = '.mp4'
            
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
            display_name = item['title']
            lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n{play_url}\n')
            
        return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return Response(f"make_m3u 에러: {str(e)}", status=500)

def play_ffmpeg_copy(encoded_name):
    try:
        full_str = _safe_b64decode(encoded_name)
        if "||" in full_str:
            quality, full_path = full_str.split("||", 1)
        else:
            quality = "자동"
            full_path = full_str
            
        # ==========================================
        # 1. 로컬 파일: 에러 요소를 완전히 제거한 100% 순정 코드
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
                
            P.logger.info(f"[재생 시작] 로컬 파일 순정 다이렉트 전송: {full_path}")
            # 이 단 한 줄이 안드로이드 탐색(Range)과 버퍼링을 가장 완벽하게 처리합니다.
            return send_file(full_path, conditional=True)
            
        # ==========================================
        # 2. 유튜브 처리: 크래시 유발 1080p 합성 폐기, 720p 다이렉트 고정
        # ==========================================
        P.logger.info(f"[재생 요청] YouTube: {full_path}")
        
        try:
            import yt_dlp
            
            # 안드로이드가 절대 에러를 뿜지 않는 '화면+소리 합본' 최대 화질(720p) MP4만 강제로 뽑아옵니다.
            format_str = 'best[height<=720][ext=mp4]/best[ext=mp4]/best'
            ydl_opts = {'format': format_str, 'quiet': True, 'noplaylist': True}
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(full_path, download=False)
                is_live = info.get('is_live', False)
                stream_url = info.get('url') or info.get('manifest_url')
                
                if not stream_url:
                    return Response("스트림 주소 추출 실패", status=500)
                
                # 🌟 일반 영상(VOD): 플레이어에게 유튜브 원본 주소를 바로 넘김 (속도 1위, 탐색 완벽)
                if not is_live:
                    P.logger.info(f"[재생 시작] YouTube VOD 302 다이렉트 리다이렉트")
                    return redirect(stream_url, code=302)
                
                # 🌟 라이브 방송(Live): 이것만 서버가 중계합니다.
                P.logger.info(f"[재생 시작] YouTube 라이브 중계 가동")
                user_agent = info.get('http_headers', {}).get('User-Agent', "Mozilla/5.0")
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                cmd.extend(["-user_agent", user_agent, "-i", stream_url])
                cmd.extend(["-map", "0:v:0?", "-map", "0:a:0?"])
                cmd.extend(["-c:v", "copy", "-c:a", "copy", "-muxdelay", "0", "-f", "mpegts", "-"])
                
        except Exception as e:
            P.logger.error(f"[재생 실패] 유튜브 에러: {e}")
            return Response(f"유튜브 에러: {e}", status=500)
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        
        @stream_with_context
        def generate_live():
            try:
                while True:
                    chunk = proc.stdout.read(188 * 32)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.kill()

        return Response(generate_live(), mimetype="video/MP2T")

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        return Response("Playback Error", status=500)
