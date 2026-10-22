"""CE-07: contract for merging CE into existing encode passes.

This module documents and enforces the integration rules so host code
cannot accidentally add a post-master re-encode for every effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PassKind = Literal[
    "video_encode",
    "audio_only_encode",
    "stream_copy_remux",
    "filter_only_preview",
]


@dataclass
class EncodePass:
    name: str
    kind: PassKind
    applies_when_ce: bool  # True = this pass may carry CE filters
    notes: str = ""


@dataclass
class EncodePolicy:
    """
    Allowed places for CE pixel effects.

    Rule: put crop/zoom/color/transitions into the SAME video_encode that
    already builds the master. Never add a new full-frame encode after
    FinalizeStage BGM (which must stay stream-copy on video).
    """
    passes: list[EncodePass] = field(default_factory=list)

    def allowed_ce_hosts(self) -> list[str]:
        return [p.name for p in self.passes if p.applies_when_ce and p.kind == "video_encode"]

    def forbidden_for_ce(self) -> list[str]:
        return [
            p.name for p in self.passes
            if p.kind == "stream_copy_remux" or not p.applies_when_ce
        ]


# Default policy matching public VideoMaker architecture (CP24+)
DEFAULT_POLICY = EncodePolicy(
    passes=[
        EncodePass("MasterBuilder_H", "video_encode", True, "Primary host for 16:9 CE filters"),
        EncodePass("MasterBuilder_V", "video_encode", True, "Primary host for 9:16 CE filters (separate geometry)"),
        EncodePass("Shorts_burn_text", "video_encode", False, "Text-only encode; inherit CE visuals from V master via stream-copy cut"),
        EncodePass("Shorts_cut", "stream_copy_remux", False, "Must remain stream-copy from processed master"),
        EncodePass("Finalize_BGM", "stream_copy_remux", False, "Video stream-copy + audio mix only"),
        EncodePass("voice_enhance", "audio_only_encode", False, "Shared narration before masters; CE must not re-trigger"),
        EncodePass("CE_preview", "filter_only_preview", True, "Offline/dev preview only; not production path"),
    ]
)


def assert_ce_host_allowed(pass_name: str, policy: EncodePolicy | None = None) -> None:
    policy = policy or DEFAULT_POLICY
    allowed = policy.allowed_ce_hosts()
    if pass_name not in allowed and pass_name != "CE_preview":
        raise RuntimeError(
            f"CE filters must not be applied in pass '{pass_name}'. "
            f"Allowed video_encode hosts: {allowed}"
        )


def describe_policy(policy: EncodePolicy | None = None) -> str:
    policy = policy or DEFAULT_POLICY
    lines = ["Encode policy for Cinematic Enhancement:", ""]
    for p in policy.passes:
        flag = "CE-OK" if p.applies_when_ce else "no-CE"
        lines.append(f"  [{flag}] {p.name:24} {p.kind:22} {p.notes}")
    lines.append("")
    lines.append("OFF path: skip all CE filter injection; zero new encodes.")
    return "\n".join(lines)
