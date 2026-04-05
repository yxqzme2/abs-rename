"""
app/api/routes/templates.py
-----------------------------
CRUD routes for naming templates + live preview.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.queries.templates import (
    list_templates,
    save_template,
    set_default_template,
)
from app.path_engine.template_engine import render_example, PREDEFINED_TEMPLATES

router = APIRouter()


@router.get("/templates")
async def get_templates():
    """List all saved templates plus the built-in predefined list."""
    saved = await list_templates()
    return {
        "predefined": PREDEFINED_TEMPLATES,
        "saved": saved,
    }


class SaveTemplateRequest(BaseModel):
    name:            str
    template_string: str
    set_default:     bool = False


@router.post("/templates")
async def create_template(request: SaveTemplateRequest):
    """Save a new custom template."""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Template name is required.")
    if not request.template_string.strip():
        raise HTTPException(status_code=400, detail="Template string is required.")

    row = await save_template(
        name=request.name.strip(),
        template_string=request.template_string.strip(),
        set_default=request.set_default,
    )
    return row


@router.post("/templates/{template_id}/default")
async def make_default(template_id: int):
    """Set a template as the default."""
    await set_default_template(template_id)
    return {"ok": True, "default_template_id": template_id}


@router.get("/templates/preview")
async def preview_template(template: str):
    """
    Render a template with sample data and return the example path.
    Query param: ?template={template_string}
    """
    if not template.strip():
        raise HTTPException(status_code=400, detail="Template string is required.")
    try:
        example = render_example(template)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Template render error: {exc}")
    return {"template": template, "example": example}
