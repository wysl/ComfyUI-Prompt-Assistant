"""User-managed multimedia reference prompt library node."""

from __future__ import annotations

from comfy_api.latest import io

from .base.base_node import BaseNode
from .io_types import ReferencePromptContent
from ..utils.reference_prompt_library import (
    compose_reference_prompts,
    selection_content_digest,
)


class MultimediaReferencePromptLibraryNode(BaseNode, io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MultimediaReferencePromptLibraryNode",
            display_name="✨Multimedia Reference Prompt Library",
            category="✨Prompt Assistant",
            description="Select and combine user-managed TXT reference prompts",
            inputs=[
                io.String.Input(
                    "selected_files",
                    multiline=True,
                    default="[]",
                    tooltip="Ordered relative paths selected from the reference prompt library",
                ),
            ],
            outputs=[
                ReferencePromptContent.Output("reference_content"),
                io.String.Output("selected_manifest"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, selected_files=None):
        return selection_content_digest(selected_files)

    @classmethod
    def execute(cls, selected_files=None):
        reference_content, selected_manifest = compose_reference_prompts(selected_files)
        return io.NodeOutput(reference_content, selected_manifest)
