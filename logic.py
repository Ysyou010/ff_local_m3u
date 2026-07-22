import os
import subprocess
import traceback
import time
import mimetypes
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect, send_file
from framework import SystemModelSetting
from .setup import P

_media_cache = {
    "timestamp": 0,
    "data": []
}
CACHE_DURATION = 43200  # 12시간

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

def invalidate_cache():
    global _media_cache
    _media_cache["timestamp"] = 0

def get_media_files(target_category='all', force_refresh=False, scan_target='all'):
    global _media_cache
    try:
        current_time = time.time()
        
        needs_scan = force_refresh or not _media_cache.get("data") or (current_time - _media_cache.get("timestamp", 0) >= CACHE_DURATION)
        
        if needs_scan:
            preserved_data = []
            
            if force_refresh and scan_target not in ['all', '전체'] and _media_cache.get("data"):
                preserved_data = [x for x in _media_cache["data"] if x['category'] != scan_target]
                P.logger.info(f"[{scan_target}] 카테고리만 부분 스캔하여 캐시를 갱신합니다.")
            else:
                scan_target = 'all'
                P.logger.info("미디어 전체를 새로 스캔하여 캐시를 갱신합니다.")
            
            media_path_raw = P.ModelSetting.get("media_path")
            if not media_path_raw:
                return []
                
            ext_setting = P.ModelSetting.get("extensions")
            valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
            
            scan_depth = int(P.ModelSetting.get("scan_depth") or "2")
            exclude_raw = P.ModelSetting.get("exclude_keywords") or ""
            exclude_keywords = [kw.strip().lower() for kw in exclude_raw.split(",") if kw.strip()]
            
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

                if scan_target != 'all' and category != scan_target:
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
                
                # 🌟 [성능 최적화] os.walk 대신 os.scandir을 사용하여 클라우드 마운트 디렉토리 탐색 속도 극대화
                elif os.path.isdir(path):
                    dirs_to_scan = [(path, 0)] # (현재 경로, 깊이)
                    entries_since_check = 0

                    while dirs_to_scan:
                        if time.time() - start_time > TIME_LIMIT:
                            timeout_reached = True
                            P.logger.warning(f"[스캔 중단] 타임아웃 {TIME_LIMIT}초 초과: {path}")
                            break
                        if file_count >= MAX_FILES:
                            max_reached = True
                            P.logger.warning(f"[스캔 중단] 최대 허용 개수({MAX_FILES}개) 도달")
                            break

                        current_dir, current_depth = dirs_to_scan.pop()

                        try:
                            with os.scandir(current_dir) as it:
                                for entry in it:
                                    # 🌟 [성능 최적화] time.time() 호출을 매 엔트리마다 하지 않고 200개마다 체크
                                    entries_since_check += 1
                                    if entries_since_check >= 200:
                                        entries_since_check = 0
                                        if time.time() - start_time > TIME_LIMIT:
                                            timeout_reached = True
                                            break
                                    if file_count >= MAX_FILES:
                                        max_reached = True
                                        break
                                        
                                    if entry.is_file() and entry.name.lower().endswith(valid_exts):
                                        full_file_path = entry.path.replace('\\', '/')
                                        
                                        skip_file = False
                                        for kw in exclude_keywords:
                                            if kw in full_file_path.lower():
                                                skip_file = True
                                                break
                                                
                                        if not skip_file:
                                            file_base_name = os.path.splitext(entry.name)[0]
                                            display_title = f"{title} - {file_base_name}" if title else file_base_name

                                            file_list.append({
                                                "category": category,
                                                "title": display_title,
                                                "quality": quality,
                                                "path": full_file_path
                                            })
                                            file_count += 1
                                            
                                    elif entry.is_dir() and current_depth < scan_depth:
                                        # 🌟 [I/O 최적화] 제외 키워드에 해당하는 폴더는 scandir로 열어보지 않고 건너뜀
                                        dir_path_lower = entry.path.lower()
                                        if not any(kw in dir_path_lower for kw in exclude_keywords):
                                            dirs_to_scan.append((entry.path, current_depth + 1))
                                        
                        except PermissionError:
                            continue
                        except Exception as e:
                            P.logger.error(f"Scandir Error: {e}")
                            continue
            
            _media_cache["data"] = preserved_data + file_list
            _media_cache["timestamp"] = time.time()
        
        if target_category and target_category not in ['all', '전체']:
            return [x for x in _media_cache["data"] if x['category'] == target_category]
        return _media_cache["data"]
        
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return []

