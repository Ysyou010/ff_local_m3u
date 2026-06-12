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
            import traceback
            error_msg = f"<h1>에러 원인 분석</h1><pre>{traceback.format_exc()}</pre>"
            P.logger.error(traceback.format_exc())
            return error_msg

    def process_command(self, command, arg1, arg2, arg3, req):
        try:
            if command == "get_list":
                ret = {"ret": "success", "list": logic.get_media_list(req)}
                return jsonify(ret)
            
            elif command == "setting_save":
                ret = P.ModelSetting.save_from_req(req)
                return jsonify({"ret": "success"})
                
            # --- 고장 난 프레임워크 탐색기를 대체할 자체 탐색기 엔진 ---
            elif command == "browse_folder":
                import os
                target_path = arg1 or "/"
                if not os.path.isdir(target_path):
                    target_path = "/"
                try:
                    items = []
                    # 상위 폴더로 가기 버튼
                    if target_path != "/":
                        items.append({"name": "📂 .. (상위 폴더로 이동)", "path": os.path.dirname(target_path)})
                    
                    # 현재 경로의 폴더들만 스캔
                    for name in sorted(os.listdir(target_path)):
                        p = os.path.join(target_path, name)
                        if os.path.isdir(p):
                            items.append({"name": f"📁 {name}", "path": p})
                            
                    return jsonify({"ret": "success", "current_path": target_path, "items": items})
                except Exception as e:
                    return jsonify({"ret": "error", "msg": str(e)})
            # -------------------------------------------------------------
            
            return super(ModuleMain, self).process_command(command, arg1, arg2, arg3, req)
                
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            return jsonify({"ret": "error", "msg": str(e)})

    def process_api(self, sub, req):
        try:
            if sub == "m3u":
                return logic.make_m3u(req)
            
            if sub.startswith("play/ffmpeg/"):
                encoded_name = sub.split("play/ffmpeg/")[1]
                return logic.play_ffmpeg_copy(encoded_name)
                
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            return Response(str(e), status=500, mimetype="text/plain")
