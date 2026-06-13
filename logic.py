import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect, send_file, request
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
            
            # 안드로이드 플레이어 인식을 위한 가짜 확장자 추가
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
            
            # 안드로이드 플레이어 인식을 위한 가짜 확장자 추가
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
        # 1. 로컬 파일: 에러 유발 코드 전면 삭제 (순정 복구)
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
            
            P.logger.info(f"[재생 시작] 로컬 파일 다이렉트 전송: {full_path}")
            # ★ 가장 처음 앞뒤 넘김이 완벽하게 작동했던 플라스크 순정 기능으로 되돌렸습니다.
            return send_file(full_path, conditional=True)
            
        # ==========================================
        # 2. 유튜브 처리: 1:1 투명 프록시 (IP 우회 + 탐색 지원)
        # ==========================================
        P.logger.info(f"[재생 요청] YouTube: {full_path} (요청 화질: {quality})")
        
        try:
            import yt_dlp
            import requests
            
            format_str = 'best[ext=mp4]/best'
            if quality != "자동" and quality.endswith('p') and quality[:-1].isdigit():
                max_height = quality[:-1]
                format_str = f'best[height<={max_height}][ext=mp4]/best[ext=mp4]/best'
                
            ydl_opts = {'format': format_str, 'quiet': True, 'noplaylist': True}
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(full_path, download=False)
                is_live = info.get('is_live', False)
                stream_url = info.get('url') or info.get('manifest_url')
                
                if not stream_url:
                    return Response("스트림 주소 추출 실패", status=500)
                
                # 🌟 [VOD] 1:1 투명 프록시 (유튜브의 응답을 가공 없이 플레이어로 그대로 전달)
                if not is_live:
                    P.logger.info(f"[재생 시작] YouTube VOD 투명 프록시 전송")
                    
                    req_headers = {}
                    if 'Range' in request.headers:
                        req_headers['Range'] = request.headers['Range']
                    
                    yt_resp = requests.request(
                        method=request.method,
                        url=stream_url,
                        headers=req_headers,
                        stream=True,
                        allow_redirects=True
                    )
                    
                    # 유튜브가 내려준 필수 헤더(구간 이동 정보 등)만 복사
                    resp_headers = {}
                    for k in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges']:
                        if k in yt_resp.headers:
                            resp_headers[k] = yt_resp.headers[k]
                    
                    if request.method == 'HEAD':
                        return Response(status=yt_resp.status_code, headers=resp_headers)
                        
                    def generate():
                        for chunk in yt_resp.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                yield chunk
                                
                    # direct_passthrough=True 로 플라스크의 간섭을 완전히 차단합니다.
                    return Response(
                        stream_with_context(generate()), 
                        status=yt_resp.status_code, 
                        headers=resp_headers, 
                        direct_passthrough=True
                    )
                
                # 🌟 [LIVE] 라이브 방송 전용 FFmpeg 프록시
                P.logger.info(f"[재생 시작] YouTube 라이브 방송 중계 가동")
                user_agent = info.get('http_headers', {}).get('User-Agent', 'Mozilla/5.0')
                    
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                cmd.extend(["-user_agent", user_agent])
                cmd.extend([
                    "-i", stream_url,
                    "-map", "0:v:0?", "-map", "0:a:0?",
                    "-c:v", "copy", "-c:a", "copy",
                    "-muxdelay", "0", "-f", "mpegts", "-"
                ])
                
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
                P.logger.info("[재생 종료] YouTube 실시간 중계 종료")

        return Response(generate_live(), mimetype="video/MP2T")

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
