import traceback
from flask import Response, request, jsonify, render_template
from .setup import *
from . import logic

class ModuleMain(PluginModuleBase):
    def __init__(self, P):
        super(ModuleMain, self).__init__(P, name="main", first_menu="setting")
        self.db_default = {
            f"{self.name}_db_version": "1",
            "custom_file_list": "", # 경로 리스트를 저장할 변수
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

            host_url = req.host_url.rstrip('/')
            arg["api_m3u"] = f"{host_url}/{P.package_name}/api/m3u"
            
            return render_template(f"{P.package_name}_{self.name}_{sub}.html", arg=arg)
            
        except Exception as e:
            import traceback
            error_msg = f"<h1>에러 원인 분석</h1><pre>{traceback.format_exc()}</pre>"
            P.logger.error(traceback.format_exc())
            return error_msg

    def process_command(self, command, arg1, arg2, arg3, req):
        ret = {"ret": "success"}
        try:
            if command == "get_list":
                ret["list"] = logic.get_media_list(req)
                
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            ret = {"ret": "error", "msg": str(e)}
            
        return jsonify(ret)

    def process_api(self, sub, req):
        try:
            if sub == "m3u":
                return logic.make_m3u(req)
            
            if sub.startswith("play/ffmpeg/"):
                encoded_path = sub.split("play/ffmpeg/")[1]
                return logic.play_ffmpeg_copy(encoded_path)
                
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            return Response(str(e), status=500, mimetype="text/plain")
