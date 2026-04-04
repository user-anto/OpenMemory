from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class ModelCommand(BaseModel):
    id: str
    label: str
    command: str
    args: list[str] = Field(default_factory=list)
    model_name: str
    provider: str
    timeout_seconds: int = 120


class LinkInfo(BaseModel):
    model_id: str
    linked_at: str


class GuiConfig(BaseModel):
    memory_store_path: str = str((Path.cwd() / "llm-memory").resolve())
    model_commands: dict[str, ModelCommand] = Field(default_factory=dict)
    session_links: dict[str, LinkInfo] = Field(default_factory=dict)


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (Path.home() / ".llm-memory-gui" / "config.json")

    def _default(self) -> GuiConfig:
        return GuiConfig(
            model_commands={
                "gemini-cli": ModelCommand(
                    id="gemini-cli",
                    label="Gemini CLI",
                    command="gemini",
                    args=["-m", "gemini-2.5-flash", "-p", "{prompt}"],
                    model_name="gemini-2.5-flash",
                    provider="google",
                ),
            }
        )

    def load(self) -> GuiConfig:
        if not self.path.exists():
            cfg = self._default()
            self.save(cfg)
            return cfg
        data = json.loads(self.path.read_text())
        cfg = GuiConfig.model_validate(data)

        changed = False

        # Remove deprecated Claude CLI adapter from prior configs.
        if "claude-cli" in cfg.model_commands:
            cfg.model_commands.pop("claude-cli", None)
            changed = True

        # Ensure defaults exist for known model adapters.
        defaults = self._default()
        for model_id, model_cfg in defaults.model_commands.items():
            if model_id not in cfg.model_commands:
                cfg.model_commands[model_id] = model_cfg
                changed = True

        # Enforce Gemini CLI baseline for MVP consistency.
        gemini_default = defaults.model_commands["gemini-cli"]
        gemini_cfg = cfg.model_commands.get("gemini-cli")
        if gemini_cfg and (
            gemini_cfg.command != gemini_default.command
            or gemini_cfg.args != gemini_default.args
            or gemini_cfg.model_name != gemini_default.model_name
            or gemini_cfg.provider != gemini_default.provider
            or gemini_cfg.label != gemini_default.label
        ):
            cfg.model_commands["gemini-cli"] = gemini_default.model_copy(
                update={"timeout_seconds": gemini_cfg.timeout_seconds}
            )
            changed = True

        if changed:
            self.save(cfg)

        return cfg

    def save(self, cfg: GuiConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = cfg.model_dump(mode="json")
        self.path.write_text(json.dumps(payload, indent=2))
