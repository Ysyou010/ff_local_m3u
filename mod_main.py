import traceback
import json
from flask import Response, request, jsonify, render_template
from .setup import *
from . import logic

class ModuleMain(PluginModuleBase):
    def __init__(self, P):
        super(ModuleMain, self).__init__(P, name="main", first_menu="setting")
        self.db_default = {
            f"{self.name}_db_version": "1",
            "media_path": "",
            "extensions": ".mp4,.mkv,.avi,.ts",
            "category_list": "", 
            "scan_depth": "2",
            "exclude_keywords": "sample, trailer, \.",
            "scan_timeout": "5",       
            "scan_max_files": "100",   
        }

    def plugin_load(self):
        P.logger.info("Local Media M3U Plugin Loaded")

    def process_menu(self, sub, req):
        try:
            arg = P.ModelSetting.to_dict()
            if arg is None:
                arg = {}
            for key, value in self.db_default.items():
                if key not in arg:
                    arg[key] = value

            arg["api_m3u"] = logic.get_api_url(req, "m3u")
            
            return render_template(f"{P.package_name}_{self.name}_{sub}.html", arg=arg)
            
        except Exception as e:
            P.logger.error(traceback.format_exc())
            return f"<h1>에러</h1><pre>{traceback.format_exc()}</pre>"

    def process_command(self, command, arg1, arg2, arg3, req):
        try:
            if command == "get_list":
                # 🌟 프론트엔드에서 새로고침 버튼을 눌렀는지(arg1 == "true") 확인합니다.
                force = (arg1 == "true")
                list_data = logic.get_media_list(req, force_refresh=force)
                return jsonify({"ret": "success", "list": list_data})
            
            # (기존 refresh_cache 명령은 이제 쓰이지 않지만 호환성을 위해 남겨둡니다)
            if command == "refresh_cache":
                logic.get_media_files(target_category='all', force_refresh=True)
                return jsonify({"ret": "success", "msg": "목록을 다시 스캔하여 갱신했습니다."})
            
            if command == "edit_item":
                data = json.loads(arg1)
                ret = logic.edit_media_item(int(data['idx']), data['category'], data['title'], data['quality'], data['path'])
                if ret:
                    return jsonify({"ret": "success", "msg": "수정되었습니다."})
                else:
                    return jsonify({"ret": "error", "msg": "저장에 실패했습니다."})
            
            return super(ModuleMain, self).process_command(command, arg1, arg2, arg3, req)
                
        except Exception as e:
            P.logger.error(traceback.format_exc())
            return jsonify({"ret": "error", "msg": str(e)})

    def process_api(self, sub, req):
        try:
            if sub == "m3u" or sub.startswith("m3u"):
                ret = logic.make_m3u(req)
                if ret is None:
                    return Response("make_m3u 함수 반환값 없음", status=500)
                return ret
            
            if sub == "play":
                encoded_name = req.args.get("file")
                if not encoded_name:
                    return Response("파일 파라미터 없음", status=400)
                    
                ret = logic.play_ffmpeg_copy(encoded_name)
                if ret is None:
                    return Response("play_ffmpeg_copy 반환값 없음", status=500)
                return ret
            
            if sub == "subtitle":
                encoded_name = req.args.get("file")
                if not encoded_name:
                    return Response("자막 파일 파라미터 없음", status=400)
                    
                ret = logic.play_subtitle(encoded_name)
                if ret is None:
                    return Response("play_subtitle 반환값 없음", status=500)
                return ret
                
            return Response(f"알 수 없는 API 요청: {sub}", status=400)
            
        except Exception as e:
            P.logger.error(traceback.format_exc())
            return Response(str(e), status=500, mimetype="text/plain")
