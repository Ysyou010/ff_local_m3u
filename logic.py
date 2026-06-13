import os
import subprocess
import traceback
import mimetypes
import re
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
        # 심층 네트워크 디버깅 로그 (플레이어 통신 상태 추적)
        # ==========================================
        P.logger.info("=" * 60)
        P.logger.info(f"[상세 통신 추적] 대상 경로: {full_path}")
        P.logger.info(f"▶ HTTP Method : {request.method}")
        P.logger.info(f"▶ Range 헤더  : {request.headers.get('Range', '없음 (전체 요청)')}")
        P.logger.info(f"▶ User-Agent  : {request.headers.get('User-Agent', '알 수 없음')}")
        P.logger.info("=" * 60)

        # ==========================================
        # 1. 로컬 파일: ExoPlayer 무한루프 방지 수동 Range 처리
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)

            file_size = os.path.getsize(full_path)
            mime_type, _ = mimetypes.guess_type(full_path)
            if not mime_type:
                mime_type = 'video/mp4'

            # 사전 탐색(HEAD) 요청 응답 처리
            if request.method == 'HEAD':
                response = Response(status=200)
                response.headers['Content-Length'] = str(file_size)
                response.headers['Accept-Ranges'] = 'bytes'
                response.headers['Content-Type'] = mime_type
                return response

            # 구간 탐색(Range) 206 Partial Content 수동 구성 처리
            range_header = request.headers.get('Range', None)
            if range_header:
                match = re.search(r'bytes=(\d+)-(\d*)', range_header)
                if match:
                    start = int(match.group(1))
                    end = match.group(2)
                    if end:
                        end = int(end)
                    else:
                        end = file_size - 1

                    length = end - start + 1
                    P.logger.info(f"[로컬 파일] 206 부분 전송 시작 (Start: {start}, End: {end}, Length: {length})")

                    def generate_partial():
                        with open(full_path, 'rb') as f:
                            f.seek(start)
                            remaining = length
                            while remaining > 0:
                                chunk_size = min(1024 * 1024, remaining)
                                data = f.read(chunk_size)
                                if not data:
                                    break
                                remaining -= len(data)
                                yield data

                    response = Response(stream_with_context(generate_partial()), status=206, mimetype=mime_type)
                    response.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
                    response.headers.add('Accept-Ranges', 'bytes')
                    response.headers.add('Content-Length', str(length))
                    return response

            # Range가 없는 최초 요청
            P.logger.info(f"[재생 시작] 로컬 파일 전체 전송 (200 OK): {full_path}")
            return send_file(full_path, conditional=True)
            
        # ==========================================
        # 2. 유튜브 처리: 안정성 및 탐색(Seeking) 최우선
        # ==========================================
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        
        if request.method == 'HEAD':
            return Response(status=200, mimetype="video/mp4")
            
        try:
            import yt_dlp
            
            if quality == "자동":
                format_str = 'best[ext=mp4]/best'
            elif quality.endswith('p') and quality[:-1].isdigit():
                max_height = quality[:-1]
                format_str = f'best[height<={max_height}][ext=mp4]/best[ext=mp4]/best'
            else:
                format_str = 'best'
                
            ydl_opts = {'format': format_str, 'quiet': True, 'noplaylist': True}
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(full_path, download=False)
                is_live = info.get('is_live', False)
                stream_url = info.get('url') or info.get('manifest_url')
                
                if not stream_url:
                    return Response("스트림 주소 추출 실패", status=500)
                
                if not is_live:
                    P.logger.info(f"[재생 시작] YouTube VOD 다이렉트 연결 리다이렉트 실행")
                    return redirect(stream_url, code=302)
                
                P.logger.info(f"[재생 시작] YouTube 라이브 방송 중계 가동")
                headers = info.get('http_headers', {})
                if headers and 'User-Agent' in headers:
                    user_agent = headers['User-Agent']
                    
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
                P.logger.info("[재생 종료] YouTube 실시간 중계 종료")

        return Response(generate(), mimetype="video/MP2T")

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
