from .config import load

settings = load()
print(f"shopapi starting on :{settings.port} ({settings.app_env})")
