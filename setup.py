import os
import traceback
from plugin import * # noqa

setting = {
    "filepath": __file__,
    "use_db": True,
    "use_default_setting": True,
    "home_module": None,
    "menu": {
        "uri": __package__,
        "name": "로컬 미디어 M3U",
        "list": [
            {"uri": "main/setting", "name": "설정"},
            {"uri": "log", "name": "로그"},
        ],
    },
    "setting_menu": None,
    "default_route": "normal",
}

P = create_plugin_instance(setting)

try:
    if os.path.exists(os.path.join(os.path.dirname(__file__), "mod_main.py")):
        from .mod_main import ModuleMain
    else:
        from support import SupportSC
        ModuleMain = SupportSC.load_module_P(P, "mod_main").ModuleMain

    P.set_module_list([ModuleMain])
except Exception as e:
    P.logger.error(f"Exception:{str(e)}")
    P.logger.error(traceback.format_exc())
