def get_media_files(target_category='all', force_refresh=False, scan_target='all'):
    global _media_cache
    try:
        current_time = time.time()
        
        # 1. 스캔이 필요한지 판단
        needs_scan = force_refresh or not _media_cache.get("data") or (current_time - _media_cache.get("timestamp", 0) >= CACHE_DURATION)
        
        if needs_scan:
            preserved_data = []
            
            # 🌟 핵심: 특정 카테고리만 스캔할 경우, 기존 캐시에서 다른 카테고리 데이터는 미리 빼두어 보존합니다.
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

                # 🌟 핵심: 부분 스캔 모드일 때, 현재 설정 줄이 요청받은 탭이 아니면 탐색 자체를 완전히 스킵합니다.
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
                
                elif os.path.isdir(path):
                    base_depth = path.rstrip(os.path.sep).count(os.path.sep)
                    
                    for root, dirs, files in os.walk(path):
                        if time.time() - start_time > TIME_LIMIT:
                            timeout_reached = True
                            break
                            
                        current_depth = root.rstrip(os.path.sep).count(os.path.sep)
                        if current_depth - base_depth >= scan_depth:
                            del dirs[:]
                            
                        for file_name in files:
                            if file_count >= MAX_FILES:
                                max_reached = True
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
            
            # 스캔 완료 후, 보존해둔 다른 탭들의 데이터와 방금 스캔한 데이터를 병합하여 캐시에 저장
            _media_cache["data"] = preserved_data + file_list
            _media_cache["timestamp"] = time.time()
        
        # 반환 시 요청한 카테고리에 맞게 필터링
        if target_category and target_category not in ['all', '전체']:
            return [x for x in _media_cache["data"] if x['category'] == target_category]
        return _media_cache["data"]
        
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return []

def get_media_list(req, force_refresh=False, scan_target='all'):
    try:
        # 프론트엔드가 모든 탭을 그려야 하므로 반환(target_category)은 'all'로 주되, 스캔 대상(scan_target)만 제한합니다.
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
