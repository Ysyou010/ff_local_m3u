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
            play_url = get_api_url(req, "play", {"file": encoded_name})
            
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
            play_url = get_api_url(req, "play", {"file": encoded_name})
            
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
            
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

        # ==========================================
        # 1. 로컬 파일 처리: 실시간 가상 라이브 방송 채널화 (-re 옵션 필수)
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
                
            P.logger.info(f"[방송 송출] 로컬 파일을 IPTV 실시간 스트림으로 인코딩: {full_path}")
            # -re 옵션이 정적 파일을 실시간 방송 배속(1배속)으로 강제 변환합니다.
            cmd.extend(["-re", "-i", full_path])
            cmd.extend([
                "-map", "0:v:0?", "-map", "0:a:0?",
                "-c:v", "copy", "-c:a", "copy",
                "-muxdelay", "0", "-f", "mpegts", "-"
            ])
            
        # ==========================================
        # 2. 유튜브 처리: 구글 차단 우회 복합 라이브 스트림화
        # ==========================================
        else:
            P.logger.info(f"[방송 송출] 유튜브 영상을 IPTV 실시간 스트림으로 인코딩: {full_path}")
            try:
                import yt_dlp
                
                # 안전한 재생을 위해 안드로이드 전용 H.264(avc1) 코덱 규격 타겟팅
                if quality in ["1080p", "1440p", "2160p"]:
                    max_height = quality[:-1]
                    format_str = f'bestvideo[height<={max_height}][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]'
                else:
                    if quality == "자동":
                        format_str = 'bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best'
                    else:
                        max_height = quality[:-1]
                        format_str = f'bestvideo[height<={max_height}][vcodec^=avc1]+bestaudio[ext=m4a]/best[height<={max_height}][ext=mp4]/best'
                        
                ydl_opts = {'format': format_str, 'quiet': True, 'noplaylist': True}
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    is_live = info.get('is_live', False)
                    stream_url = info.get('url') or info.get('manifest_url')
                    user_agent = info.get('http_headers', {}).get('User-Agent', "Mozilla/5.0")
                    req_formats = info.get('requested_formats')
                    
                    if req_formats and len(req_formats) == 2 and not is_live:
                        cmd.extend(["-user_agent", user_agent, "-i", req_formats[0].get('url'), "-i", req_formats[1].get('url')])
                        cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
                    else:
                        cmd.extend(["-user_agent", user_agent, "-i", stream_url])
                        cmd.extend(["-map", "0:v:0?", "-map", "0:a:0?"])
                        
                    cmd.extend([
                        "-c:v", "copy", "-c:a", "copy",
                        "-bsf:v", "h264_mp4toannexb",
                        "-fflags", "+genpts",
                        "-muxdelay", "0", "-f", "mpegts", "-"
                    ])
            except Exception as e:
                P.logger.error(f"[재생 실패] 유튜브 파싱 에러: {e}")
                return Response(f"유튜브 에러: {e}", status=500)

        # ==========================================
        # 3. 통합 실시간 미디어 스트림 출력 (IPTV 규격 통일)
        # ==========================================
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        
        @stream_with_context
        def generate_live_stream():
            try:
                while True:
                    chunk = proc.stdout.read(188 * 32)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.kill()
                P.logger.info("[송출 종료] 실시간 IPTV 파이프라인 닫힘")

        return Response(generate_live_stream(), mimetype="video/MP2T")

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
