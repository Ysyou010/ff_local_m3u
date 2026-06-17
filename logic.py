import os
import subprocess
import traceback
import time
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect, send_file
from framework import SystemModelSetting
from .setup import P

_media_cache = {
    "timestamp": 0,
    "data": []
}
CACHE_DURATION = 3600  

def get_apikey():
    try:
        if SystemModelSetting.get_bool("use_apikey"):
            return str(SystemModelSetting.get("apikey") or "").strip()
    except:
        pass
    return ""

def get_base_url(req):
    try:
        ddns = SystemModelSetting.get('ddns')
        if ddns and ddns.strip():
            return ddns.strip().rstrip("/")
    except Exception as e:
        P.logger.error(f"DDNS 주소 가져오기 실패: {str(e)}")
        pass

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

def get_media_files(target_category=None, force_refresh=False):
    global _media_cache
    try:
        current_time = time.time()
        
        if not force_refresh and _media_cache["data"] and (current_time - _media_cache["timestamp"] < CACHE_DURATION):
            P.logger.debug("메모리에 캐싱된 M3U 미디어 목록을 반환합니다.")
            if target_category and target_category != 'all':
                return [x for x in _media_cache["data"] if x['category'] == target_category]
            return _media_cache["data"]

        P.logger.info("미디어 폴더를 새로 스캔하여 캐시를 갱신합니다.")
        
        media_path_raw = P.ModelSetting.get("media_path")
        if not media_path_raw:
            return []
            
        ext_setting = P.ModelSetting.get("extensions")
        valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
        
        scan_depth = int(P.ModelSetting.get("scan_depth") or "2")
        exclude_raw = P.ModelSetting.get("exclude_keywords") or ""
        exclude_keywords = [kw.strip().lower() for kw in exclude_raw.split(",") if kw.strip()]
        
        # 🌟 추가됨: 사용자 지정 스캔 타임아웃 및 최대 개수 불러오기 (오류 방지 예외처리 포함)
        try:
            MAX_FILES = int(P.ModelSetting.get("scan_max_files") or "100")
        except:
            MAX_FILES = 100
            
        try:
            TIME_LIMIT = float(P.ModelSetting.get("scan_timeout") or "5.0")
        except:
            TIME_LIMIT = 5.0
            
        file_list = []
        paths = [p.strip() for p in media_path_raw.split('\n') if p.strip()]
        
        start_time = time.time()
        file_count = 0
        timeout_reached = False
        max_reached = False
        
        for line in paths:
            if max_reached or timeout_reached:
                break
                
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

            if target_category and target_category != 'all' and category != target_category:
                continue
                
            if path.startswith("http://") or path.startswith("https://"):
                if file_count < MAX_FILES:
                    display_title = title if title else "YouTube Stream"
                    file_list.append({"category": category, "title": display_title, "quality": quality, "path": path})
                    file_count += 1
            
            elif os.path.isfile(path):
                if path.lower().endswith(valid_exts) and file_count < MAX_FILES:
                    display_title = title if title else os.path.basename(path)
                    file_list.append({"category": category, "title": display_title, "quality": quality, "path": path.replace('\\', '/')})
                    file_count += 1
            
            elif os.path.isdir(path):
                base_depth = path.rstrip(os.path.sep).count(os.path.sep)
                
                for root, dirs, files in os.walk(path):
                    if time.time() - start_time > TIME_LIMIT:
                        timeout_reached = True
                        P.logger.warning(f"[스캔 중단] 타임아웃 {TIME_LIMIT}초 초과: {path}")
                        break
                        
                    current_depth = root.rstrip(os.path.sep).count(os.path.sep)
                    if current_depth - base_depth >= scan_depth:
                        del dirs[:]
                        
                    for file_name in files:
                        if file_count >= MAX_FILES:
                            max_reached = True
                            P.logger.warning(f"[스캔 중단] 최대 허용 개수({MAX_FILES}개) 도달")
                            break
                            
                        if file_name.lower().endswith(valid_exts):
                            full_file_path = os.path.join(root, file_name).replace('\\', '/')
                            
                            skip_file = False
                            for kw in exclude_keywords:
                                if kw in full_file_path.lower():
                                    skip_file = True
                                    break
                                    
                            if skip_file:
                                continue
                                
                            file_base_name = os.path.splitext(file_name)[0]
                            display_title = f"{title} - {file_base_name}" if title else file_base_name

                            file_list.append({
                                "category": category,
                                "title": display_title,
                                "quality": quality,
                                "path": full_file_path
                            })
                            file_count += 1
                            
                    if max_reached or timeout_reached:
                        break
        
        _media_cache["data"] = file_list
        _media_cache["timestamp"] = time.time()
        
        return file_list
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return []

