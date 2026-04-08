from services.mode_controller import ModeController
import services.mode_controller as mc

print("ModeController loaded from:", mc.__file__)
print("Has _build_live_service_locked:", hasattr(ModeController, "_build_live_service_locked"))

mode_controller = ModeController()