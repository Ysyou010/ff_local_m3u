import os
import subprocess
import time
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect, send_file
from framework import SystemModelSetting
from .setup import P

# 🌟 메모리 캐싱을 위한 전역 변수 설정 (기본 유지시간 1시간)
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
        # 🌟 1순위: Flaskfarm 시스템에 설정된 DDNS 주소를 최우선으로 가져옵니다.
        ddns = SystemModelSetting.get('ddns')
        if ddns and ddns.strip():
            return ddns.strip().rstrip("/")
    except Exception as e:
        P.logger.error(f"DDNS 주소 가져오기 실패: {str(e)}")
        pass

    try:
        # 🌟 2순위: 만약 DDNS 설정이 비어있다면 기존처럼 현재 접속 중인 주소를 씁니다.
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
        
        # 🌟 UI 설정창에서 스캔 깊이와 제외 단어 리스트를 가져옵니다.
        scan_depth = int(P.ModelSetting.get("scan_depth") or "2")
        exclude_raw = P.ModelSetting.get("exclude_keywords") or ""
        exclude_keywords = [kw.strip().lower() for kw in exclude_raw.split(",") if kw.strip()]
        
        file_list = []
        paths = [p.strip() for p in media_path_raw.split('\n') if p.strip()]
        
        # 🌟 안전장치 변수 설정
        MAX_FILES = 200
        TIME_LIMIT = 10.0
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
            
            # 🌟 폴더인 경우 안전장치 적용 스캔
            elif os.path.isdir(path):
                base_depth = path.rstrip(os.path.sep).count(os.path.sep)
                
                for root, dirs, files in os.walk(path):
                    # 1. 스캔 시간 초과 (5초) 확인
                    if time.time() - start_time > TIME_LIMIT:
                        timeout_reached = True
                        P.logger.warning(f"[스캔 중단] 타임아웃 5초 초과: {path}")
                        break
                        
                    # 2. 스캔 깊이 제한 적용
                    current_depth = root.rstrip(os.path.sep).count(os.path.sep)
                    if current_depth - base_depth >= scan_depth:
                        del dirs[:] # 지정된 깊이에 도달하면 더 이상 하위 폴더로 내려가지 않습니다.
                        
                    for file_name in files:
                        # 3. 최대 100개 제한 확인
                        if file_count >= MAX_FILES:
                            max_reached = True
                            P.logger.warning(f"[스캔 중단] 최대 허용 개수(100개) 도달")
                            break
                            
                        if file_name.lower().endswith(valid_exts):
                            full_file_path = os.path.join(root, file_name).replace('\\', '/')
                            
                            # 4. 제외 키워드 포함 여부 검사
                            skip_file = False
                            for kw in exclude_keywords:
                                if kw in full_file_path.lower():
                                    skip_file = True
                                    break
                                    
                            if skip_file:
                                continue # 금지어가 포함되어 있으면 스킵
                                
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
            
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mkv'
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
            display_name = f"[{item['category']}] {item['title']}"
            result.append({
                "idx": idx,
                "name": display_name,
                "url": play_url,
                # 🌟 [수정] 프론트엔드 수정창에 띄워줄 원본 데이터 추가
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
        
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
            P.logger.info(f"[재생 시작] 로컬 파일 다이렉트: {full_path}")
            return send_file(full_path, conditional=True)
            
        P.logger.info(f"[재생 요청] YouTube: {full_path} (요청 화질: {quality})")
        try:
            import yt_dlp
            
            ydl_opts = {
                'quiet': True, 
                'noplaylist': True,
                # 🌟 [봇 차단 우회] 유튜브 서버에 모바일/TV 앱인 것처럼 클라이언트 속이기
                'extractor_args': {'youtube': ['player_client=android,ios,tv,web']} 
            }
            # 🌟 [구간 탐색 활성화 핵심 로직] 화질별로 스트림 요청 방식을 분리합니다.
            if quality in ["720p", "480p", "360p", "240p", "144p"]:
                # 720p 이하는 '이미 화면+소리가 합쳐진 단일 파일(b)'을 가져옵니다.
                # FFmpeg 병합을 거치지 않고 주소만 바로 넘기므로 ExoPlayer에서 앞뒤 탐색이 100% 가능해집니다.
                ydl_opts['format'] = 'b'
                max_height = quality[:-1]
                ydl_opts['format_sort'] = [f'res:{max_height}']
                
            else:
                # 1080p 또는 '자동(최대화질)'은 유튜브가 화면/소리를 분리하므로 실시간 병합(bv*+ba/b)을 사용합니다.
                # 이 경우 플레이어에서 생방송처럼 인식되어 구간 탐색이 불가능합니다.
                ydl_opts['format'] = 'bv*+ba/b'
                if quality != "자동" and quality.endswith('p') and quality[:-1].isdigit():
                    max_height = quality[:-1]
                    ydl_opts['format_sort'] = [f'res:{max_height}']
                
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(full_path, download=False)
                
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
                        
                    P.logger.info(f"[재생 시작] YouTube 고화질 VOD - MKV 수동 병합 중계 (목표 해상도: {quality})")
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
                        
                    if not is_live:
                        P.logger.info(f"[재생 시작] YouTube 단일 스트림 VOD 다이렉트 리다이렉트")
                        return redirect(stream_url, code=302)
                    
                    P.logger.info(f"[재생 시작] YouTube 라이브 방송 TS 중계 가동")
                    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                    cmd.extend(["-user_agent", user_agent])
                    cmd.extend([
                        "-i", stream_url,
                        "-map", "0:v:0?", "-map", "0:a:0?",
                        "-c:v", "copy", "-c:a", "copy",
                        "-f", "mpegts", "-"
                    ])
                    mimetype = "video/MP2T"
                    
        except Exception as e:
            P.logger.error(f"[재생 실패] 유튜브 에러: {e}")
            P.logger.error(traceback.format_exc())
            return Response(f"유튜브 에러: {e}", status=500)
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        
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
                P.logger.info("[재생 종료] YouTube 스트림 연결 해제")

        return Response(generate(), mimetype=mimetype)

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

# 🌟 [추가] 프론트엔드에서 넘어온 수정 데이터를 DB에 반영하는 함수
def edit_media_item(idx, category, title, quality, path):
    try:
        media_path_raw = P.ModelSetting.get("media_path")
        lines = media_path_raw.split('\n')
        
        non_empty_count = 0
        for i, line in enumerate(lines):
            if line.strip():  # 빈 줄은 건너뛰고 카운트
                non_empty_count += 1
                if non_empty_count == idx:
                    # 해당 순번의 줄을 새로운 규격으로 덮어씁니다
                    lines[i] = f"{category} | {title} | {quality} | {path}"
                    break
                    
        new_media_path = "\n".join(lines)
        P.ModelSetting.set("media_path", new_media_path)
        return True
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return False