def get_media_list(req, force_refresh=False):
    try:
        # 🌟 파라미터로 받은 강제 새로고침 여부를 넘겨줍니다.
        files = get_media_files('all', force_refresh=force_refresh)
        result = []
        for idx, item in enumerate(files, 1):
            encoded_payload = f"{item['quality']}||{item['path']}"
            encoded_name = _safe_b64encode(encoded_payload)
            
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mkv'
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
            display_name = f"[{item['category']}] {item['title']}"
            result.append({
                "idx": idx,
                "name": display_name,
                "url": play_url,
                "raw_category": item['category'],
                "raw_title": item['title'],
                "raw_quality": item['quality'],
                "raw_path": item['path']
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
            
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mkv'
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
            display_name = item['title']

            sub_url = ""
            if not item['path'].startswith('http'):
                base_path = os.path.splitext(item['path'])[0]
                
                possible_srts = [
                    base_path + ".srt",
                    base_path + ".ko.srt",
                    base_path + ".kr.srt",
                    base_path + ".kor.srt"
                ]
                
                for srt_path in possible_srts:
                    if os.path.isfile(srt_path):
                        encoded_sub = _safe_b64encode(srt_path)
                        sub_url = get_api_url(req, "subtitle", {"file": encoded_sub, "ext": ".srt"})
                        break 

            if sub_url:
                lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}" subtitle="{sub_url}",{display_name}\n')
                lines.append(f'#EXTVLCOPT:sub-file={sub_url}\n')
                lines.append(f'{play_url}\n')
            else:
                lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n')
                lines.append(f'{play_url}\n')
            
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
            
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        
        cmd = []
        mimetype = "video/MP2T"

        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
            
            P.logger.info(f"[재생 시작] 로컬 파일 IPTV TS 패키징 (ExoPlayer 대응): {full_path}")
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
            cmd.extend(["-re", "-i", full_path])
            cmd.extend([
                "-map", "0:v:0?", "-map", "0:a:0?",
                "-c:v", "copy", "-c:a", "copy",
                "-fflags", "+genpts",
                "-mpegts_flags", "resend_headers",
                "-pcr_period", "40",
                "-muxdelay", "0", "-f", "mpegts", "-"
            ])
            
        else:
            P.logger.info(f"[재생 요청] YouTube: {full_path} (요청 화질: {quality})")
            try:
                import yt_dlp
                
                ydl_opts = {
                    'quiet': True, 
                    'noplaylist': True,
                    'extractor_args': {'youtube': ['player_client=android,ios,tv,web']} 
                }
                
                if quality in ["720p", "480p", "360p", "240p", "144p"]:
                    ydl_opts['format'] = 'b'
                    max_height = quality[:-1]
                    ydl_opts['format_sort'] = [f'res:{max_height}']
                else:
                    ydl_opts['format'] = 'bv*+ba/b'
                    if quality != "자동" and quality.endswith('p') and quality[:-1].isdigit():
                        max_height = quality[:-1]
                        ydl_opts['format_sort'] = [f'res:{max_height}']
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    stream_url = info.get('url') or info.get('manifest_url')
                    is_live = info.get('is_live', False)
                    req_formats = info.get('requested_formats')
                    
                    if req_formats and len(req_formats) == 2 and not is_live:
                        video_url = req_formats[0].get('url')
                        audio_url = req_formats[1].get('url')
                        
                        headers = req_formats[0].get('http_headers', {})
                        if headers and 'User-Agent' in headers:
                            user_agent = headers['User-Agent']
                            
                        P.logger.info(f"[재생 시작] YouTube 고화질 VOD - MKV 수동 병합 중계")
                        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                        cmd.extend(["-user_agent", user_agent])
                        cmd.extend([
                            "-i", video_url,
                            "-i", audio_url,
                            "-map", "0:v:0", "-map", "1:a:0",
                            "-c:v", "copy", "-c:a", "copy",
                            "-f", "matroska", "-"
                        ])
                        mimetype = "video/x-matroska"
                    
                    else:
                        if not stream_url:
                            P.logger.error("[재생 실패] 유튜브 스트림 추출 실패")
                            return Response("유튜브 스트림 추출 실패", status=500)
                            
                        headers = info.get('http_headers', {})
                        if headers and 'User-Agent' in headers:
                            user_agent = headers['User-Agent']
                            
                        P.logger.info(f"[재생 시작] YouTube 단일 스트림 TS 프록시 중계 (ExoPlayer 대응)")
                        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                        cmd.extend(["-user_agent", user_agent])
                        cmd.extend([
                            "-i", stream_url,
                            "-map", "0:v:0?", "-map", "0:a:0?",
                            "-c:v", "copy", "-c:a", "copy",
                            "-fflags", "+genpts",
                            "-mpegts_flags", "resend_headers",
                            "-pcr_period", "40",
                            "-f", "mpegts", "-"
                        ])
                        mimetype = "video/MP2T"
                        
            except Exception as e:
                P.logger.error(f"[재생 실패] 유튜브 에러: {e}")
                P.logger.error(traceback.format_exc())
                return Response(f"유튜브 에러: {e}", status=500)
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        
        @stream_with_context
        def generate():
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.kill()
                P.logger.info("[재생 종료] 스트림 연결 해제")

        response = Response(generate(), mimetype=mimetype)
        
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Connection'] = 'keep-alive'
        
        return response

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)

def play_subtitle(encoded_name):
    try:
        full_path = _safe_b64decode(encoded_name)
        if not os.path.isfile(full_path):
            P.logger.error(f"[자막 실패] 파일 없음: {full_path}")
            return Response("Subtitle not found", status=404)
            
        P.logger.info(f"[자막 전송] {full_path}")
        return send_file(full_path, mimetype="text/plain", conditional=True)
        
    except Exception as e:
        P.logger.error(f"[자막 에러] {str(e)}")
        return Response("Subtitle Error", status=500)

def edit_media_item(idx, category, title, quality, path):
    try:
        media_path_raw = P.ModelSetting.get("media_path")
        lines = media_path_raw.split('\n')
        
        non_empty_count = 0
        for i, line in enumerate(lines):
            if line.strip():  
                non_empty_count += 1
                if non_empty_count == idx:
                    lines[i] = f"{category} | {title} | {quality} | {path}"
                    break
                    
        new_media_path = "\n".join(lines)
        P.ModelSetting.set("media_path", new_media_path)
        return True
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return False
