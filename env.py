from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=".env", env_file_encoding="utf-8"
    )
    bot_token: str = ""
    guild_id: int = 0
    admin_group: str = "equipo-docente"
    student_group: str = "estudiantes"
    notion_token: str = ""
    notion_members_datasource: str = ""
    notion_voicestate_events_datasource: str = ""
    notion_attendance_datasource: str = ""


app = Settings()
