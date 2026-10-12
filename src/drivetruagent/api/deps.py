"""Dependencies shared by the API routes."""

from typing import Annotated

from fastapi import Depends

from drivetruagent.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]