def get_media_list(req, force_refresh=False, scan_target='all'):
    try:
        files = get_media_files(target_category='all', force_refresh=force_refresh, scan_target=scan_target)
        result = []
        for idx, item in enumerate(files, 1):
            encoded_payload = f"{item['quality']}||{item['path']}"
            encoded_name = _safe_b64encode(encoded_payload)
            
            if not item['path'].startswith('http'):
                ext = os.path.splitext(item['path'])[1].lower()
            else:
                ext = '.mp4' if item['quality'] in ["720p", "480p", "360p", "240p", "144p"] else '.ts'
                
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
            
            if not item['path'].startswith('http'):
                ext = os.path.splitext(item['path'])[1].lower()
            else:
                ext = '.mp4' if item['quality'] in ["720p", "480p", "360p", "240p", "144p"] else '.ts'
                
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
            display_name = item['title']

            sub_url = ""
            if not item['path'].startswith('http'):
                base_path = os.path.splitext(item['path'])[0]
                possible_srts = [
                    base_path + ".srt", base_path + ".ko.srt",
                    base_path + ".kr.srt", base_path + ".kor.srt"
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
            
            mime_type, _ = mimetypes.guess_type(full_path)
            if not mime_type:
                mime_type = 'video/mp4'
                
            P.logger.info(f"[재생 시작] 로컬 파일 다이렉트 전송 (구간 탐색 가능): {full_path}")
            response = send_file(full_path, mimetype=mime_type, conditional=True)
            response.headers['Access-Control-Allow-Origin'] = '*'
            # VOD는 정적 파일이므로 매 요청 재검증을 강제하지 않고 캐싱을 허용해 탐색/재생 반응 속도를 높인다.
            response.headers['Cache-Control'] = 'public, max-age=86400'
            return response
            
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
                            
                        P.logger.info(f"[재생 시작] YouTube 고화질 VOD - MKV 수동 병합 중계 (구간 탐색 불가)")
                        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-user_agent", user_agent,
                               "-i", video_url, "-i", audio_url,
                               "-map", "0:v:0", "-map", "1:a:0",
                               "-c:v", "copy", "-c:a", "copy",
                               "-f", "matroska", "-"]
                        mimetype = "video/x-matroska"
                    
                    elif not is_live and stream_url and quality in ["720p", "480p", "360p", "240p", "144p"]:
                        P.logger.info(f"[재생 시작] YouTube 단일 스트림 다이렉트 연결 (구간 탐색 가능)")
                        return redirect(stream_url, code=302)
                        
                    else:
                        if not stream_url:
                            P.logger.error("[재생 실패] 유튜브 스트림 추출 실패")
                            return Response("유튜브 스트림 추출 실패", status=500)
                            
                        headers = info.get('http_headers', {})
                        if headers and 'User-Agent' in headers:
                            user_agent = headers['User-Agent']
                            
                        P.logger.info(f"[재생 시작] YouTube 실시간 방송 TS 프록시 중계 (구간 탐색 불가)")
                        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-user_agent", user_agent,
                               "-i", stream_url,
                               "-map", "0:v:0?", "-map", "0:a:0?",
                               "-c:v", "copy", "-c:a", "copy",
                               "-fflags", "+genpts", "-mpegts_flags", "resend_headers",
                               "-pcr_period", "40", "-f", "mpegts", "-"]
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

def add_media_item(category, title, quality, path):
    global _media_cache
    try:
        path = path.strip()
        new_line = f"{category} | {title} | {quality} | {path}"

        media_path_raw = P.ModelSetting.get("media_path") or ""
        new_media_path = f"{media_path_raw}\n{new_line}" if media_path_raw.strip() else new_line
        P.ModelSetting.set("media_path", new_media_path)

        is_folder = not (path.startswith("http://") or path.startswith("https://")) and os.path.isdir(path)

        if is_folder:
            # 폴더는 내부 파일 목록을 알 수 없으므로 캐시를 무효화해 다음 조회 때 전체 재스캔되게 한다.
            invalidate_cache()
            P.logger.info(f"[폴더 추가] 다음 목록 조회 시 전체 재스캔됩니다: {path}")
        else:
            # 파일/유튜브 URL 단건 추가는 폴더 스캔 없이 캐시에 바로 반영한다.
            ext_setting = P.ModelSetting.get("extensions")
            valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
            is_url = path.startswith("http://") or path.startswith("https://")

            if is_url or path.lower().endswith(valid_exts):
                _media_cache["data"].append({
                    "category": category,
                    "title": title,
                    "quality": quality,
                    "path": path.replace('\\', '/')
                })
                P.logger.info(f"[빠른 추가] 폴더 스캔 없이 캐시에 항목만 추가: {path}")

        return True
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return False

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
