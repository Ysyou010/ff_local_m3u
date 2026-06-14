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
            
            # 🌟 안드로이드가 MKV 컨테이너를 정확히 파싱하도록 확장자를 변경합니다.
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mkv'
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
            
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mkv'
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
            display_name = item['title']

            # 🌟 [자막 로직 추가] 영상과 동일한 이름의 .srt 파일이 있는지 확인
            sub_url = ""
            if not item['path'].startswith('http'):
                base_path = os.path.splitext(item['path'])[0]
                
                # 🌟 서버가 찾아볼 자막 이름 후보들 (우선순위 순)
                possible_srts = [
                    base_path + ".srt",      # 예: 가족여행.srt
                    base_path + ".ko.srt",   # 예: 가족여행.ko.srt
                    base_path + ".kr.srt",   # 예: 가족여행.kr.srt
                    base_path + ".kor.srt"   # 예: 가족여행.kor.srt
                ]
                
                # 후보들을 하나씩 뒤져서 파일이 있으면 바로 주소를 만듭니다.
                for srt_path in possible_srts:
                    if os.path.isfile(srt_path):
                        encoded_sub = _safe_b64encode(srt_path)
                        sub_url = get_api_url(req, "subtitle", {"file": encoded_sub, "ext": ".srt"})
                        break # 자막을 하나 찾았으면 더 찾지 않고 멈춤
            # 🌟 자막 파일 존재 여부에 따라 M3U 작성 분기
            if sub_url:
                # 안드로이드 앱을 위한 subtitle 속성 추가
                lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}" subtitle="{sub_url}",{display_name}\n')
                # 팟플레이어를 위한 vlc 옵션 추가
                lines.append(f'#EXTVLCOPT:sub-file={sub_url}\n')
                lines.append(f'{play_url}\n')
            else:
                # 자막이 없을 때 기존 오리지널 방식
                lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n')
                lines.append(f'{play_url}\n')
            
        return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return Response(f"make_m3u 에러: {str(e)}", status=500)

# 🌟 [새로 추가할 함수] 플레이어가 자막을 요청할 때 .srt 파일을 전송해 줍니다.
def play_subtitle(encoded_name):
    try:
        full_path = _safe_b64decode(encoded_name)
        if not os.path.isfile(full_path):
            P.logger.error(f"[자막 실패] 파일 없음: {full_path}")
            return Response("Subtitle not found", status=404)
            
        P.logger.info(f"[자막 전송] {full_path}")
        # SRT 자막은 일반 텍스트 형태로 전송
        return send_file(full_path, mimetype="text/plain", conditional=True)
        
    except Exception as e:
        P.logger.error(f"[자막 에러] {str(e)}")
        return Response("Subtitle Error", status=500)

def play_ffmpeg_copy(encoded_name):
    try:
        full_str = _safe_b64decode(encoded_name)
        if "||" in full_str:
            quality, full_path = full_str.split("||", 1)
        else:
            quality = "자동"
            full_path = full_str
            
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        
        # ==========================================
        # 1. 로컬 파일: 100% 순정 코드 (건드리지 않음)
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
            P.logger.info(f"[재생 시작] 로컬 파일 다이렉트: {full_path}")
            return send_file(full_path, conditional=True)
            
        # ==========================================
        # 2. YouTube 수동 해상도 매칭
        # ==========================================
        P.logger.info(f"[재생 요청] YouTube: {full_path} (요청 화질: {quality})")
        try:
            import yt_dlp
            
            # yt-dlp의 자동 선택을 무시하고, 정보만 모두 긁어옵니다.
            ydl_opts = {'quiet': True, 'noplaylist': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(full_path, download=False)
                is_live = info.get('is_live', False)
                formats = info.get('formats', [])
                
                headers = info.get('http_headers', {})
                if headers and 'User-Agent' in headers:
                    user_agent = headers['User-Agent']
                    
                cmd = []
                mimetype = "video/mp4"
                
                if is_live:
                    stream_url = info.get('url') or info.get('manifest_url')
                    P.logger.info(f"[재생 시작] YouTube 라이브 방송 TS 중계 가동")
                    mimetype = "video/MP2T"
                    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                    cmd.extend(["-user_agent", user_agent, "-i", stream_url])
                    cmd.extend(["-map", "0:v:0?", "-map", "0:a:0?"])
                    cmd.extend(["-c:v", "copy", "-c:a", "copy", "-muxdelay", "0", "-f", "mpegts", "-"])
                else:
                    # 🌟 [족집게 추출 로직] 사용자가 설정한 해상도를 계산
                    max_height = 1080
                    if quality.endswith('p') and quality[:-1].isdigit():
                        max_height = int(quality[:-1])
                    elif quality == "자동":
                        max_height = 2160
                        
                    # 비디오+오디오 합본과 분리본을 직접 분류
                    merged = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and (f.get('height') or 0) <= max_height]
                    v_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none' and (f.get('height') or 0) <= max_height]
                    a_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
                    
                    # 720p 이하는 앞뒤 탐색을 위해 302 리다이렉트 (합본 사용)
                    if max_height <= 720 and merged:
                        best_merged = sorted(merged, key=lambda x: ((x.get('height') or 0), (x.get('tbr') or 0)))[-1]
                        P.logger.info(f"[재생 시작] YouTube VOD 다이렉트 리다이렉트 ({best_merged.get('height')}p)")
                        return redirect(best_merged.get('url'), code=302)
                    
                    # 1080p 이상은 분리된 초고화질 스트림을 MKV로 합쳐서 전송
                    else:
                        if v_formats and a_formats:
                            best_v = sorted(v_formats, key=lambda x: ((x.get('height') or 0), (x.get('tbr') or 0)))[-1]
                            best_a = sorted(a_formats, key=lambda x: (x.get('tbr') or 0))[-1]
                            
                            P.logger.info(f"[재생 시작] YouTube 고화질 VOD - MKV 수동 병합 중계 ({best_v.get('height')}p)")
                            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                            cmd.extend(["-user_agent", user_agent])
                            cmd.extend([
                                "-i", best_v.get('url'),
                                "-i", best_a.get('url'),
                                "-map", "0:v:0", "-map", "1:a:0",
                                "-c:v", "copy", "-c:a", "copy",
                                "-f", "matroska", "-"
                            ])
                            mimetype = "video/x-matroska"
                        
                        # 예외: 분리본이 없으면 합본으로 폴백
                        elif merged:
                            best_merged = sorted(merged, key=lambda x: ((x.get('height') or 0), (x.get('tbr') or 0)))[-1]
                            P.logger.info(f"[재생 시작] YouTube VOD 다이렉트 리다이렉트 폴백 ({best_merged.get('height')}p)")
                            return redirect(best_merged.get('url'), code=302)
                        else:
                            P.logger.error("[재생 실패] 조건에 맞는 스트림을 찾을 수 없습니다.")
                            return Response("스트림 주소 추출 실패", status=500)
                            
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
