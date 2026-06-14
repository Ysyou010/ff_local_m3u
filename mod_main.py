import traceback
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
                list_data = logic.get_media_list(req)
                return jsonify({"ret": "success", "list": list_data})
            
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
            
            # 🌟 [추가된 부분] 자막 요청(subtitle)이 들어오면 logic.play_subtitle로 연결해 줍니다.
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
